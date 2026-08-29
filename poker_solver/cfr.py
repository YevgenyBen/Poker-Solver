"""CFR+ solving over the N-player preflop betting tree.

Two solving paths live here, sharing InfoSetTable:

1. `solve()` — exact, exhaustive CFR+, kept as a deliberate fast path
   for any two-position tree (heads-up preflop, or a flop-only tree —
   see M11). The betting tree (game_tree.py) has no knowledge of hole
   cards — the same tree is shared by every (position_a hand, position_b
   hand) pair. A naive implementation would loop over all 169x169 hand
   pairs and re-walk the tree for each one; for a tree with ~100 nodes
   and thousands of iterations that's on the order of tens of billions
   of Python-level operations, far too slow. Instead this walks the tree
   exactly once per iteration, carrying whole hand-class distributions
   through it as NumPy arrays: `reach_a`/`reach_b` are length-N vectors
   propagated *down* the tree, and node "values" are NxN matrices
   propagated *up* it (value[i, j] = position_a's payoff given they hold
   hand i and position_b holds hand j). This turns each iteration into a
   handful of NxN array ops instead of N*N per-pair recursions. Position
   labels default to `(BTN, BB)` and reach defaults to `combo_weight`
   (preflop's original behavior, unchanged); both are overridable — see
   `solve()`'s own docstring — so a postflop tree can supply its own
   labels (`("OOP", "IP")`) and starting ranges (a real range from
   earlier action, not "every hand equally likely") without a second
   implementation.

2. `mccfr_solve()` — External-Sampling MCCFR (traverser-vectorized),
   for N>=2 players generally (used for N>=3 in practice). Exhaustive
   CFR does not survive the jump to 3+ players: the value representation
   would need to become an N-dimensional (169^N) tensor, which blows up
   in both memory and compute well before N=4 — see the project plan
   for the numbers. Instead, each iteration picks one *traverser*
   position (cycling through all players across iterations); the
   traverser's own hand stays vectorized over all 169 classes and their
   own decision nodes are explored exhaustively (regret/strategy-sum
   updates stay exact for them), while every *other* position's hole
   cards and every one of their action choices are sampled once per
   iteration from their true distributions (combo_weight for hole
   cards, or a caller-supplied per-position `initial_reach` override as
   of M31 — see mccfr_solve's own docstring; current strategy, blended
   with a small exploration floor, for actions — sampled directly, with
   no importance-sampling correction, see EXPLORATION_EPSILON and
   _mccfr_recurse below for why). The
   exploration floor turned out to matter in practice, not just in
   theory: CFR+'s regret flooring can legitimately push
   current_strategy() to an *exact* 0/1 split even when the true
   equilibrium wants a small residual frequency (e.g. "call 0.2% of the
   time"), and sampling straight from that degenerate strategy makes the
   rare-but-consequential branch permanently unreachable — silently
   starving the traverser's value estimate of it. An importance-sampling
   correction (the textbook-unbiased fix) was tried first but rejected:
   at N>=3 it compounds multiplicatively across nested opponent decisions
   (e.g. SB's node then BB's node), producing high-variance outliers that
   CFR+'s regret flooring turns actively destructive (one bad iteration
   can erase many iterations' worth of accumulated positive regret).
   Sampling directly from the floored strategy trades textbook
   unbiasedness for a small, bounded, non-compounding bias instead — see
   _mccfr_recurse for the full writeup and the M8 PR/commit for the
   diagnosis. This was caught during M8 by cross-validating against the
   exact solver at N=2, then stress-tested at N=3 against known GTO
   preflop intuition (premium hands almost never fold) before trusting
   it. Terminal payoffs come from equity.MultiwayEquityCache rather than
   a precomputed table, since a full N-way equity table is never viable
   to build eagerly.

   **A caveat worth stating plainly, not left implicit** (flagged by
   docs/full-table-diagnostic-2026-08.md's SS3.8): CFR/CFR+'s proof that
   the average strategy converges to a Nash equilibrium is a two-player
   zero-sum result. At N>=3, nothing here (or in the general CFR+
   literature) guarantees convergence to a Nash equilibrium — only, at
   best, to a coarse-correlated equilibrium, and that's before counting
   this module's own additional approximations above (no importance-
   sampling correction, MultiwayEquityCache's blocker-ignoring pairwise
   approximation). This doesn't make mccfr_solve's output meaningless —
   M9-M27's own directional GTO sanity checks (premium hands rarely
   fold, weak hands fold far more, etc.) hold up empirically at every
   player count tested — but "converges toward good play" and "provably
   reaches equilibrium" are different claims, and only the first one is
   actually backed by a proof at N>=3.

3. `solve()`'s exact path also optionally walks through `chance.ChanceNode`s
   (M12) — a "deal the next community card" point, e.g. flop->turn — via
   the `chance_fn`/`chance_data` parameters. See `_solve_recurse`'s
   ChanceNode branch below and `chance.py`'s module docstring for the
   full design (why dispatch has to turn itself off per-branch rather
   than thread unconditionally, and the approximation this introduces).
   Both parameters default to `None`, and every pre-M12 call site omits
   them — `chance_fn is None` short-circuits the new logic entirely, so
   this is purely additive to the existing exact-CFR behavior.

4. `mccfr_solve()` gained its own, differently-shaped chance-dispatch
   capability at M32 (Phase 3 of docs/full-table-diagnostic-2026-08.md's
   recommendation #5, "true multiway postflop solving") — also via
   `chance_fn`/`chance_data` parameters (same names, deliberately
   mirroring `solve()`'s), plus a new `board` parameter. Where `solve()`
   AVERAGES over every one of a `ChanceNode`'s branches every iteration
   (correct there, since the exact solver visits the whole tree
   exhaustively anyway), `mccfr_solve()` SAMPLES exactly one card per
   visit, via `chance.build_mccfr_chance_branch` and a new
   `chance.SampledChanceBranch` (deliberately not `ChanceBranch`/
   `ChanceNode`, which are shaped for eager, all-branches, pairwise-table
   use — see both dataclasses' own docstrings). Reusing `build_chance_
   node`'s eager approach here would defeat MCCFR's entire reason to
   exist — see `build_mccfr_chance_branch`'s own docstring for the
   measured cost comparison. The sampled branch's own equity source
   (M30's board-aware `NwayBoardEquityCache`) is threaded into
   `_mccfr_terminal_value` via plain duck typing — nothing about that
   function's signature changed, since `MultiwayEquityCache` and
   `NwayBoardEquityCache` both expose the identical
   `.traverser_equity_vector(opponent_hands) -> np.ndarray` shape. See
   `_mccfr_recurse`'s own `TerminalNode`-branch dispatch for the
   sampling/memoization mechanics and why its gating condition
   necessarily diverges from `_solve_recurse`'s (traverser-vectorized,
   not both-positions-at-once), and `_sample_chance_card`'s own docstring
   for why chance-card sampling needs neither `EXPLORATION_EPSILON`'s
   exploration floor nor `MAX_OPPONENT_RESAMPLE_ATTEMPTS`'s
   reject-and-resample loop, unlike this module's other two sampling
   decisions.

Reach probabilities are seeded with each hand's combo_weight (its prior
probability of being dealt) in both paths — see the project plan for why
card-removal/blocker effects are still ignored (each class is treated as
an independent unit) at any N.
"""

import random
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from .cards import Card, remaining_deck
from .chance import ChanceNode
from .combos import HandCombo
from .equity import MultiwayEquityCache, deal_n_hands
from .game_tree import BB, BTN, DecisionNode, TerminalNode

# Opponent-action sampling always retains at least this much probability
# mass on every legal action, regardless of how converged/degenerate
# current_strategy() has become — see the module docstring and
# _mccfr_recurse for why this is necessary, not just a nice-to-have.
#
# Kept deliberately small: since sampling is no longer importance-weight
# corrected (see _mccfr_recurse), each opponent decision along a path has
# an EXPLORATION_EPSILON chance of taking a "forced exploration" action
# rather than their true strategy, biasing that one sampled path. This
# compounds with tree depth (roughly 1-(1-eps)^depth chance that *some*
# node along the path was forced) — at 3-max's default 4-raise tree,
# depth-4 opponent chains are possible, so a large epsilon here measurably
# slows/biases convergence for hands whose value depends on getting deep
# into the tree correctly (empirically, 0.15 visibly hurt convergence at
# N=3 for exactly this reason; 0.05 measured meaningfully better without
# reintroducing the original degenerate-lockout bug it exists to prevent).
EXPLORATION_EPSILON = 0.05

# mccfr_solve samples each opponent's hand independently, with no
# card-removal tracking between them (see the module docstring above)
# — so the resulting tuple can be physically undealable (e.g. two
# opponents both drawn as KK exhausts all 4 kings). Left unresampled,
# MultiwayEquityCache.traverser_equity_vector still has to return
# *something* for these, and even a well-chosen placeholder (see
# equity.py) turned out to compound rather than average out under CFR+'s
# regret flooring — measured during M27 at real 6-max scale: a hand's
# fold rate that should stabilize instead grew monotonically with more
# iterations (22.8% at 300 -> 69.2% at 3,000 -> 94.8% at 30,000), the
# same "gets worse with more iterations" signature M8 already diagnosed
# for a different mechanism (see EXPLORATION_EPSILON above). Rejecting
# an infeasible opponent tuple and resampling a fresh one — instead of
# proceeding and needing a placeholder at all — attacks this at the
# source rather than picking a better constant to compound. Capped, not
# unbounded: real hands pools were measured to need at most a handful of
# retries (infeasibility rates topped out in the low tens of percent
# even at 9-max's 8 opponents), so this generous a cap is essentially
# never exhausted in practice; it exists only so a pathologically small
# or degenerate hands pool (e.g. every candidate the same single pair
# class) can't hang the solver in an unwinnable resampling loop — that
# case falls back to proceeding with the last (infeasible) draw, exactly
# like every draw did before this fix existed.
MAX_OPPONENT_RESAMPLE_ATTEMPTS = 50


@dataclass
class InfoSetTable:
    """Regret/strategy accumulators for one DecisionNode, across every
    hand class the acting player could hold at that node.

    **Not thread-safe, deliberately not fixed here** (docs/full-table-
    diagnostic-2026-08.md's §3.10): `regret_sum`/`strategy_sum`'s
    read-modify-write updates in `_solve_recurse`/`_mccfr_recurse` (e.g.
    `table.regret_sum = np.maximum(table.regret_sum + regret, 0.0)`) are
    unguarded. Every solving path in this codebase visits one `node_data`
    dict from a single Python thread for the full duration of one solve
    (no parallel-traverser architecture exists yet), so this is not a
    live bug — but it does mean a *future* concurrent-solving design
    can't just start spawning traverser threads against a shared
    `node_data` without addressing this first. Deliberately NOT adding a
    lock here now, unlike `equity.MultiwayEquityCache`'s own analogous
    §3.10 fix: this struct is mutated on the single hottest path in the
    entire engine (once per node, per iteration, of every solve that
    exists today), where a lock's overhead — real even uncontended,
    unlike the comparatively rare, already-expensive `MultiwayEquityCache`
    lookups it would be paid in exchange for — would cost every current,
    single-threaded caller something for a scaling move that doesn't
    exist yet and whose actual synchronization needs aren't decided (a
    future parallel-traverser design might use per-thread `node_data`
    dicts merged at the end instead, sidestepping per-table locking
    entirely, rather than needing exactly this lock). Named explicitly so
    it's a decision on record, not a gap nobody noticed.
    """

    regret_sum: np.ndarray
    strategy_sum: np.ndarray
    # M97: the most recent iteration's instantaneous regret at this node,
    # used as the one-step prediction by predictive regret matching. None
    # until the first update; `optimism=0.0` never reads it.
    last_regret: np.ndarray | None = None
    # M97: the policy this node last played, for `smoothing`. None until
    # the first visit; `smoothing=0.0` never reads it.
    last_strategy: np.ndarray | None = None

    @classmethod
    def zeros(cls, num_hands: int, num_actions: int) -> "InfoSetTable":
        return cls(
            regret_sum=np.zeros((num_hands, num_actions)),
            strategy_sum=np.zeros((num_hands, num_actions)),
        )

    def current_strategy(self, optimism: float = 0.0, smoothing: float = 0.0) -> np.ndarray:
        """Regret-matching+ strategy: shape (num_hands, num_actions).

        Two optional modifications, **both off by default and both
        measured worse than off** (M97). They are kept so the result is
        reproducible rather than folklore — see `mccfr_solve`'s docstring
        for the numbers, and do not enable either without re-measuring.

        - `smoothing` **damps**: the policy is blended with the one this
          node last played, `smoothing * last + (1 - smoothing) * fresh`,
          so a policy that swings wholesale between two near-tied actions
          moves in steps instead.
        - `optimism` **anticipates**: the policy is matched against
          `regret_sum + optimism * last_regret` — accumulated regret plus
          one more copy of the most recent instantaneous regret, as a
          guess at what the next iteration will add. This is predictive
          regret matching.

        Both were built for the oscillation M74 diagnosed: when two
        actions are near-tied, plain regret matching swings the policy
        wholesale between them (AA's jam comes out ~0.000 or ~1.000
        depending on the run), and the time-average then reports whichever
        phase the run ended in. M74 prescribed policy damping as the fix.

        Neither works, and the reasons differ:

        - Prediction is a **full-information** technique. Under external
          sampling the last instantaneous regret is dominated by the
          all-in — the noisiest action in the tree, since its payoff
          swings a whole stack, which is the same thing M71 found when it
          removed the CFR+ clamp. Adding another copy of it amplifies
          sampling noise rather than damping a cycle.
        - Damping is a **lag filter on the output** of regret matching,
          while the oscillation lives in `regret_sum`, its input. A
          delayed oscillation is still an oscillation, and `strategy_sum`
          accumulates it either way. The mechanism also has a hard
          ceiling: enough damping to outlast a cycle thousands of
          iterations long also stops the policy learning at all —
          measured, at `smoothing=0.99`, as AA jamming 0.998 with a seed
          spread of 0.000 while T7s's fold *collapsed* from ~0.94 to
          ~0.34.

        At 0.0 each is skipped entirely, and the arrays they read
        (`last_regret`, `last_strategy`) are not even stored — see the
        record sites in `_mccfr_recurse` for why that matters.
        """
        regrets = self.regret_sum
        if optimism and self.last_regret is not None:
            regrets = regrets + optimism * self.last_regret
        positive = np.maximum(regrets, 0.0)
        totals = positive.sum(axis=1, keepdims=True)
        num_actions = regrets.shape[1]
        uniform = np.full_like(regrets, 1.0 / num_actions)
        with np.errstate(invalid="ignore", divide="ignore"):
            normalized = positive / totals
        strategy = np.where(totals > 0, normalized, uniform)
        if smoothing and self.last_strategy is not None:
            strategy = smoothing * self.last_strategy + (1.0 - smoothing) * strategy
        return strategy

    def average_strategy(self) -> np.ndarray:
        """The actual CFR output: the time-averaged strategy."""
        totals = self.strategy_sum.sum(axis=1, keepdims=True)
        num_actions = self.strategy_sum.shape[1]
        uniform = np.full_like(self.strategy_sum, 1.0 / num_actions)
        with np.errstate(invalid="ignore", divide="ignore"):
            normalized = self.strategy_sum / totals
        return np.where(totals > 0, normalized, uniform)

    def trained_mask(self) -> np.ndarray:
        """Boolean array, shape (num_hands,): whether each hand has any
        accumulated strategy_sum at this node at all, or would silently
        fall back to average_strategy()'s own uniform-prior default.

        Exactly the condition average_strategy() already checks
        internally (`totals > 0`) — this exposes it instead of
        discarding it, so a caller can tell a genuinely converged
        answer from one nobody ever computed. That distinction is real:
        MCCFR only visits nodes/hands actually reached along a sampled
        path, unlike the exact heads-up solver, which visits every hand
        at every node exhaustively on every iteration (so this mask is
        trivially all-True for an exact-path result) — see
        docs/full-table-diagnostic-2026-08.md's SS3.3, which measured 88%
        of a real 9-max solve's touched decision nodes at exactly zero
        strategy_sum for at least one hand.
        """
        return self.strategy_sum.sum(axis=1) > 0


def _terminal_value_matrix(
    node: TerminalNode, equity_table: np.ndarray, position_a: str, position_b: str
) -> np.ndarray:
    """`position_a`'s net payoff matrix (shape num_hands x num_hands) at a leaf."""
    if position_a in node.folded:
        return np.full(equity_table.shape, -node.invested[position_a])
    if position_b in node.folded:
        return np.full(equity_table.shape, node.pot - node.invested[position_a])
    return equity_table * node.pot - node.invested[position_a]


def _solve_recurse_matrix(
    node,
    reach_a: np.ndarray,
    reach_b: np.ndarray,
    updating_player: str,
    node_data: dict,
    equity_table: np.ndarray,
    position_a: str,
    position_b: str,
    chance_fn: Optional[Callable] = None,
    chance_data: Optional[dict] = None,
    strategy_weight: float = 1.0,
) -> np.ndarray:
    """Returns this node's value matrix (`position_a`'s payoff, shape NxN).

    M161: this is NO LONGER the shipped recursion — `_solve_recurse` is,
    and it propagates an N-vector instead of this N x N matrix. This one
    is kept, deliberately and with a caller, as the REFERENCE the rewrite
    is validated against: `test_the_vector_recursion_matches_the_matrix_
    one` runs both through `solve()` over whole trees and asserts the
    resulting strategies agree. A rewrite of the solver every reference
    solve and the canonical library depend on is worth the dead weight of
    keeping the thing it replaced runnable.

    `chance_fn`/`chance_data` (M12): if `chance_fn` is set, a
    showdown-eligible `TerminalNode` (`is_showdown`, i.e. capped action
    without a fold) is treated not as a true showdown but as the point
    where the next street's chance node gets built (once, then memoized
    in `chance_data` by `id(node)` — the tree structure is stable across
    iterations, same assumption `node_data`'s own id-keying already
    relies on) and recursed into instead. A `ChanceNode`'s value is the
    uniform average of its branches' values — no regret/strategy update
    happens at a chance node itself, it isn't a decision point.

    Critically, each branch recurses with `chance_fn=branch.chance_fn`
    (not the ambient `chance_fn`), which is `None` for every M12 branch
    — so a turn-level showdown terminal correctly falls through to
    `_terminal_value_matrix` using that branch's own (already
    river-averaged) equity table, instead of being handed back to the
    flop-scoped `chance_fn` and having a card dealt off the wrong (3-card)
    board. See chance.py's module docstring for why this per-branch
    on/off switch is the correct design, not an unconditional thread-through.
    """
    if isinstance(node, ChanceNode):
        branch_values = [
            _solve_recurse_matrix(
                branch.root, reach_a, reach_b, updating_player, node_data,
                branch.equity_table, position_a, position_b,
                chance_fn=branch.chance_fn, chance_data=chance_data,
                strategy_weight=strategy_weight,
            )
            for branch in node.branches.values()
        ]
        return sum(branch_values) / len(branch_values)

    if isinstance(node, TerminalNode):
        if chance_fn is not None and node.is_showdown:
            chance_node = chance_data.get(id(node))
            if chance_node is None:
                chance_node = chance_fn(node)
                chance_data[id(node)] = chance_node
            return _solve_recurse_matrix(
                chance_node, reach_a, reach_b, updating_player, node_data,
                equity_table, position_a, position_b, chance_fn, chance_data,
                strategy_weight,
            )
        return _terminal_value_matrix(node, equity_table, position_a, position_b)

    num_hands = equity_table.shape[0]
    actions = node.legal_actions
    table = node_data.setdefault(id(node), InfoSetTable.zeros(num_hands, len(actions)))
    strategy = table.current_strategy()
    acting_is_a = node.player_to_act == position_a

    child_values = []
    for a_idx, action in enumerate(actions):
        child = node.children[action]
        if acting_is_a:
            child_value = _solve_recurse_matrix(
                child, reach_a * strategy[:, a_idx], reach_b, updating_player,
                node_data, equity_table, position_a, position_b, chance_fn, chance_data,
                strategy_weight,
            )
        else:
            child_value = _solve_recurse_matrix(
                child, reach_a, reach_b * strategy[:, a_idx], updating_player,
                node_data, equity_table, position_a, position_b, chance_fn, chance_data,
                strategy_weight,
            )
        child_values.append(child_value)

    # M160: follow the equity table's precision rather than forcing
    # float64. These are the solve's dominant cost — one num_hands x
    # num_hands matrix per node, ~809KB at 318 combos in float64, carried
    # through every node on every iteration. Measured on a real flop
    # solve: 16 decision nodes and 29 terminals, 11.2ms per iteration,
    # ~18GB of traffic per 500-iteration solve. The values themselves come
    # from a 30-sample Monte Carlo equity estimate whose own error is
    # ~0.09, so float64's 16 digits were never carrying information.
    #
    # Accumulators stay float64 — see InfoSetTable.zeros. Regret sums
    # accumulate across hundreds of iterations, which is where precision
    # actually matters; these transients do not.
    value_dtype = child_values[0].dtype if child_values else equity_table.dtype
    node_value = np.zeros((num_hands, num_hands), dtype=value_dtype)
    for a_idx, child_value in enumerate(child_values):
        if acting_is_a:
            node_value += strategy[:, a_idx][:, None] * child_value
        else:
            node_value += strategy[:, a_idx][None, :] * child_value

    if node.player_to_act == updating_player:
        acting_reach = reach_a if acting_is_a else reach_b
        opponent_reach = reach_b if acting_is_a else reach_a

        cf_action_values = np.zeros((num_hands, len(actions)), dtype=value_dtype)
        for a_idx, child_value in enumerate(child_values):
            if acting_is_a:
                cf_action_values[:, a_idx] = child_value @ opponent_reach
            else:
                cf_action_values[:, a_idx] = (-child_value).T @ opponent_reach
        cf_node_value = (
            node_value @ opponent_reach if acting_is_a else (-node_value).T @ opponent_reach
        )

        regret = cf_action_values - cf_node_value[:, None]
        table.regret_sum = np.maximum(table.regret_sum + regret, 0.0)  # CFR+: floor at 0
        # M71: weight this iteration's contribution to the time-average by
        # `strategy_weight`, the same fix M69 applied to the sampled
        # solver. The exact solver had the identical defect and it was
        # NOT harmless: on a toy AA-vs-72o game its average all-in
        # frequency read 0.656 at 500 iterations and only drifted to
        # 0.892 / 0.956 / 0.969 at 2k / 10k / 50k. That 0.656 was not the
        # equilibrium, it was the untrained opening iterations still
        # sitting in the average — and it was being used as the "ground
        # truth" that test_mccfr_agrees_with_exact_solve_at_heads_up
        # checked the sampled solver against.
        table.strategy_sum += strategy_weight * acting_reach[:, None] * strategy

    return node_value


def _terminal_value_vector(
    node: TerminalNode,
    equity_table: np.ndarray,
    position_a: str,
    position_b: str,
    traverser_is_a: bool,
    reach_opp: np.ndarray,
) -> np.ndarray:
    """The traverser's counterfactual value VECTOR at a leaf, length num_hands.

    Exactly `_terminal_value_matrix(...) @ reach_opp` when the traverser
    is `position_a`, and exactly `(-_terminal_value_matrix(...)).T @
    reach_opp` when it is `position_b` — the two expressions the matrix
    recursion used to build, without ever materialising the N x N matrix
    it built them from. That matters because the matrix version allocates
    a fresh `equity_table * pot - invested` at every showdown terminal on
    every iteration; this reads the shared table once and writes an
    N-vector.

    **Everything here is expressed in `position_a`'s payoff and negated
    for `position_b`, deliberately, because that is what the matrix
    version does — and it is NOT the same as computing `position_b`'s own
    payoff.** `node.pot` includes dead money carried in from earlier
    streets, so `value_a + value_b == pot - invested_a - invested_b`,
    which is the dead pot, not zero. Valuing b as `-value_a` therefore
    offsets b's counterfactual values by that dead pot. Within a single
    street the offset is identical at every terminal and cancels out of
    every regret difference, so it is invisible. Across a chance node
    into a new street it does not cancel, because the new street's dead
    pot depends on how much was bet to reach it — measured at 0.97 of
    absolute strategy difference on a flop->turn tree, dtype-independent.

    Reproducing the offset rather than removing it is intentional: this
    milestone is a performance rewrite, and silently changing what the
    solver computes on every multi-street solve would make its own
    speedup unmeasurable and every reference solve incomparable. See
    F45 in CLAUDE.md for the finding and what adjudicating it needs.

    Blocked hand pairs are NOT excluded, matching the matrix version:
    `solver.solve_flop` replaces the equity table's NaNs with 0.5 before
    solving, so every (i, j) pair is treated as reachable.
    """
    num_hands = equity_table.shape[0]
    opp_mass = reach_opp.sum()
    invested_a = node.invested[position_a]

    constant = None
    if position_a in node.folded:
        constant = -invested_a
    elif position_b in node.folded:
        constant = node.pot - invested_a
    if constant is not None:
        total = constant * opp_mass
        return np.full(
            num_hands, total if traverser_is_a else -total, dtype=reach_opp.dtype
        )

    if traverser_is_a:
        return (equity_table @ reach_opp) * node.pot - invested_a * opp_mass
    # The table is indexed [a_hand, b_hand], so b's own axis is the
    # column — hence the transpose, matching the matrix version's `.T`.
    return invested_a * opp_mass - (equity_table.T @ reach_opp) * node.pot


def _vector_recurse(
    node,
    reach_trav: np.ndarray,
    reach_opp: np.ndarray,
    traverser: str,
    position_a: str,
    position_b: str,
    traverser_is_a: bool,
    node_data: dict,
    equity_table: np.ndarray,
    chance_fn: Optional[Callable],
    chance_data: Optional[dict],
    strategy_weight: float,
) -> np.ndarray:
    """Returns the traverser's counterfactual value VECTOR at `node`.

    Entry `i` is `sum_j reach_opp(node)[j] * value(i, j)` where
    `reach_opp(node)` is the opponent's probability of reaching this node
    holding hand `j`. The matrix recursion this replaces returned the
    whole `value(i, j)` and multiplied by the opponent's reach only at
    the ancestor that needed it; pushing the reach DOWN instead makes
    every node's return O(N) rather than O(N^2).

    The two are algebraically identical, and the step that makes it work
    is worth stating because it is not the obvious one. At a node where
    the OPPONENT acts, the matrix version weights each child matrix by
    the opponent's action probability along the column axis. Here that
    probability is instead folded into the child's own `reach_opp`, so
    the branches **sum** rather than average or re-weight:

        u_h[i] = sum_j reach_opp(h)[j] * sum_a sigma[j,a] * V_{h.a}[i,j]
               = sum_a sum_j reach_opp(h.a)[j] * V_{h.a}[i,j]
               = sum_a u_{h.a}[i]

    At a node where the TRAVERSER acts the opponent's reach is unchanged,
    so each child's returned vector IS that action's counterfactual value
    — the quantity the matrix version had to compute as
    `child_value @ opponent_reach`. Regret is then a subtraction of
    vectors rather than a stack of matrix-vector products.

    `chance_fn`/`chance_data` behave exactly as in the matrix version,
    including recursing each branch with `branch.chance_fn` rather than
    the ambient one — see `_solve_recurse_matrix`'s docstring for why
    that per-branch switch is the correct design.
    """
    if isinstance(node, ChanceNode):
        branch_values = [
            _vector_recurse(
                branch.root, reach_trav, reach_opp, traverser, position_a,
                position_b, traverser_is_a, node_data, branch.equity_table,
                branch.chance_fn, chance_data, strategy_weight,
            )
            for branch in node.branches.values()
        ]
        return sum(branch_values) / len(branch_values)

    if isinstance(node, TerminalNode):
        if chance_fn is not None and node.is_showdown:
            chance_node = chance_data.get(id(node))
            if chance_node is None:
                chance_node = chance_fn(node)
                chance_data[id(node)] = chance_node
            return _vector_recurse(
                chance_node, reach_trav, reach_opp, traverser, position_a,
                position_b, traverser_is_a, node_data, equity_table, chance_fn,
                chance_data, strategy_weight,
            )
        return _terminal_value_vector(
            node, equity_table, position_a, position_b, traverser_is_a, reach_opp
        )

    num_hands = equity_table.shape[0]
    actions = node.legal_actions
    table = node_data.setdefault(id(node), InfoSetTable.zeros(num_hands, len(actions)))
    strategy = table.current_strategy()
    acting_is_traverser = node.player_to_act == traverser

    # M160 kept the value path in the equity table's precision; the reach
    # vectors have to follow it or `equity_table @ reach_opp` at every
    # terminal upcasts the SHARED table to float64 and undoes the halved
    # memory traffic that change bought. `current_strategy()` reads off
    # float64 accumulators, so the cast is here rather than there —
    # accumulation stays in double, propagation does not.
    reach_strategy = strategy.astype(reach_opp.dtype, copy=False)

    child_values = []
    for a_idx, action in enumerate(actions):
        child = node.children[action]
        if acting_is_traverser:
            child_values.append(_vector_recurse(
                child, reach_trav * reach_strategy[:, a_idx], reach_opp,
                traverser, position_a, position_b, traverser_is_a, node_data,
                equity_table, chance_fn, chance_data, strategy_weight,
            ))
        else:
            child_values.append(_vector_recurse(
                child, reach_trav, reach_opp * reach_strategy[:, a_idx],
                traverser, position_a, position_b, traverser_is_a, node_data,
                equity_table, chance_fn, chance_data, strategy_weight,
            ))

    if not acting_is_traverser:
        # See the docstring: the opponent's action probability is already
        # inside each child's reach_opp, so this is a plain sum.
        return sum(child_values)

    cf_action_values = np.stack(child_values, axis=1)
    node_value = (cf_action_values * reach_strategy).sum(axis=1)

    regret = cf_action_values - node_value[:, None]
    table.regret_sum = np.maximum(table.regret_sum + regret, 0.0)  # CFR+: floor at 0
    # M71: weight this iteration's contribution to the time-average by
    # `strategy_weight` — see `_solve_recurse_matrix` for what an
    # unweighted average cost on a toy game that was being used as ground
    # truth. `strategy` (float64), not `reach_strategy`, on purpose: this
    # is an accumulator.
    table.strategy_sum += strategy_weight * reach_trav[:, None] * strategy

    return node_value


def _solve_recurse(
    node,
    reach_a: np.ndarray,
    reach_b: np.ndarray,
    updating_player: str,
    node_data: dict,
    equity_table: np.ndarray,
    position_a: str,
    position_b: str,
    chance_fn: Optional[Callable] = None,
    chance_data: Optional[dict] = None,
    strategy_weight: float = 1.0,
) -> np.ndarray:
    """Vector CFR (M161). Signature-compatible with `_solve_recurse_matrix`.

    Keeps the (reach_a, reach_b, position_a, position_b) calling
    convention so the two are drop-in swappable from `solve()`, which is
    what lets the equivalence test run whole trees through both. The
    traverser-relative view the vector recursion actually needs is
    derived here, once, rather than threaded through every call site.

    Returns the updating player's counterfactual value VECTOR, not
    `position_a`'s payoff matrix. Nothing outside this module consumed
    the return value — `solve()` discards it — so the change is internal.
    """
    traverser_is_a = updating_player == position_a
    return _vector_recurse(
        node,
        reach_a if traverser_is_a else reach_b,
        reach_b if traverser_is_a else reach_a,
        updating_player,
        position_a,
        position_b,
        traverser_is_a,
        node_data,
        equity_table,
        chance_fn,
        chance_data,
        strategy_weight,
    )


def solve(
    root: DecisionNode,
    hands: list,
    equity_table: np.ndarray,
    iterations: int = 1000,
    positions: tuple = (BTN, BB),
    initial_reach: dict | None = None,
    chance_fn: Optional[Callable] = None,
    chance_data: Optional[dict] = None,
    linear_averaging: bool = True,
    initial_node_data: Optional[dict] = None,
    _recurse: Optional[Callable] = None,
) -> dict:
    """Run `iterations` of CFR+ over `root`, for the given `hands`.

    `linear_averaging` (M71, default True) weights iteration t's
    contribution to the time-average by t. This solver is EXACT — it
    marginalizes over the opponent's whole range instead of sampling — so
    it was assumed not to need M69's fix. That assumption was wrong, and
    the cost was measurable: on a toy AA-vs-72o game, AA's averaged
    all-in frequency read **0.656 at 500 iterations and drifted to 0.892 /
    0.956 / 0.969 at 2k / 10k / 50k**. The equilibrium is ~0.97; the 0.656
    was the untrained opening iterations still weighing in the average.

    That mattered beyond cosmetics, because this solver is the reference
    the sampled one is validated against
    (test_mccfr_agrees_with_exact_solve_at_heads_up). A contaminated
    reference makes a correct sampled answer look like a regression, which
    is exactly what happened when M71 removed the CFR+ regret clamp.

    `equity_table` must be shaped (len(hands), len(hands)) with rows/cols
    in the same order as `hands` (see equity.get_equity_table).

    `positions` is the (first, second) position labels this tree uses —
    defaults to `(BTN, BB)`, preflop's convention, unchanged from before
    this parameter existed. A flop-only tree (M11) passes its own two
    position labels instead (e.g. `("OOP", "IP")` — postflop's natural
    labels, not blinds) since `root.player_to_act` values come from
    whatever `GameConfig`/`StreetConfig` built the tree, not from this
    module.

    `initial_reach` optionally maps position -> starting reach-weight
    array (same length/order as `hands`), overriding the default
    combo_weight-derived prior (each hand's raw prior probability of
    being dealt) for that position — e.g. a real range carried over from
    an earlier street's action, not "every hand equally likely to have
    continued." A position missing from `initial_reach` (or the default
    `None`, meaning "no overrides at all") still falls back to
    combo_weight, so every pre-existing preflop call site is unaffected.

    `chance_fn` (M12) optionally chains this tree into a *further*
    street: whenever a showdown-eligible terminal is reached, `chance_fn(
    terminal)` is called (once per distinct terminal, memoized in
    `chance_data`) to produce a `chance.ChanceNode`, which is recursed
    into instead of treating the terminal as an immediate showdown. See
    `_solve_recurse`'s docstring for exactly how dispatch turns itself
    off inside a chance branch's own subtree. `chance_data` defaults to a
    fresh `{}` when `chance_fn` is set and no dict is supplied — pass
    your own to read it back afterward (e.g. to inspect a specific
    branch's subtree once solving is done). Both default to `None`,
    matching every pre-M12 call site's behavior exactly (no dispatch at
    all — every showdown-eligible terminal is valued directly from
    `equity_table`, unchanged).

    Returns a dict of {id(DecisionNode): InfoSetTable}. There's no
    randomness anywhere in this process (the tree is walked exhaustively,
    not sampled), so results are exactly deterministic for a given
    (root, hands, equity_table, iterations, positions, initial_reach,
    chance_fn's own determinism).
    """
    if equity_table.shape != (len(hands), len(hands)):
        raise ValueError(
            f"equity_table shape {equity_table.shape} doesn't match "
            f"len(hands)={len(hands)}"
        )
    position_a, position_b = positions
    initial_reach = initial_reach or {}
    if chance_fn is not None and chance_data is None:
        chance_data = {}

    def _default_reach():
        # Computed lazily, not eagerly: `hands` may be combos.HandCombo
        # (M11's flop tree), which has no combo_weight at all — fine, as
        # long as every position is actually overridden by initial_reach
        # in that case, which solve_flop always does. Only preflop's
        # StartingHand-keyed calls (where combo_weight exists) ever fall
        # through to this.
        return np.array([hand.combo_weight for hand in hands])

    # M160: match the equity table, or every operation promotes back to
    # float64 and the halved table read buys nothing downstream.
    reach_dtype = equity_table.dtype if equity_table.dtype.kind == "f" else float
    reach_a = np.array(
        initial_reach[position_a] if position_a in initial_reach else _default_reach(),
        dtype=reach_dtype,
    )
    reach_b = np.array(
        initial_reach[position_b] if position_b in initial_reach else _default_reach(),
        dtype=reach_dtype,
    )

    # M158: `initial_node_data` warm-starts the solve. `_solve_recurse`
    # does `node_data.setdefault(id(node), zeros(...))`, so a
    # pre-populated dict simply IS the starting point - regrets and
    # strategy sums accumulate on top of whatever is handed in.
    #
    # The caller owns the hard part: node_data is keyed by `id(node)` and
    # every request rebuilds its tree, so tables have to be re-keyed onto
    # the new nodes (a node's action path from the root is stable across
    # rebuilds of the same config) and re-shaped if the hand pool changed.
    # Passing a table whose row count does not match `hands` is a
    # programming error, so it is checked rather than broadcast.
    node_data: dict = dict(initial_node_data or {})
    for table in node_data.values():
        if table.regret_sum.shape[0] != len(hands):
            raise ValueError(
                f"initial_node_data has {table.regret_sum.shape[0]} hand rows "
                f"but this solve has {len(hands)} hands — re-shape the tables "
                "onto the new pool before warm-starting"
            )
    # M161: `_recurse` is private and exists for one reason — so
    # `test_the_vector_recursion_matches_the_matrix_one` can drive the
    # replaced implementation through this exact loop rather than a
    # re-implementation of it in the test, which could drift from this
    # one and quietly stop comparing what ships. Production never passes
    # it; the default IS the shipped recursion.
    recurse = _recurse or _solve_recurse
    for iteration in range(iterations):
        updating_player = position_a if iteration % 2 == 0 else position_b
        recurse(
            root, reach_a.copy(), reach_b.copy(), updating_player,
            node_data, equity_table, position_a, position_b,
            chance_fn, chance_data,
            float(iteration + 1) if linear_averaging else 1.0,
        )
    return node_data


# ---------------------------------------------------------------------------
# External-Sampling MCCFR (traverser-vectorized), for N-player trees.
# ---------------------------------------------------------------------------


def _continuation_key(pot: float, chips_behind: float, live_seats: int) -> tuple:
    """Local mirror of `continuation.continuation_key`.

    Imported lazily rather than at module scope because `continuation.py`
    imports from this module — a top-level import here would be a cycle.
    Kept as a one-line delegation so the definition lives in exactly one
    place.
    """
    from .continuation import continuation_key

    return continuation_key(pot, chips_behind, live_seats)


def _mccfr_terminal_value(
    node: TerminalNode,
    traverser: str,
    opponent_hands: dict,
    num_hands: int,
    equity_cache,
    continuation: float = 0.0,
    stack_bb: float | None = None,
    continuation_table: dict | None = None,
) -> np.ndarray:
    """The traverser's net payoff for each of their `num_hands` possible
    hands (length-`num_hands` vector), given the fixed `opponent_hands`
    sampled for this iteration.

    `equity_cache` is duck-typed, not restricted to `MultiwayEquityCache`
    by anything this function actually checks: any object exposing
    `.traverser_equity_vector(opponent_hands: tuple) -> np.ndarray`
    (indexed over that object's own fixed candidate pool) works here
    unchanged — as of M32, that includes `multiway_board_equity.
    NwayBoardEquityCache` (board-aware, reached via a sampled chance
    branch — see `_mccfr_recurse`), not just the original board-blind
    `MultiwayEquityCache`. Nothing about this function's own signature or
    body needed to change to support that; only the NaN-handling line
    below is new.
    """
    if traverser in node.folded:
        return np.full(num_hands, -node.invested[traverser])

    live = [p for p in node.invested if p not in node.folded]
    other_live = [p for p in live if p != traverser]
    if not other_live:
        # Everyone else folded — the traverser wins the pot outright,
        # regardless of which hand they hold.
        return np.full(num_hands, node.pot - node.invested[traverser])

    opponent_live_hands = tuple(opponent_hands[p] for p in other_live)
    equity_vector = equity_cache.traverser_equity_vector(opponent_live_hands)

    # M66: mark hands whose "equity" is fabricated rather than simulated,
    # so `_mccfr_recurse` can skip their regret update instead of learning
    # from a number that has no true value. Two equity sources, one signal:
    #   - MultiwayEquityCache (preflop, class-level) has to return a dense
    #     float array, so it reports fabrication out-of-band via
    #     `traverser_validity_mask` (M66); we fold that into NaN here.
    #   - NwayBoardEquityCache (postflop, combo-level) already reports it
    #     IN-band as NaN (M30's deliberate "no placeholder value, ever"
    #     convention), so it needs no mask method and gets none.
    # Both therefore arrive at the same representation below, and the
    # `getattr` matches this function's existing duck-typing of
    # `equity_cache` rather than introducing an isinstance check.
    validity_mask = getattr(equity_cache, "traverser_validity_mask", None)
    if validity_mask is not None:
        equity_vector = np.where(validity_mask(opponent_live_hands), equity_vector, np.nan)
    # M32 replaced NaN with a neutral 0.5 here. M66 deliberately does NOT:
    # NaN is now the signal, and it is left intact so it propagates. Every
    # arithmetic step from here to the traverser's own decision node is
    # per-hand (`einsum("ha,ha->h")` contracts over actions, never over
    # hands), so a NaN for hand h taints hand h's value at every ancestor
    # and touches no other hand — which is exactly the conservative
    # propagation we want: if ANY line reachable this iteration priced
    # hand h with a fabricated number, h's regret at every node above is
    # untrustworthy, not just at the terminal where the fabrication
    # happened. `_mccfr_recurse` turns that into a skipped update. See
    # this milestone's entry in docs/milestones.md for why 0.5 was not
    # safe to keep once the same code path started serving the preflop
    # multiway solver's much higher fabrication rate.
    value = equity_vector * node.pot - node.invested[traverser]

    # M115: replace raw showdown equity with a SOLVED continuation value
    # wherever one exists for this spot.
    #
    # This is the fix M98 identified and M100 failed to achieve with a
    # heuristic. The pricing above is exactly right for an all-in — the
    # hand really does end at showdown — and wrong for every smaller bet,
    # which is why the tree over-prefers the all-in (M98) and why the
    # fold/play boundary cannot move with position (M111). One cause, two
    # symptoms.
    #
    # Applied ONLY where chips remain behind, for the same reason: an
    # all-in terminal is already correct, and "correcting" it would move
    # the very baseline the defect is measured against.
    #
    # Falls back to equity pricing per-hand when the table has no entry —
    # a missing spot must not become a fabricated number, which is the
    # failure this codebase's `trained` flags exist to expose.
    #
    # **M115's verdict: this does NOT fix the defect. Default is None.**
    # Paired across 5 seeds (same seed both arms, so seed variance
    # cancels), 6-max at 12,000 iterations:
    #
    #     AA jam delta      +0.019 +/- 0.201   fell in 2/5
    #     fold spread delta +1.23pp +/- 1.91pp  widened in 4/5
    #
    # Both null. A single seed had shown AA's jam falling 0.4955 ->
    # 0.3078 with a monotone positional gradient, which looked like the
    # fix working; M110 had already measured 12k jam varying 0.37-0.92
    # across seeds, so that difference was inside known noise.
    #
    # M98's diagnosis of the CAUSE still stands — it is read from this
    # very expression, and M99 confirmed the mechanism postflop with a
    # monotone 10.2pp effect. What fails is this particular correction.
    # The likeliest reason is the approximation `continuation_key` makes
    # and documents: it carries SPR and live-seat count but NOT range
    # strength, so a three-bet pot and a limped pot at the same SPR get
    # the same continuation value, and their ranges are nothing alike.
    # M114 named adding a range-strength dimension as the first thing to
    # try if validation failed. It failed; that is the next thing to try,
    # not more boards and not more iterations.
    if continuation_table is not None and stack_bb is not None:
        chips_behind = stack_bb - max(node.invested.values())
        if chips_behind > 0:
            key = _continuation_key(node.pot, chips_behind, len(live))
            spot = continuation_table.get(key)
            if spot:
                labels = getattr(equity_cache, "hands", None)
                if labels is not None and len(labels) == num_hands:
                    solved = np.array(
                        [spot.get(str(hand), np.nan) for hand in labels], dtype=float
                    )
                    replace = np.isfinite(solved)
                    value = np.where(
                        replace, solved * node.pot - node.invested[traverser], value
                    )

    # M100: an optional continuation value for showdown terminals that
    # still have money behind. Default 0.0 — off, byte-identical to M99.
    #
    # M98 found the defect this exists to test: `equity * pot - invested`
    # prices an ALL-IN correctly, because an all-in really does end at
    # showdown, while every smaller bet is scored as if the hand stopped
    # there — discarding the postflop game that is most of a raise's
    # value. So the tree's only correctly-priced action is the all-in,
    # and it over-prefers it by exactly that gap.
    #
    # The term below is a deliberately crude stand-in for the missing
    # game: a hand with above-break-even equity also captures a share of
    # the chips still behind, proportional to its edge.
    #
    #     value += continuation * (equity - 1/n_live) * chips_behind
    #
    # It is NOT a solve and is not claimed to be one. It exists to answer
    # whether restoring *any* continuation value removes the jam bias —
    # i.e. whether M98's diagnosis is sufficient, not just consistent.
    # Real continuation values mean chaining a flop, which is the
    # architectural work this is meant to justify or rule out first.
    #
    # **M100's answer: it does NOT validate the approach.** Swept at
    # c = 0 / 0.25 / 0.5 / 1.0, three seeds each, AA's all-in frequency:
    #
    #     budget    c=0     c=0.25   c=0.5    c=1.0
    #     12,000    0.615   0.208    0.417    0.374
    #      3,000    0.061   0.112    0.287    0.010
    #
    # Non-monotone at BOTH budgets. A term capturing a real mechanism
    # should move the number in one direction as it is turned up; this
    # goes down, up, down. The `c=1.0 @ 3,000` cell is the trap: 0.010
    # +/- 0.005, an order of magnitude tighter than any other arm, and it
    # is easy to read as the fix. It is not — a large bonus for keeping
    # chips behind simply makes the all-in DOMINATED, so the policy goes
    # purely "never jam" and reports a stable near-zero, below the ~0.031
    # reference. That is hitting the target by making the action
    # unattractive, not by modelling what follows it.
    #
    # A PAIRED 9-seed test settles it (both arms on the same seed, so the
    # per-seed difference cancels seed variance): c=0 vs c=0.25 at 12,000
    # iterations gives a paired delta of **-0.060 +/- 0.137, falling in
    # 5 of 9** — a coin flip. The term does not reduce the jam.
    #
    # The lesson worth keeping: **this knob can produce any number, so
    # matching the reference does not validate it.** Do not tune `c` to
    # make output look right. If continuation value is ever done
    # properly, it has to come from solved flop values, not a linear
    # edge-times-stack stand-in.
    #
    # Two properties that make it a fair test rather than a thumb on the
    # scale. It applies ONLY where chips remain, so an all-in terminal is
    # untouched — that asymmetry IS the defect, so the correction has to
    # be asymmetric in the same place. And it is zero-sum by construction
    # whenever stacks are equal (equities sum to 1 across n live players,
    # so the per-player terms sum to `(1 - n * 1/n) * behind == 0`),
    # which is exactly the preflop case; it does not quietly inject or
    # destroy chips.
    if continuation and stack_bb is not None:
        chips_behind = stack_bb - node.invested[traverser]
        if chips_behind > 0:
            value = value + continuation * (equity_vector - 1.0 / len(live)) * chips_behind
    return value


def _sample_chance_card(board: tuple, opponent_live_hands: tuple, rng) -> Card:
    """Uniformly samples one card not already on `board` and not held by
    any of `opponent_live_hands` (M32) — the MCCFR-appropriate sibling of
    `_sample_opponent_hands`'s opponent-hand sampling and `_mccfr_recurse`'s
    own opponent-action sampling, but deliberately shaped differently from
    both, for reasons worth stating rather than leaving implicit:

    No `EXPLORATION_EPSILON`-style floor: that floor exists because
    `current_strategy()` is a *learned* policy that can legitimately
    converge to an exact 0/1 split, making a rare-but-relevant *action*
    permanently unreachable under naive sampling. A chance card's
    distribution is fixed, uniform, exogenous "nature" randomness — the
    physical shuffle — identical on iteration 1 and iteration 1,000,000,
    with no analogous collapse risk to guard against. Mixing a uniform
    distribution with a uniform floor would be a mathematical no-op
    anyway, not a safety margin.

    No `MAX_OPPONENT_RESAMPLE_ATTEMPTS`-style reject-and-resample loop:
    that mechanism exists because *multiple, interdependent* opponent
    draws from a class-based pool can conflict with each other and need
    retrying to find any feasible joint assignment. Card sampling here is
    the opposite shape — excluding every known-conflicting card from the
    pool *before* drawing makes the single draw correct on the first
    attempt, always, by construction; there is nothing to retry.
    """
    excluded = set(board)
    for combo in opponent_live_hands:
        excluded.update(combo.cards)
    deck = remaining_deck(excluded)
    if not deck:
        raise ValueError(f"no cards remain to deal a chance card (board={board}, excluded {len(excluded)} cards)")
    return rng.choice(deck)


def _mccfr_recurse(
    node,
    traverser: str,
    opponent_hands: dict,
    reach: np.ndarray,
    node_data: dict,
    num_hands: int,
    hand_index: dict,
    equity_cache,
    rng,
    board: Optional[tuple] = None,
    chance_fn: Optional[Callable] = None,
    chance_data: Optional[dict] = None,
    strategy_weight: float = 1.0,
    floor_regret: bool = True,
    optimism: float = 0.0,
    smoothing: float = 0.0,
    continuation: float = 0.0,
    stack_bb: float | None = None,
    continuation_table: dict | None = None,
) -> np.ndarray:
    """Returns the traverser's payoff vector (length num_hands) from this
    node onward, given the fixed `opponent_hands` for this iteration.

    `board`/`chance_fn`/`chance_data` (M32): mirrors `_solve_recurse`'s
    own parameters of the same names/roles, with one necessary divergence.
    `_solve_recurse` AVERAGES over every branch of a `ChanceNode` every
    iteration (correct there — the exact solver visits the whole tree
    exhaustively anyway). MCCFR SAMPLES exactly one card via
    `_sample_chance_card`, using this same iteration's own seeded `rng`
    (the same "same seed -> same result" determinism story every other
    sampling decision in this module already honors), and recurses into
    only that one card's own lazily-built subtree (`chance.
    build_mccfr_chance_branch`, memoized in `chance_data` by
    `(id(node), card)` — not just `id(node)` alone, since a different
    iteration's sampled card for the *same* terminal needs its own,
    separately-memoized branch).

    The dispatch gate (`chance_fn is not None and node.is_showdown and
    traverser not in node.folded`) necessarily differs from
    `_solve_recurse`'s single `is_showdown` check: `_solve_recurse`
    values both positions in one pass, so `is_showdown` alone is the
    right and only gate there. MCCFR is traverser-vectorized — a terminal
    where the traverser has already folded has a value that's already
    fixed regardless of any further street (folding ends that position's
    stake), so dispatching a chance branch there would be wasted work.
    (A terminal where the traverser is live but has no remaining live
    OPPONENT can't actually arise here: `is_showdown` already requires
    >=2 live positions, and a live traverser is one of them, so at least
    one other live position is algebraically guaranteed whenever both
    conditions hold — proven once, in a comment at the call site, rather
    than re-checked on every dispatch.) `is_showdown` is kept explicit
    anyway for direct visual symmetry with `_solve_recurse`'s own gate,
    even though `traverser not in node.folded` alone, combined with
    `_mccfr_terminal_value`'s own early-return shape, would already rule
    out the same cases.

    Double-dispatch prevention is structural, not incidental, at two
    points: `build_mccfr_chance_branch` sets a branch's own `chance_fn` to
    `None` inside the same `if remaining_stack == 0` block that decides
    `root` (an all-in-already branch can never be re-dispatched); and the
    recursive call into a branch's own subtree always passes `chance_fn=
    branch.chance_fn` (never the ambient `chance_fn` parameter) — mirrors
    `_solve_recurse`'s own M12 pattern exactly. Since M32 never populates
    a branch's own `chance_fn` (one hop only — flop->turn, not chained to
    river), every terminal reached inside a turn-level subtree falls
    straight through to `_mccfr_terminal_value`, with no possibility of a
    second dispatch.
    """
    if isinstance(node, TerminalNode):
        if chance_fn is not None and node.is_showdown and traverser not in node.folded:
            # No separate `if other_live:` guard needed here (an earlier
            # draft had one): is_showdown means >=2 positions are live
            # (invested minus folded), and traverser not in node.folded
            # means the traverser is one of them — so at least one *other*
            # live position is algebraically guaranteed to exist. Checked,
            # not assumed: proven once here rather than re-verified at
            # runtime on every dispatch, since re-deriving a provably-
            # always-true condition on a hot path buys nothing (unlike
            # e.g. query_strategy_from_path's own explicit RuntimeError,
            # which guards a genuinely separate assumption that could, in
            # principle, drift out of sync with its own precondition).
            live = [p for p in node.invested if p not in node.folded]
            other_live = [p for p in live if p != traverser]
            opponent_live_hands = tuple(opponent_hands[p] for p in other_live)
            card = _sample_chance_card(board, opponent_live_hands, rng)
            key = (id(node), card)
            branch = chance_data.get(key)
            if branch is None:
                branch = chance_fn(node, card)
                chance_data[key] = branch
            return _mccfr_recurse(
                branch.root, traverser, opponent_hands, reach, node_data, num_hands, hand_index,
                branch.equity_cache, rng,
                board=branch.board, chance_fn=branch.chance_fn, chance_data=chance_data,
                strategy_weight=strategy_weight, floor_regret=floor_regret, optimism=optimism,
                smoothing=smoothing, continuation=continuation, stack_bb=stack_bb,
                continuation_table=continuation_table,
            )
        return _mccfr_terminal_value(node, traverser, opponent_hands, num_hands, equity_cache,
                                    continuation=continuation, stack_bb=stack_bb,
                                    continuation_table=continuation_table)

    actions = node.legal_actions
    table = node_data.setdefault(id(node), InfoSetTable.zeros(num_hands, len(actions)))
    strategy = table.current_strategy(optimism, smoothing)
    if smoothing:
        # What this node actually played, for the next visit's blend.
        # Conditional for the same memory reason as `last_regret` below.
        table.last_strategy = strategy

    if node.player_to_act == traverser:
        # Own decision: explore every action exhaustively, vectorized
        # over all of the traverser's possible hands.
        child_values = [
            _mccfr_recurse(
                node.children[action],
                traverser,
                opponent_hands,
                reach * strategy[:, a_idx],
                node_data,
                num_hands,
                hand_index,
                equity_cache,
                rng,
                board=board,
                chance_fn=chance_fn,
                chance_data=chance_data,
                strategy_weight=strategy_weight,
                floor_regret=floor_regret,
                optimism=optimism,
                smoothing=smoothing,
                continuation=continuation,
                stack_bb=stack_bb,
                continuation_table=continuation_table,
            )
            for a_idx, action in enumerate(actions)
        ]
        cf_action_values = np.stack(child_values, axis=1)  # (num_hands, num_actions)
        node_value = np.einsum("ha,ha->h", strategy, cf_action_values)

        # No opponent-reach contraction needed here (unlike the exact
        # path) — opponent_hands is a single fixed sample, not a
        # distribution to marginalize over; that marginalization happens
        # implicitly across many iterations instead, via the natural
        # sampling frequency (combo_weight-proportional).
        # M66: skip hands whose value this iteration is fabricated rather
        # than simulated. `node_value[h]` is NaN iff some reachable
        # terminal priced hand h with a fallback (see
        # `_mccfr_terminal_value`) — note this catches it even when
        # `strategy[h, a] == 0`, since 0 * NaN is still NaN, so a tainted
        # action cannot hide behind a zero weight.
        #
        # Skipping is NOT equivalent to feeding a neutral value. CFR+
        # floors regret at 0 and never lets it decrease, so a fabricated
        # value biased in one direction ratchets: each iteration adds a
        # little more regret for the same action and none of it can ever
        # be undone. Contributing nothing leaves regret_sum untouched, so
        # such a hand learns only from the iterations where it had a real
        # value — the honest amount of information available. It also
        # keeps strategy_sum at 0 for a hand that never once had a real
        # value, so `InfoSetTable.trained_mask()` (M28) reports it as
        # untrained rather than confidently wrong.
        #
        # SCOPE, stated so nobody re-derives a wrong conclusion from this
        # code: M27 proposed this change to fix 6-max preflop divergence
        # (AKs's UTG fold rate climbing 15.6% -> 48.7% -> 92.4% at
        # 300/3k/30k iterations). M66 built it and measured it — it does
        # NOT fix that. The cause there was the API's multiway preflop hand
        # pool being 48.6% premium by combo weight, not this update rule
        # (M67 replaced that pool with all 169 classes). This change is
        # here on its own merits (correctness and an honest trained_mask),
        # measured behaviour-neutral where answers are well-determined.
        valid = np.isfinite(node_value)
        regret = np.where(valid[:, None], cf_action_values - node_value[:, None], 0.0)
        # M71: the CFR+ floor is optional. It discards NEGATIVE regret
        # entirely while accumulating positive regret, which is a ratchet
        # on whichever action's value estimate is noisiest — under
        # sampling that is the all-in, whose payoff swings a whole stack.
        # See mccfr_solve's `floor_regret` / `discount`.
        table.regret_sum = (
            np.maximum(table.regret_sum + regret, 0.0) if floor_regret
            else table.regret_sum + regret
        )
        # M97: predictive regret matching's one-step prediction, kept ONLY
        # when something is going to read it.
        #
        # The tidier design stores it always, so a table's history doesn't
        # depend on the flags. It costs too much to justify for a feature
        # that is off by default: `last_regret` and `last_strategy`
        # together are two more (num_hands, num_actions) arrays on top of
        # `regret_sum` and `strategy_sum`, which exactly DOUBLES
        # `node_data` — the largest structure any solve produces, and the
        # one M93 had just finished putting a ceiling on. Paying 2x memory
        # on every solve to keep a field consistent for a mode nobody
        # enables is the wrong trade.
        if optimism:
            table.last_regret = regret
        # M69: `strategy_weight` scales this iteration's contribution to
        # the time-average. At 1.0 every iteration counts equally — which
        # lets iteration 1's untrained, exactly-uniform current_strategy()
        # weigh as much as iteration 12,000's converged one. See
        # mccfr_solve's `linear_averaging`.
        table.strategy_sum += strategy_weight * np.where(
            valid[:, None], reach[:, None] * strategy, 0.0
        )
        return node_value

    # Opponent's decision: sample one action from their current strategy,
    # blended with a small exploration floor so no action's sampling
    # probability is ever *exactly* zero. This matters more than it might
    # look: CFR+'s regret flooring can legitimately push current_strategy()
    # to an exact 0/1 split even when the true equilibrium wants a small
    # residual frequency (e.g. "call 0.2% of the time"). Sampling from
    # that degenerate strategy directly makes the rare-but-consequential
    # branch permanently unreachable, which silently starves the
    # traverser's value estimate of it.
    #
    # We deliberately do NOT apply an importance-sampling correction here
    # (true_prob/sampling_prob) — an earlier version did, and it's the
    # textbook-unbiased choice, but it compounds *multiplicatively* across
    # every nested opponent decision on the path to a terminal (e.g. SB's
    # node then BB's node at 3-max), which produced wild-variance value
    # estimates (empirically, single-iteration outliers of -70+ on a pot
    # where the real range is roughly ±100 but should be tightly clustered
    # given typical opponent strategies). CFR+'s regret flooring makes
    # this actively destructive, not just noisy: one such outlier can wipe
    # out many iterations' worth of accumulated positive regret in a
    # single step (negative regret is floored to 0, discarding it, so
    # recovery has to rebuild from scratch) — the exact "gets worse with
    # more iterations" signature diagnosed during M8 (see the PR/commit).
    # Sampling directly from the floored strategy with weight 1 instead
    # treats that floored strategy as the opponent's actual policy for
    # this iteration, trading textbook unbiasedness for a small, bounded,
    # non-compounding bias (proportional to EXPLORATION_EPSILON, not to
    # tree depth) — verified empirically to fix convergence at N=3 without
    # regressing the N=2 exact-solver cross-validation.
    opponent_hand_idx = hand_index[opponent_hands[node.player_to_act]]
    true_probs = strategy[opponent_hand_idx]
    num_actions = len(actions)
    sampling_probs = (1.0 - EXPLORATION_EPSILON) * true_probs + EXPLORATION_EPSILON / num_actions
    sampled_idx = rng.choices(range(num_actions), weights=sampling_probs.tolist())[0]
    sampled_action = actions[sampled_idx]

    return _mccfr_recurse(
        node.children[sampled_action],
        traverser,
        opponent_hands,
        reach,
        node_data,
        num_hands,
        hand_index,
        equity_cache,
        rng,
        board=board,
        chance_fn=chance_fn,
        chance_data=chance_data,
        strategy_weight=strategy_weight,
        floor_regret=floor_regret,
        optimism=optimism,
        smoothing=smoothing,
        continuation=continuation,
        stack_bb=stack_bb,
        continuation_table=continuation_table,
    )


def _opponent_hands_are_dealable(hands: list) -> bool:
    """True if `hands` (one per live opponent this iteration) could all be
    dealt simultaneously — i.e. no two of them physically conflict.

    Dispatches on hand type (mirrors this file's own existing
    `isinstance(node, ChanceNode)`/`isinstance(node, TerminalNode)` style,
    not duck-typing — `StartingHand` and `HandCombo` have fully disjoint
    attribute sets, so either would work, but `isinstance` is the file's
    established idiom): `StartingHand` (preflop's 169-class abstraction,
    every existing call site until M32) delegates to `deal_n_hands`'s own
    combo-level search — a *class* pair can be mutually feasible or not
    depending on which underlying combos are still available, which only
    `deal_n_hands`'s search actually resolves. `HandCombo` (M32, postflop)
    needs no search at all — combos already *are* concrete cards, so a
    plain pairwise "any card repeated?" check is exact, not an
    approximation, and far cheaper than routing through `deal_n_hands`
    (which expects `StartingHand`-only attributes and would raise
    `AttributeError` on a `HandCombo` — confirmed by direct execution
    during M32's own design, not assumed).
    """
    if not hands:
        return True
    if isinstance(hands[0], HandCombo):
        seen: set = set()
        for combo in hands:
            for card in combo.cards:
                if card in seen:
                    return False
                seen.add(card)
        return True
    try:
        deal_n_hands(hands)
    except RuntimeError:
        return False
    return True


def _sample_opponent_hands(
    positions: tuple,
    traverser: str,
    position_weights: dict,
    hands: list,
    rng,
) -> dict:
    """Draws one hand per non-traverser position, each from THAT
    position's own weight vector (`position_weights[position]`) — not a
    single shared distribution — retrying up to
    MAX_OPPONENT_RESAMPLE_ATTEMPTS times whenever the joint draw is
    physically infeasible (`_opponent_hands_are_dealable` returns False;
    see MAX_OPPONENT_RESAMPLE_ATTEMPTS's own module-level docstring for
    why this rejection-resampling exists, and why exhausting every
    attempt is left to proceed with the last (infeasible) draw rather
    than raising or hanging — unchanged behavior, just relocated here).

    Extracted from `mccfr_solve`'s own loop body specifically so it can
    be tested directly and deterministically against a real per-position
    weight vector, without needing to reverse-engineer sampling behavior
    from a full solve's aggregate regret/strategy output.
    """
    candidate_hands: dict = {}
    for _ in range(MAX_OPPONENT_RESAMPLE_ATTEMPTS):
        candidate_hands = {
            position: rng.choices(hands, weights=position_weights[position].tolist())[0]
            for position in positions
            if position != traverser
        }
        if _opponent_hands_are_dealable(list(candidate_hands.values())):
            return candidate_hands
        # opponents mutually incompatible — resample the whole draw
    return candidate_hands


def mccfr_solve(
    root: DecisionNode,
    hands: list,
    positions: tuple,
    equity_cache,
    iterations: int,
    seed: int = 0,
    initial_reach: dict | None = None,
    board: tuple | None = None,
    chance_fn: Optional[Callable] = None,
    chance_data: Optional[dict] = None,
    linear_averaging: bool = True,
    floor_regret: bool = False,
    discount: tuple | None = None,
    optimism: float = 0.0,
    smoothing: float = 0.0,
    continuation: float = 0.0,
    stack_bb: float | None = None,
    continuation_table: dict | None = None,
) -> dict:
    """Run `iterations` of External-Sampling MCCFR over `root`.

    `positions` is the acting order (matches GameConfig.positions); the
    traverser cycles through it, one per iteration. `equity_cache` should
    be constructed with the same `hands` list (see
    equity.MultiwayEquityCache) so indices line up.

    `floor_regret` (M71, default **False** — i.e. plain CFR regret
    matching, NOT CFR+) controls whether accumulated regret is clamped at
    zero. CFR+'s clamp is a genuine win in the exact heads-up solver
    (which still uses it — `_solve_recurse` is untouched), but under
    SAMPLING it is a ratchet: it discards negative regret while
    accumulating positive regret, so whichever action's value estimate is
    noisiest collects spurious positive regret that can never be
    cancelled. The all-in is by far the noisiest action, since its payoff
    swings an entire stack, which is exactly where the bias showed up.

    Measured at 6-max over 169 classes, 3,000 iterations, 3 seeds each —
    AA's jam frequency, against a trusted heads-up reference of ~3.1%:

        floor=True   0.211 / 0.203 / 0.182   (mean 0.199)
        floor=False  0.034 / 0.033 / 0.029   (mean 0.032)

    T7s's UTG fold rate moves 0.744 -> 0.938 over the same runs. 3-max at
    its 12,000-iteration budget agrees (AA jam 0.468 -> 0.120).

    **The exception, measured not assumed:** plain CFR converges more
    slowly than CFR+, so it needs enough iterations per position to get
    there. 9-max at 3,000 iterations gives each of nine seats only 333
    traversals and is too under-trained to benefit — AA's jam goes the
    wrong way (0.777 -> 0.982) even as T7s's fold improves (0.203 ->
    0.349). `api/config.py` therefore keeps 9-max on the CFR+ clamp
    explicitly, and says so.

    Published Discounted CFR was tried too and was worse than plain CFR
    here: DCFR(1.5, 0) gave AA jam 0.139 and DCFR(1.5, 0.5) gave 0.103,
    against plain CFR's 0.034, at roughly twice the cost. `discount=
    (alpha, beta)` remains available but is not used by default.

    `optimism` and `smoothing` (M97, both default 0.0 = off) are the two
    shapes of policy damping M74 prescribed for the 6-max jam
    oscillation. **Both measured worse than doing nothing**, so both stay
    off; see `InfoSetTable.current_strategy` for the mechanisms and for
    why each fails. Measured at 6-max, 3,000 iterations, three seeds, a
    FRESH equity cache per run, against a heads-up AA-jam reference of
    ~0.031. **Correction (M98): this is NOT the shipped operating point,
    as M97 originally claimed.** Every arm constructed
    `MultiwayEquityCache(hands=...)` without `samples`, so all three ran
    at the engine default of 200 where the API ships
    `MULTIWAY_PREFLOP_SAMPLES = 50`. The arms are still comparable to
    each other — the mistake was identical across them — but the label
    was wrong:

        arm                  AA jam                  mean   spread  cost
        plain                0.036 / 0.073 / 0.058   0.056   0.037  262s
        optimism=1.0         0.562 / 0.963 / 0.359   0.628   0.604  258s
        smoothing=0.9        0.141 / 0.172 / 0.732   0.348   0.591  275s

    Plain wins on level and on stability, at the same cost. Note the
    third smoothed seed: the first two (0.141, 0.172) looked like a tight
    distribution and the third was 0.732. **Do not read a trend off two
    seeds here** — that mistake has now been made three times in this
    codebase (M73's exploration floor, M80's bias scare, this).

    Raising the budget does not rescue either: at 12,000 iterations every
    arm sits at 0.40-0.61 mean, an order of magnitude above the
    reference, and damping narrows the seed spread without moving the
    level. That is itself informative — if the 12,000-iteration answer
    were purely a cycle sampled at a random phase, damping should pull
    the mean toward the cycle's average. It does not, which points at the
    question being asked (the equity model, the pool) rather than at the
    policy dynamics, and is where the next attempt should look.

    `linear_averaging` (M69, default True) weights iteration t's
    contribution to the time-averaged strategy by t, rather than counting
    every iteration equally. Standard CFR+ practice, and it matters here
    more than usual: `current_strategy()` returns an EXACTLY uniform
    1/num_actions before any regret accumulates, so with equal weighting
    a long run's average stays contaminated by the untrained opening
    iterations. That contamination was visible in real output — a 6-max
    169-class solve at 12,000 iterations had AA jamming 25% and calling
    25%, two actions sitting at almost exactly the uniform 0.25.
    Measured, at identical cost (the change is one scalar multiply):

        iters   weighting   AA jam   T7s UTG fold
         3,000  equal        0.33     0.66
         3,000  linear       0.26     0.78
        12,000  equal        0.25     0.87
        12,000  linear       0.20     0.94

    Every figure moves toward the truth, and linear at 3,000 beats equal
    at 3,000 by about as much as quadrupling the iterations would.
    **It does not fully fix the sizing axis** — AA still jams 20% where a
    converged solve is near 3% — so this is a real improvement on a known
    problem, not a resolution of it. Pass False to recover the exact
    pre-M69 behaviour.

    `initial_reach` (M31) optionally maps position -> a weight array
    (same length/order as `hands`), overriding the default combo_weight-
    derived prior for THAT position — mirrors `solve()`'s own parameter
    of the same name/shape/semantics exactly, deliberately, so a caller
    who already understands one already understands the other. A
    position missing from `initial_reach` (or the default `None`,
    meaning "no overrides at all") falls back to combo_weight, so every
    pre-M31 call site is unaffected — proven, not just argued: see
    test_mccfr_solve_default_initial_reach_matches_explicit_combo_weight.
    Used for BOTH sides of sampling, not just one: the traverser's own
    `reach` (their belief over their own hand, seeded once per iteration)
    is drawn from their own weight vector, AND each opponent's hand is
    independently sampled from THEIR OWN weight vector (previously every
    position — traverser and opponent alike — drew from one shared
    global combo_weight prior regardless of position; see
    docs/full-table-diagnostic-2026-08.md's §4, which named this among
    true multiway postflop solving's prerequisites). Raises `ValueError`
    upfront, before any solving happens, for a wrong-length weight vector
    or one that sums to zero (that position would have no possible hand
    to ever be sampled as, whether traversing or acting as an opponent).

    Zero real callers today (as of M31) — `solver.py`'s own multiway
    dispatch always solves a full preflop tree from its root, where every
    position's prior is legitimately uniform combo_weight — shipped as a
    standalone, tested capability ahead of its real consumer, matching
    this project's own M17-then-M18/M19-then-M20 precedent. Its real use
    is a future milestone: seeding real per-position ranges (e.g. from
    derive_ranges_from_path, already N-player-general per M16) into a
    genuine multiway postflop MCCFR solve.

    `board`/`chance_fn`/`chance_data` (M32) close the two prerequisites
    `initial_reach`'s own M31 docstring named as still-blocking: a
    board-aware, per-chance-branch equity source (multiway_board_equity.
    py's NwayBoardEquityCache, M30) is now threaded through this
    function's terminal-value computation via plain duck typing (see
    `_mccfr_terminal_value`'s own docstring — nothing about its signature
    changed), and a chance-branch SAMPLING case (as opposed to
    `_solve_recurse`'s own AVERAGING one) now exists in this module's own
    recursion — see `_mccfr_recurse`'s docstring for the full mechanics.
    Mirrors `solve()`'s own `chance_fn`/`chance_data` parameter names
    exactly; `board` (the current street's community cards) has no
    `solve()` analog, required because MCCFR must *sample* a specific
    next card rather than dispatch to a pre-built exhaustive structure.
    `chance_fn is not None` requires `board` to also be supplied (raises
    `ValueError` upfront otherwise); `chance_data` defaults to a fresh
    `{}` when `chance_fn` is set and none is supplied, mirroring `solve()`
    exactly. All three default to `None`, and every pre-M32 call site
    omits them — purely additive, same guarantee `solve()`'s own M12
    `chance_fn`/`chance_data` addition made.

    Returns a dict of {id(DecisionNode): InfoSetTable} — the same shape
    `solve()` returns, so downstream code (StrategyResult etc.) doesn't
    need to know which path produced it.

    Unlike `solve()`, this is only deterministic given a fixed `seed`
    ("same seed -> same result"), not unconditionally — opponent hands
    and actions are genuinely sampled. Each iteration's opponent-hand
    draw is resampled (up to MAX_OPPONENT_RESAMPLE_ATTEMPTS times) until
    it's physically dealable, so — unlike a version without this fix —
    the number of `rng` draws consumed per iteration isn't fixed; a
    given `seed` still reproduces the same result, just not one whose
    per-iteration `rng` consumption can be reasoned about independent of
    `hands`' own composition.
    """
    if chance_fn is not None and board is None:
        raise ValueError("chance_fn requires board (the current street's community cards) to also be supplied")
    if chance_fn is not None and chance_data is None:
        chance_data = {}

    num_hands = len(hands)
    hand_index = {hand: i for i, hand in enumerate(hands)}
    initial_reach = initial_reach or {}

    def _default_weights():
        # Lazy, not eager — mirrors solve()'s own identical reasoning:
        # `hands` may someday be combos.HandCombo (a future postflop
        # MCCFR consumer), which has no combo_weight at all. Only ever
        # called for a position genuinely missing its own initial_reach
        # entry, so a caller supplying every position's own real range
        # never touches this.
        return np.array([hand.combo_weight for hand in hands], dtype=float)

    position_weights: dict = {}
    for position in positions:
        weights = (
            np.asarray(initial_reach[position], dtype=float)
            if position in initial_reach
            else _default_weights()
        )
        if weights.shape != (num_hands,):
            raise ValueError(
                f"initial_reach[{position!r}] has shape {weights.shape}, "
                f"expected ({num_hands},) to match len(hands)"
            )
        if float(weights.sum()) <= 0:
            raise ValueError(
                f"initial_reach[{position!r}] sums to zero — position "
                f"{position!r} would have no possible hand to ever be "
                "sampled as, whether traversing or acting as an opponent"
            )
        position_weights[position] = weights

    rng = random.Random(seed)
    node_data: dict = {}
    for iteration in range(iterations):
        traverser = positions[iteration % len(positions)]
        opponent_hands = _sample_opponent_hands(positions, traverser, position_weights, hands, rng)
        reach = position_weights[traverser].copy()
        _mccfr_recurse(
            root, traverser, opponent_hands, reach, node_data, num_hands, hand_index, equity_cache, rng,
            board=board, chance_fn=chance_fn, chance_data=chance_data,
            strategy_weight=float(iteration + 1) if linear_averaging else 1.0,
            floor_regret=floor_regret,
            optimism=optimism,
            smoothing=smoothing,
            continuation=continuation,
            stack_bb=stack_bb,
            continuation_table=continuation_table,
        )
        if discount is not None:
            # Discounted CFR: shrink accumulated regret toward zero each
            # iteration, positives and negatives at different rates. The
            # point is that early regret — accumulated while every
            # strategy in the tree was still untrained — should not weigh
            # as much as recent regret.
            alpha, beta = discount
            step = iteration + 1
            positive_scale = step ** alpha / (step ** alpha + 1.0)
            negative_scale = step ** beta / (step ** beta + 1.0)
            for table in node_data.values():
                accumulated = table.regret_sum
                table.regret_sum = np.where(
                    accumulated > 0.0,
                    accumulated * positive_scale,
                    accumulated * negative_scale,
                )
    return node_data
