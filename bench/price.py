"""Price one real decision in big blinds against a fuller solve of it.

Every chips-priced figure this project carries - M183's 4.7 bb per 100
postflop decisions, M188's facing-a-bet split, M189's costly band - was
produced by a harness that lived in a scratchpad and is gone. The figures
outlived their instrument, and outlived the configuration they were taken
at: M188 and M189 both predate M203-M213's bet menu, so every one of
their numbers describes an engine whose smallest modellable bet was 2.5x
the pot (M209 priced that capability gap at up to 1.74 bb on a single
facing-a-bet decision). Re-measuring them needs the instrument back.

**What it does.** Given an `/advise` request and its shipped answer, it
builds a REFERENCE solve of the same request - the same production
derivation, uncapped ranges, more equity samples, more iterations - and
prices hero's shipped row against the reference's row at the same node,
with `poker_solver/ev.py`:

    loss = EV(reference row) - EV(shipped row)

with the opponent's strategy, the continuation below the node and the
ranges all held fixed. Only hero's mix at the one decision moves, which
is what isolates the decision from the solve (`ev.py`'s own contract).

**It is a LOWER BOUND, as every internal figure here is.** Both arms
share the model - the bet menu, the street isolation, the range
derivation - so an error they share cancels and cannot appear in the
price. M202 measured that shared error at ~0.1 bb a decision on the flop.
An outside reference is what M260/M296 use; this is the cheaper
instrument that can run over hundreds of real spots.

**The rules it exists to obey**, each learned the hard way here:

* **M180**: an arm must use the spot's own pot AND stack, and both come
  from the product's own answer, never from a constant.
* **M177**: a facing-a-bet reference starts from the street's OPENING
  pot, not the node's - a tree built at the post-bet pot sizes its bets
  off it and models a much larger bet. So the entry pot and entry stack
  are read from a second `/advise` call at the street's first decision,
  which is the same production code that computed them.
* **M164**: the ranges come from `_derive_path_situation`, the
  derivation production actually runs, not a reconstruction of it.
* **M138**: a reference that has not converged is not a reference. Every
  row carries the reference's own exploitability, measured with
  `poker_solver/exploitability.py` (M211: the best-response walk costs
  ~0s beside the solve), so a study can refuse a row whose reference is
  loose instead of trusting it.
* **M202**: the two arms must offer the same actions, or the comparison
  is between two games. Every row carries `remapped_mass`, the shipped
  probability sitting on labels the reference node does not have.

**Hero's own cards are removed from the opponent's reach.** A villain
combo sharing a card with hero cannot be held, and M256 measured what
leaving them in does to a realisation figure (8.83% -> 5.31%). It
matters less for a LOSS, where a common contamination largely cancels,
which is exactly why it would have gone unnoticed.
"""
from __future__ import annotations

import dataclasses

import numpy as np

from poker_solver.cards import parse_cards
from poker_solver.combos import HandCombo
from poker_solver.ev import action_values, ev_loss
from poker_solver.exploitability import exploitability

__all__ = ["PricedRow", "street_board", "street_tree_config", "reach_to",
           "strategy_fn_for", "opponent_weights", "price_request"]

#: The reference arm. Uncapped ranges (all 169 classes), M292's sample
#: and iteration counts - the settings an uncapped flop solve was
#: measured affordable at (13.3s there), and the same arm M292 used, so
#: two studies re-measuring two disclosures share one reference
#: definition rather than each inventing their own.
REFERENCE_CLASSES = 169
REFERENCE_SAMPLES = 200
REFERENCE_ITERATIONS = 2500


@dataclasses.dataclass
class PricedRow:
    """One decision, priced. `loss_bb` is positive when shipped is worse."""
    loss_bb: float
    value_spread_bb: float
    ev_shipped_bb: float
    ev_reference_bb: float
    remapped_mass: float
    reference_exploitability_pct: float
    #: Seconds per stage - derive / solve / price / control. A study that
    #: cannot see which stage it is waiting on cannot be sized to a
    #: machine, and the first run of M299 spent fifteen minutes inside
    #: one of them with no way to tell which.
    stage_seconds: dict
    actions: list
    shipped_row: list
    reference_row: list


def street_board(body: dict, street: str) -> tuple:
    """The cards the street's own solve is built on.

    A street is solved on its OWN complete board (M173 turn, M174 river),
    so the turn card and river card are part of it - reading only
    `board` would value the hand on the flop and still answer.
    """
    cards = body["board"]
    if street in ("turn", "river"):
        cards += body["turn_card"]
    if street == "river":
        cards += body["river_card"]
    return tuple(parse_cards(cards))


def street_tree_config(cfg, street: str) -> tuple:
    """`(raise_sizes, max_raises)` for the street's shipped tree.

    Read off the config at call time, never copied: a study that pinned
    these would keep measuring the tree that shipped when it was written,
    which is the whole defect this instrument exists to re-measure.
    """
    if street == "flop":
        return cfg.FLOP_RAISE_SIZES, cfg.FLOP_MAX_RAISES
    if street == "turn":
        return cfg.TURN_STANDALONE_RAISE_SIZES, cfg.TURN_STANDALONE_MAX_RAISES
    if street == "river":
        return cfg.RIVER_STANDALONE_RAISE_SIZES, cfg.RIVER_STANDALONE_MAX_RAISES
    raise ValueError(f"{street!r} is not a postflop street")


def strategy_fn_for(result):
    """`node -> (hands x actions)` average strategy, or None if unvisited.

    `ev.py` treats None as the uniform prior, which is what an unvisited
    node's table already averages to - so this must return None rather
    than a zero table, or an untrained node would price as "never takes
    any action" instead of "has no preference".
    """
    def fn(node):
        table = result.node_data.get(id(node))
        return None if table is None else table.average_strategy()
    return fn


def opponent_weights(position_ranges: dict, position: str, hands: list,
                     hero_combo=None) -> np.ndarray:
    """The opponent's range as a vector over `hands`, hero's cards removed.

    Pure, and the blocker removal is the reason it exists: a combo
    sharing a card with hero cannot be in their range, and nothing
    upstream knows hero's cards when the ranges are derived.
    """
    weights = position_ranges[position]
    blocked = frozenset() if hero_combo is None else frozenset(hero_combo.cards)
    return np.array([0.0 if (blocked and hand.blocks(blocked)) else float(weights.get(hand, 0.0))
                     for hand in hands])


def reach_to(root, actions, *, opp_position: str, opp_reach: np.ndarray, strategy_fn):
    """Walk `actions` from `root`, multiplying the opponent's reach.

    Returns `(node, reach)`. The opponent's action probability is
    per-HAND, so it multiplies their reach rather than scaling the
    branch - the same step that makes the vector CFR recursion correct
    (M161) and `ev.py`'s own opponent branch.

    Hero's own actions along the path do NOT touch the opponent's reach:
    conditioning on hero having taken them says nothing about which hand
    the opponent holds.
    """
    node, reach = root, opp_reach
    for action in actions:
        if node.player_to_act == opp_position:
            table = strategy_fn(node)
            index = list(node.legal_actions).index(action)
            if table is None:
                reach = reach / len(node.legal_actions)
            else:
                reach = reach * table[:, index]
        node = node.children[action]
    return node, reach


def _row_over(actions, strategy: dict) -> tuple:
    """`strategy` as a vector over `actions`, plus the mass it lost.

    The mass on labels the node does not offer is REPORTED, not
    redistributed: a comparison between two different action menus is
    two games being scored against each other (M202/M241), and a study
    has to be able to refuse the row.
    """
    labels = [str(a) for a in actions]
    row = np.array([float(strategy.get(label, 0.0)) for label in labels])
    return row, float(sum(v for k, v in strategy.items() if k not in labels))


def price_request(*, body: dict, street: str, shipped_strategy: dict,
                  entry_pot: float, entry_stack: float, solving, cfg,
                  classes: int = REFERENCE_CLASSES,
                  samples: int = REFERENCE_SAMPLES,
                  iterations: int = REFERENCE_ITERATIONS) -> PricedRow:
    """Price `shipped_strategy` against a fuller solve of the same request.

    `solving` is `api.solving` - passed in rather than imported, so the
    caller owns when the app is configured and this module stays an
    instrument rather than a second entry point into the API.

    `entry_pot` / `entry_stack` are the STREET's, not the node's (M177);
    the caller reads them off `/advise` at the street's first decision.
    """
    from poker_solver.solver import DEFAULT_ITERATIONS, solve_flop
    from api.parallel import parallel_board_equity_table

    import time
    stage = {}
    started = time.time()
    board = street_board(body, street)
    hero_combo = HandCombo(*parse_cards(body["hero_cards"]))
    situation = solving._derive_path_situation(
        action_kinds=list(body["preflop_action_path"]),
        stack_bb=body["stack_bb"],
        board_cards=tuple(parse_cards(body["board"])),
        iterations=DEFAULT_ITERATIONS,
        players=body.get("players", 2),
        multiway=False,
        sibling_endpoint="/advise",
        max_classes_per_position=classes,
        path_field_name="preflop_action_path",
        hero_combo=hero_combo,
    )
    stage["derive"] = time.time() - started
    started = time.time()
    oop_position, ip_position = situation.postflop_positions
    raise_sizes, max_raises = street_tree_config(cfg, street)

    captured = {}

    def capture(board_cards, combos, equity_samples, seed=None):
        # `solve_flop` converts the table it is handed exactly this way
        # before using it; doing it here too is a no-op that hands the
        # caller the very table the solve priced with (M165's rule -
        # valuing a hand on a different board still answers).
        table = parallel_board_equity_table(board_cards, combos, equity_samples, seed=seed)
        captured["table"] = np.nan_to_num(table, nan=0.5).astype(np.float32)
        return captured["table"]

    result = solve_flop(
        board=board,
        hero_range=situation.position_ranges[oop_position],
        villain_range=situation.position_ranges[ip_position],
        pot=entry_pot,
        effective_stack_bb=entry_stack,
        positions=(oop_position, ip_position),
        raise_sizes=raise_sizes,
        max_raises=max_raises,
        iterations=iterations,
        equity_samples=samples,
        equity_table_fn=capture,
    )
    stage["solve"] = time.time() - started
    started = time.time()
    table = captured["table"]
    hands = list(result.hands)
    strategy_fn = strategy_fn_for(result)

    kinds = list(body.get(street + "_action_path") or [])
    actions_taken, _ = solving._resolve_action_path(result.root, kinds) if kinds else ([], result.root)
    hero_position = None
    node = result.root
    for action in actions_taken:
        node = node.children[action]
    hero_position = node.player_to_act
    opp_position = ip_position if hero_position == oop_position else oop_position

    node, opp_reach = reach_to(
        result.root, actions_taken, opp_position=opp_position,
        opp_reach=opponent_weights(situation.position_ranges, opp_position, hands, hero_combo),
        strategy_fn=strategy_fn)

    hero_index = hands.index(hero_combo)
    values = action_values(
        node, hero_position=hero_position, hero_index=hero_index,
        hero_is_a=(hero_position == oop_position), equity_table=table,
        opp_reach=opp_reach, strategy_fn=strategy_fn)
    if not values:
        raise ValueError("the opponent cannot reach this node with any hand")

    actions = list(node.legal_actions)
    shipped_row, remapped = _row_over(actions, shipped_strategy)
    reference_table = strategy_fn(node)
    reference_row = (np.full(len(actions), 1.0 / len(actions)) if reference_table is None
                     else reference_table[hero_index])
    priced = ev_loss(shipped_row, reference_row, values, actions)
    stage["price"] = time.time() - started
    started = time.time()

    control = exploitability(
        result.root, positions=(oop_position, ip_position), equity_table=table,
        reach_a=opponent_weights(situation.position_ranges, oop_position, hands),
        reach_b=opponent_weights(situation.position_ranges, ip_position, hands),
        strategy_fn=strategy_fn)

    stage["control"] = time.time() - started
    return PricedRow(
        loss_bb=priced["loss_bb"],
        value_spread_bb=priced["value_spread_bb"],
        ev_shipped_bb=priced["ev_shipped_bb"],
        ev_reference_bb=priced["ev_reference_bb"],
        remapped_mass=remapped,
        reference_exploitability_pct=float(control["exploitability_pct_of_pot"]),
        stage_seconds=stage,
        actions=[str(a) for a in actions],
        shipped_row=[float(v) for v in shipped_row],
        reference_row=[float(v) for v in reference_row],
    )
