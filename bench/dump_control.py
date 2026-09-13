"""The control a dump study cannot do without: check it from OUTSIDE.

**Every control M256 built was internal, and internal consistency is
exactly what a wrong input preserves.** Exact pairwise conservation -
`EV(h|v) + EV(v|h) == pot` - took that walker's error from 0.80% to
0.00% and then passed at 0.00% while the opponent's range was flat,
because conservation holds for ANY reach weights so long as both sides
use the same ones (F59, M257). M219's dead guard in another shape: an
assertion that cannot distinguish the two things it compares.

This module supplies the missing kind of check. The solver reports its
own exploitability for the strategy it dumped, and that figure is not
derived from our walk, so it can referee it.

**The bound that makes it work.** At an epsilon-equilibrium no
single-node deviation can gain more than epsilon. The reference's own
per-hand regret at a node IS such a deviation - strictly weaker than the
full best response exploitability measures - so its range-weighted mean
must come in **at or below** the reported figure. A walk that returns
more than that is wrong, whatever else it agrees with.

Measured on a converged spot (reported 0.256% of pot, solver target
0.5%): **0.711 bb = 2.155% of pot, a ratio of 8.4x**. The walk
overstated, and the leading suspect is the leaf - a two-round dump
carries no river, so a turn leaf gets valued at `equity * pot` while the
strategy being scored was optimised against river play the solver did
run and merely did not serialise.

Run this before believing any figure off a dump.
"""
from __future__ import annotations

import numpy as np

from bench.dump_ev import class_label
from bench.solver_dump import strategy_at

#: A walk whose weighted mean slack exceeds the reported exploitability
#: by more than this is overstating: a root-only deviation cannot beat a
#: full best response. The allowance covers the definitional slop - the
#: solver's figure may cover both players where this covers one.
MAX_PLAUSIBLE_RATIO = 4.0


class ControlFailed(AssertionError):
    """The walk does not reproduce the solver's own figure."""


def range_weighted_slack(node, walk_for, board, weights, hero_keys,
                         sample=25):
    """Range-weighted mean of the REFERENCE's own per-hand slack.

    `walk_for(hero_key)` returns a `(Walk, reach)` pair for that hand -
    the caller owns leaf construction, which is where the equity tables
    live. Hands outside the range, and hands the board blocks, are
    skipped rather than counted at zero: a hand that cannot be held is
    not a hand the solver got wrong.
    """
    dead = {str(c) for c in board}
    slacks, masses = [], []
    keys = list(hero_keys)
    for hero_key in keys[::max(1, len(keys) // sample)]:
        mass = weights.get(class_label(hero_key), 0.0)
        if mass <= 0 or {hero_key[0:2], hero_key[2:4]} & dead:
            continue
        row = strategy_at(node).get(hero_key)
        if not row:
            continue
        walk, reach = walk_for(hero_key)
        got = walk.regret_of_row(node, board, reach, row)
        if got is None or got[1] is None:
            continue
        slacks.append(got[1])
        masses.append(mass)
    if not slacks:
        return None
    slacks, masses = np.array(slacks), np.array(masses)
    return {"hands": len(slacks),
            "weighted_mean_slack_bb": float(np.average(slacks,
                                                       weights=masses)),
            "median_slack_bb": float(np.median(slacks)),
            "max_slack_bb": float(slacks.max()),
            "negative_slacks": int((slacks < -1e-9).sum())}


def check(summary, pot, reported_exploitability_pct,
          max_ratio=MAX_PLAUSIBLE_RATIO):
    """`(ok, detail)` - does the walk reproduce the solver's own figure?

    Never raises on its own: a study decides whether to stop. What it
    must not do is publish a number without having run this.
    """
    ours_pct = 100.0 * summary["weighted_mean_slack_bb"] / pot
    ratio = (ours_pct / reported_exploitability_pct
             if reported_exploitability_pct else float("inf"))
    detail = dict(summary)
    detail.update({"ours_pct_of_pot": ours_pct,
                   "reported_exploitability_pct": reported_exploitability_pct,
                   "ratio": ratio, "max_ratio": max_ratio})
    return ratio <= max_ratio, detail


def require(summary, pot, reported_exploitability_pct,
            max_ratio=MAX_PLAUSIBLE_RATIO):
    """`check`, but refuses to continue - for a study that would
    otherwise publish."""
    ok, detail = check(summary, pot, reported_exploitability_pct, max_ratio)
    if not ok:
        raise ControlFailed(
            "the walk reports %.3f%% of pot where the solver reports %.3f%% "
            "(ratio %.1fx, limit %.1fx). A root-only deviation cannot beat a "
            "full best response, so the walk is overstating and no figure "
            "from it means anything."
            % (detail["ours_pct_of_pot"],
               detail["reported_exploitability_pct"],
               detail["ratio"], max_ratio))
    return detail
