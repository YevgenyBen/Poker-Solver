"""Expected-value loss: what following one strategy costs against another.

Every accuracy number in this project is a FREQUENCY distance — how far
the shipped strategy's action mix sits from a fuller solve's. That is a
useful convergence measure and a poor measure of advice quality, for a
reason worth stating plainly: **a frequency gap costs nothing when the
actions it is split between are close in value.** Solvers mix precisely
when actions are near-indifferent, so mixed frequencies are exactly where
a large frequency error is cheapest. The same 0.10 on a clearly dominated
action costs real chips.

That is not hypothetical here. A big-blind fold rate measured 3x away
from a reference and cost **0.0021 bb/hand** when priced — a "defect"
that was withdrawn rather than fixed. And `Kc8c` on 7h9hKd is alarming
not because its frequency error is 0.95 but because value-betting top
pair is clearly better than checking.

This module prices the difference:

    loss = EV(reference row) - EV(shipped row)

with **everything else held identical** — same opponent strategy, same
continuation strategy below the node, same range. Only hero's mix at the
one decision changes, which is what isolates the decision rather than the
whole solve.

Two properties worth knowing:

* **Hero's TRUE payoff is used, not the solver's convention.** `cfr.py`
  values the second position as minus the first's, which offsets it by
  the dead pot (F45). That offset is constant across hero's actions at a
  node, so it cancels from the loss either way — but computing the real
  payoff makes the absolute EVs interpretable as chips.
* **Loss is signed and can be negative.** The shipped row occasionally
  prices better than the reference against this particular opponent
  model; reporting that honestly is the point, since a metric that can
  only find fault is not measuring.
"""
from __future__ import annotations

import numpy as np

from .game_tree import TerminalNode

__all__ = ["action_values", "strategy_ev", "ev_loss"]


def _hero_equity_column(equity_table: np.ndarray, hero_index: int, hero_is_a: bool) -> np.ndarray:
    """Hero's equity against every opponent hand, as a vector over THEIR hands.

    `equity_table[a_hand, b_hand]` is position a's equity, so position b's
    is one minus the transposed entry. Getting this backwards silently
    prices hero's hand as the opponent's.
    """
    if hero_is_a:
        return equity_table[hero_index, :]
    return 1.0 - equity_table[:, hero_index]


def _terminal_ev(node: TerminalNode, hero_position: str, hero_index: int,
                 hero_is_a: bool, equity_table: np.ndarray,
                 opp_reach: np.ndarray) -> float:
    """Hero's own expected chips at a leaf, weighted by the opponent's reach."""
    opp_mass = float(opp_reach.sum())
    if opp_mass <= 0.0:
        return 0.0
    invested_hero = node.invested[hero_position]
    opponent = next(p for p in node.invested if p != hero_position)

    if hero_position in node.folded:
        return -invested_hero * opp_mass
    if opponent in node.folded:
        return (node.pot - invested_hero) * opp_mass

    equity = _hero_equity_column(equity_table, hero_index, hero_is_a)
    return float(equity @ opp_reach) * node.pot - invested_hero * opp_mass


def _value(node, *, hero_position: str, hero_index: int, hero_is_a: bool,
           equity_table: np.ndarray, opp_reach: np.ndarray,
           strategy_fn, hero_override, override_node) -> float:
    """Hero's expected chips from `node` down, both players following
    `strategy_fn` (except hero's mix at `override_node`, if given)."""
    if isinstance(node, TerminalNode):
        return _terminal_ev(node, hero_position, hero_index, hero_is_a,
                            equity_table, opp_reach)

    actions = node.legal_actions
    if not actions:
        return 0.0
    table = strategy_fn(node)

    if node.player_to_act == hero_position:
        row = (hero_override if (override_node is not None and node is override_node
                                 and hero_override is not None)
               else (None if table is None else table[hero_index]))
        if row is None:
            row = np.full(len(actions), 1.0 / len(actions))
        total = 0.0
        for i, action in enumerate(actions):
            p = float(row[i])
            if p <= 0.0:
                continue
            total += p * _value(
                node.children[action], hero_position=hero_position,
                hero_index=hero_index, hero_is_a=hero_is_a,
                equity_table=equity_table, opp_reach=opp_reach,
                strategy_fn=strategy_fn, hero_override=hero_override,
                override_node=override_node)
        return total

    # The opponent acts: their action probability is per-HAND, so it
    # multiplies their reach rather than weighting the branch — the same
    # step that makes the vector CFR recursion correct (M161).
    total = 0.0
    for i, action in enumerate(actions):
        if table is None:
            child_reach = opp_reach / len(actions)
        else:
            child_reach = opp_reach * table[:, i]
        if float(child_reach.sum()) <= 0.0:
            continue
        total += _value(
            node.children[action], hero_position=hero_position,
            hero_index=hero_index, hero_is_a=hero_is_a,
            equity_table=equity_table, opp_reach=child_reach,
            strategy_fn=strategy_fn, hero_override=hero_override,
            override_node=override_node)
    return total


def action_values(node, *, hero_position: str, hero_index: int, hero_is_a: bool,
                  equity_table: np.ndarray, opp_reach: np.ndarray,
                  strategy_fn) -> dict:
    """{action: hero's expected chips from taking it} at `node`.

    Normalised by the opponent's total reach, so the numbers are chips per
    hand rather than chips times an arbitrary range mass.
    """
    mass = float(opp_reach.sum())
    if mass <= 0.0:
        return {}
    out = {}
    for action in node.legal_actions:
        out[action] = _value(
            node.children[action], hero_position=hero_position,
            hero_index=hero_index, hero_is_a=hero_is_a,
            equity_table=equity_table, opp_reach=opp_reach,
            strategy_fn=strategy_fn, hero_override=None,
            override_node=None) / mass
    return out


def strategy_ev(row, values: dict, actions) -> float:
    """Expected chips from playing `row` over `actions`, given action values."""
    total = 0.0
    for i, action in enumerate(actions):
        p = float(row[i])
        if p:
            total += p * values[action]
    return total


def ev_loss(shipped_row, reference_row, values: dict, actions) -> dict:
    """What following `shipped_row` costs against `reference_row`.

    Returns the two EVs, the signed loss in chips (big blinds, since the
    tree is denominated in them), and the value spread at the node —
    `best - worst` — which is the honest scale for reading the loss. A
    node whose actions are all worth the same cannot produce a costly
    error however wrong the frequencies look, and that is the distinction
    frequency distance cannot make.
    """
    ev_shipped = strategy_ev(shipped_row, values, actions)
    ev_reference = strategy_ev(reference_row, values, actions)
    ordered = [values[a] for a in actions]
    return {
        "ev_shipped_bb": ev_shipped,
        "ev_reference_bb": ev_reference,
        "loss_bb": ev_reference - ev_shipped,
        "value_spread_bb": (max(ordered) - min(ordered)) if ordered else 0.0,
        "best_action": max(values, key=values.get) if values else None,
    }
