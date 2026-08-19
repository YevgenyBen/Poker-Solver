"""Public entrypoint: solve a heads-up or multiway preflop spot end to end.

Wires together game_tree (the betting tree), equity (showdown values),
and cfr (the solving loop) behind one function, `solve_preflop`, and
formats the result into simple hand -> action -> frequency dictionaries.

Dispatches internally between cfr.py's two solving paths based on player
count: the exact, exhaustive `solve()` for heads-up (kept as a fast path,
unchanged since M4), and `mccfr_solve()` for 3+ players (exhaustive CFR
doesn't scale past 2 players — see cfr.py's module docstring and the
project plan). Both return the same {id(node): InfoSetTable} shape, so
StrategyResult doesn't need to know which path produced it.
"""

import random
import time
from dataclasses import dataclass, field

import numpy as np

from .board_equity import DEFAULT_SEED as DEFAULT_EQUITY_SEED
from .board_equity import build_board_equity_table
from .chance import build_chance_node
from .cfr import InfoSetTable, mccfr_solve, solve
from .equity import MultiwayEquityCache, get_equity_table
from .game_tree import CALL_OR_CHECK, DecisionNode, GameConfig, StreetConfig, build_game_tree, build_street_tree
from .starting_hands import all_starting_hands

# Exact path (heads-up): measured ~3-4ms/iteration at full 169-hand
# scale (M4) — 1000 iterations lands comfortably in low-single-digit
# seconds.
DEFAULT_ITERATIONS = 1000

# MCCFR path (3+ players): needs far more iterations than the exact
# path to control sampling variance — see the M8 benchmarks/PR for the
# concrete numbers this was tuned against.
DEFAULT_MCCFR_ITERATIONS = 50_000


@dataclass
class StrategyResult:
    """The outcome of solving one GameConfig (preflop) or StreetConfig
    (a single postflop street, e.g. M11's flop-only tree) — this class
    only ever reads `config.positions`, which both configs provide, so
    one result type serves either without needing to know which kind of
    config actually produced it."""

    config: object  # GameConfig | StreetConfig
    root: DecisionNode
    hands: list
    node_data: dict
    iterations: int
    elapsed_seconds: float
    # M12: {id(flop showdown terminal): chance.ChanceNode} — empty for
    # every pre-M12 result (solve_preflop/solve_flop never touch it),
    # populated only by solve_flop_turn. Lets a caller walk into a
    # specific next-card branch's subtree, e.g.
    # `result.chance_data[id(some_terminal)].branches[some_card].root`.
    chance_data: dict = field(default_factory=dict)

    def strategy_at(self, node: DecisionNode) -> dict:
        """hand label -> {action label -> frequency} at any node in the
        tree this result was solved for.

        Falls back to the uniform strategy if `node` has no entry in
        `node_data` at all — MCCFR (unlike the exact HU solver) only
        visits nodes actually reached along a sampled/traversed path, so
        a node reachable only via a low-probability combination of
        earlier actions (e.g. "every one of 8 earlier players limps" at
        a 9-max table with a modest iteration budget) can genuinely go
        unvisited. This mirrors InfoSetTable.average_strategy()'s own
        fallback for a *visited* node with no accumulated strategy_sum
        yet, so an unvisited node behaves the same as a visited-but-
        untrained one — consistent, not a special case.
        """
        actions = node.legal_actions
        table = self.node_data.get(id(node))
        if table is None:
            table = InfoSetTable.zeros(len(self.hands), len(actions))
        avg = table.average_strategy()
        return {
            str(hand): {str(action): float(avg[hand_idx, a_idx]) for a_idx, action in enumerate(actions)}
            for hand_idx, hand in enumerate(self.hands)
        }

    def opening_range(self) -> dict:
        """The first-to-act position's strategy at their very first
        decision — preflop, that's BTN's RFI spot; for a flop-only
        result (M11), it's whichever position acts first on that
        street's `positions` (OOP, by convention)."""
        return self.strategy_at(self.root)

    def node_for_position(self, position: str) -> DecisionNode:
        """The decision node for `position`'s first turn to act, reached
        by everyone before them calling/checking (i.e. "the action folds
        around to you with no raise yet") — the natural generalization of
        `opening_range()`'s "BTN's very first decision" to a position that
        isn't first to act. Every position gets exactly one such node,
        found by walking call_or_check from the root: the first player to
        act is the root itself, and each subsequent position's turn
        arrives right after everyone before them has called/checked.
        """
        if position not in self.config.positions:
            raise ValueError(f"{position!r} is not one of {self.config.positions}")
        node = self.root
        while node.player_to_act != position:
            call_action = next(a for a in node.legal_actions if a.kind == CALL_OR_CHECK)
            node = node.children[call_action]
        return node

    def strategy_for_position(self, position: str) -> dict:
        """`position`'s strategy (hand -> action -> frequency) the first
        time it's their turn, assuming no raise has happened yet — see
        node_for_position. For the first-to-act position this is exactly
        opening_range()."""
        return self.strategy_at(self.node_for_position(position))


def solve_preflop(
    stack_bb: float = 100.0,
    iterations: int = None,
    config: GameConfig = None,
    hands: list = None,
    equity_table=None,
    equity_cache: MultiwayEquityCache = None,
    seed: int = 0,
) -> StrategyResult:
    """Solve a preflop spot and return its strategy.

    Either pass `stack_bb` for the default blind/sizing/raise-cap
    settings (heads-up), or pass a fully custom `config` directly (e.g.
    with `positions=("BTN", "SB", "BB")` for 3-max).

    `hands` defaults to the full 169-class canonical set — override for
    smaller/faster test solves. `equity_table` (heads-up only) and
    `equity_cache` (3+ players only) default to the real disk-cached
    table / a fresh lazy cache respectively — override together with
    `hands` for fast, self-contained testing.

    `iterations` defaults to DEFAULT_ITERATIONS for heads-up or
    DEFAULT_MCCFR_ITERATIONS for 3+ players — the two paths need very
    different scales, so leave it unset unless you have a specific
    reason to override.
    """
    config = config if config is not None else GameConfig(stack_bb=stack_bb)
    root = build_game_tree(config)
    hands = hands if hands is not None else all_starting_hands()

    start = time.perf_counter()
    if config.num_players == 2:
        actual_iterations = iterations if iterations is not None else DEFAULT_ITERATIONS
        equity_table = equity_table if equity_table is not None else get_equity_table()
        node_data = solve(root, hands, equity_table, iterations=actual_iterations)
    else:
        actual_iterations = iterations if iterations is not None else DEFAULT_MCCFR_ITERATIONS
        equity_cache = equity_cache if equity_cache is not None else MultiwayEquityCache(hands=hands)
        node_data = mccfr_solve(
            root, hands, config.positions, equity_cache, iterations=actual_iterations, seed=seed
        )
    elapsed = time.perf_counter() - start

    return StrategyResult(
        config=config,
        root=root,
        hands=hands,
        node_data=node_data,
        iterations=actual_iterations,
        elapsed_seconds=elapsed,
    )


# Flop tree is heads-up (2 positions), same shape as preflop's exact
# fast path — same default as DEFAULT_ITERATIONS, tuned for M11's
# curated-range demo scale, not the full ~1176-combo case (see
# board_equity.py's module-level comment for the measured reason a wide
# range isn't there yet).
DEFAULT_FLOP_ITERATIONS = 1000


def solve_flop(
    board: tuple,
    hero_range: dict,
    villain_range: dict,
    pot: float,
    effective_stack_bb: float,
    positions: tuple = ("OOP", "IP"),
    raise_sizes: tuple = (2.5, 3.0, 2.2),
    max_raises: int = 4,
    iterations: int = None,
    equity_samples: int = None,
    equity_seed: int = DEFAULT_EQUITY_SEED,
) -> StrategyResult:
    """Solve a single flop betting round and return its strategy.

    `board` is the flop (3 Cards). `hero_range`/`villain_range` map
    combos.HandCombo -> weight — see combos.range_from_class_frequencies
    for the natural way to build one from a preflop solve's per-class
    continue-frequency output. They become `positions`' two combo pools
    *and* their starting reach weights, in `positions` order
    (`hero_range` for `positions[0]`, `villain_range` for `positions[1]`)
    — a combo missing from a range gets 0 weight for that position, not
    an error, exactly like a class the preflop solve folded 100% of the
    time contributes nothing to a range built via
    range_from_class_frequencies.

    `pot`/`effective_stack_bb` describe the state entering the flop —
    see StreetConfig for exactly what each means (`effective_stack_bb`
    is what's left *behind*, not the hand's original starting stack).

    Reuses cfr.solve()'s exact tensor CFR — this is a heads-up (2
    position), single-street tree, the same shape as preflop's exact
    fast path, not a new solving loop (see cfr.py's module docstring for
    the generalizations — custom position labels, custom starting reach
    — that make this direct reuse possible).

    Hero's and villain's ranges are combined into one shared combo pool
    (the union of both, matching cfr.solve()'s single `hands` list/NxN
    equity_table design) — some (hero combo, villain combo) pairs in
    that pool inevitably share a physical card (e.g. hero's AhKh and
    villain's AhQc both need the Ah), which board_equity.
    build_board_equity_table correctly reports as an undefined (NaN)
    matchup. Those NaN entries are replaced with a neutral 0.5 before
    solving — the same "true probability is ~0 anyway, so any neutral
    placeholder is fine, it just must not poison the computation"
    reasoning equity.MultiwayEquityCache already uses for the analogous
    N>=3 preflop case (see its docstring) — cfr.solve()'s reach vectors
    don't account for *cross-position* card removal either (each
    position's range is supplied independently), the same "ignore
    blockers between players' hands" approximation carried since M1,
    just now also visible at combo (not just class) granularity.
    """
    hero_position, villain_position = positions
    combos = sorted(set(hero_range) | set(villain_range), key=str)
    hero_reach = np.array([hero_range.get(combo, 0.0) for combo in combos])
    villain_reach = np.array([villain_range.get(combo, 0.0) for combo in combos])

    config = StreetConfig(
        positions=positions,
        pot=pot,
        stack_bb=effective_stack_bb,
        raise_sizes=raise_sizes,
        max_raises=max_raises,
    )
    root = build_street_tree(config)

    equity_kwargs = {"rng": random.Random(equity_seed)}
    if equity_samples is not None:
        equity_kwargs["samples"] = equity_samples
    equity_table = build_board_equity_table(board, combos, **equity_kwargs)
    equity_table = np.nan_to_num(equity_table, nan=0.5)

    actual_iterations = iterations if iterations is not None else DEFAULT_FLOP_ITERATIONS
    start = time.perf_counter()
    node_data = solve(
        root,
        combos,
        equity_table,
        iterations=actual_iterations,
        positions=positions,
        initial_reach={hero_position: hero_reach, villain_position: villain_reach},
    )
    elapsed = time.perf_counter() - start

    return StrategyResult(
        config=config,
        root=root,
        hands=combos,
        node_data=node_data,
        iterations=actual_iterations,
        elapsed_seconds=elapsed,
    )


# Flop->turn chaining (M12) walks a real exact-CFR tree ~47x wider at
# every showdown-eligible terminal (one branch per undealt card) than
# solve_flop's — see chance.py's module docstring. DEFAULT_FLOP_TURN_ITERATIONS
# is set from real measurement (see the M12 PR), not copied from
# DEFAULT_FLOP_ITERATIONS.
DEFAULT_FLOP_TURN_ITERATIONS = 200


def solve_flop_turn(
    board: tuple,
    hero_range: dict,
    villain_range: dict,
    pot: float,
    effective_stack_bb: float,
    positions: tuple = ("OOP", "IP"),
    raise_sizes: tuple = (2.5, 3.0, 2.2),
    max_raises: int = 4,
    iterations: int = None,
    equity_seed: int = DEFAULT_EQUITY_SEED,
) -> StrategyResult:
    """Solve a flop betting round that, whenever action is capped without
    a fold, chains into a real turn betting round (dealt via a real
    chance node) instead of solve_flop's "average every remaining runout
    immediately" shortcut — the river is still handled that way, one
    street further out, at each *turn* terminal. See chance.py's and
    cfr.py's module docstrings for the full chance-node design.

    Same parameters as `solve_flop`, minus `equity_samples` (board_equity
    tables built here are all turn-board tables — remaining_needed==1 —
    which are resolved exactly, not sampled, so there's nothing to tune).
    `raise_sizes`/`max_raises` apply to both the flop and turn streets —
    a deliberate M12 scope cut, not a separate turn-specific sizing menu.

    The returned StrategyResult's `chance_data` maps each showdown-
    eligible flop terminal actually reached during solving to its
    `chance.ChanceNode` — the only way to reach turn-street strategy,
    since `StrategyResult`'s own convenience walkers
    (`node_for_position`/`strategy_for_position`) only ever walk `root`'s
    own street.
    """
    hero_position, villain_position = positions
    combos = sorted(set(hero_range) | set(villain_range), key=str)
    hero_reach = np.array([hero_range.get(combo, 0.0) for combo in combos])
    villain_reach = np.array([villain_range.get(combo, 0.0) for combo in combos])

    config = StreetConfig(
        positions=positions,
        pot=pot,
        stack_bb=effective_stack_bb,
        raise_sizes=raise_sizes,
        max_raises=max_raises,
    )
    root = build_street_tree(config)

    equity_table = build_board_equity_table(board, combos, rng=random.Random(equity_seed))
    equity_table = np.nan_to_num(equity_table, nan=0.5)

    def chance_fn(terminal):
        return build_chance_node(
            terminal, board=board, combos=combos, positions=positions,
            effective_stack_bb=effective_stack_bb, raise_sizes=raise_sizes, max_raises=max_raises,
        )

    chance_data: dict = {}
    actual_iterations = iterations if iterations is not None else DEFAULT_FLOP_TURN_ITERATIONS
    start = time.perf_counter()
    node_data = solve(
        root,
        combos,
        equity_table,
        iterations=actual_iterations,
        positions=positions,
        initial_reach={hero_position: hero_reach, villain_position: villain_reach},
        chance_fn=chance_fn,
        chance_data=chance_data,
    )
    elapsed = time.perf_counter() - start

    return StrategyResult(
        config=config,
        root=root,
        hands=combos,
        node_data=node_data,
        iterations=actual_iterations,
        elapsed_seconds=elapsed,
        chance_data=chance_data,
    )


def format_opening_range_grid(result: StrategyResult) -> str:
    """A quick 13x13-ish text dump of the opening range, sorted by hand
    strength, for eyeballing sanity (used by the console script)."""
    opening = result.opening_range()
    non_fold_action_by_strength = []
    for hand in result.hands:
        freqs = opening[str(hand)]
        fold_freq = freqs.get("fold", 0.0)
        non_fold_action_by_strength.append((str(hand), 1.0 - fold_freq, freqs))

    lines = [
        f"{result.root.player_to_act} opening range @ {result.config.stack_bb:.0f}bb "
        f"({result.iterations} iterations, {result.elapsed_seconds:.2f}s):",
    ]
    for label, non_fold_freq, freqs in non_fold_action_by_strength:
        breakdown = ", ".join(f"{action}={freq:.2f}" for action, freq in freqs.items())
        lines.append(f"  {label:>4s}  non-fold={non_fold_freq:5.1%}   ({breakdown})")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    stack = float(sys.argv[1]) if len(sys.argv) > 1 else 100.0
    iters = int(sys.argv[2]) if len(sys.argv) > 2 else None
    result = solve_preflop(stack_bb=stack, iterations=iters)
    print(format_opening_range_grid(result))
