"""Card abstraction: bucketing strategically-similar combos.HandCombo
together, so postflop solving can eventually operate over far fewer
units than the full exact-combo pool (M17 — Phase 1 of the real-time-
speed roadmap in CLAUDE.md's v3 vision section).

Sits strictly downstream of both combos.py (combo pool membership) and
board_equity.py (the equity signal bucketing is built from) — the same
reason board_equity.py itself was split out of equity.py (avoiding a
circular import), and the analogous "bridge" role combos.py already
plays between 169-class StartingHands and concrete combos: this module
is the bridge between exact combos and bucketed combos.

**Deliberately a standalone primitive, not wired into solve_flop/
solve_flop_turn/solve_flop_to_river/cfr.solve() yet.** Wiring straight
into live solving would stack three unknowns at once — the bucketing
signal's fidelity, the bucket-count/accuracy tradeoff, and whether
CFR's own equilibrium-finding behaves sanely over lossy bucket-
aggregated input — with no measurement checkpoint between them. A prior
attempt this session at a *different* speed optimization (batching
board_equity's hand-evaluation calls across matchups) was implemented
before being measured and turned out to deliver almost nothing, once
profiled — a cheap, self-contained mistake to discover and discard. A
bad bucketing choice wired straight into solve_flop wouldn't be cheap to
discover: it wouldn't fail loudly, it would silently produce wrong
strategy output from a function real callers already treat as
authoritative. So this module ships as a measured primitive first
(mirrors M10's combos.py/board_equity.py shipping before M11 wired them
into a real solve) — wiring it into live solving is deliberately a
follow-up milestone, scoped from this module's own real numbers.

The bucketing signal: each combo's mean same-board equity against every
*other* combo in its own pool (build_board_equity_table's row, nanmean'd
over the non-NaN entries), binned into equal-frequency buckets. Using
the *same* pool as both the combo being bucketed and the yardstick is
deliberate, not an approximation to fix: what matters for bucketing a
specific hero/villain matchup is relative strength *within that pool*
(the same pool solve_flop would itself build an equity table over), not
against some external, differently-composed reference range. A richer
signal — accounting for equity variance/potential across future
runouts, distinguishing a made hand from a same-mean-equity draw — is
explicitly deferred, the same "prove flop before turn" discipline M12
applied before M13's turn-to-river chaining: ship the simple scalar-mean
version, measure its real accuracy loss (bucket_equity_error), let a
future milestone's actual numbers decide if more is needed.
"""

import random
import warnings
from dataclasses import dataclass

import numpy as np

from .board_equity import DEFAULT_BOARD_EQUITY_SAMPLES, DEFAULT_SEED, build_board_equity_table
from .combos import HandCombo


@dataclass(frozen=True)
class HandBucket:
    """One cluster of combos.HandCombo treated as strategically
    interchangeable for a given board.

    `bucket_id` matches this bucket's position in a BucketedPool's
    `buckets` list — which is also its row/col index in a bucket-vs-
    bucket equity table (the same "one canonical order, reused
    everywhere" convention build_board_equity_table's own `combos`
    argument already establishes). `members` is never empty — an empty
    bucket would mean build_hand_buckets' binning step has a bug, not a
    valid bucket. `weight` is the sum of member combos' own pool weights
    (from the range dict a caller supplies, e.g. solve_flop's
    hero_range/villain_range convention) — not member *count*, which
    matters once a bucket is used in a weighted equity aggregate (see
    build_bucket_equity_table). `strength` is the mean of member combos'
    own per-combo signal values (see compute_combo_strengths).
    """

    bucket_id: int
    members: tuple
    weight: float
    strength: float

    def __str__(self) -> str:
        # str(hand) is the load-bearing contract cfr.solve()/
        # StrategyResult.strategy_at already rely on for every hand type
        # (HandCombo, StartingHand) as their output-dict key — a bucket
        # needs the same. bucket_id alone guarantees uniqueness within
        # one BucketedPool (hence within one StrategyResult.hands); n=/
        # strength= make it actually legible, not just unique.
        return f"bucket{self.bucket_id}(n={len(self.members)}, strength={self.strength:.3f})"


@dataclass(frozen=True)
class BucketedPool:
    """`buckets`: list[HandBucket], ordered by ascending `strength` (so
    bucket 0 is the weakest on this board). `combo_to_bucket`: dict
    mapping every bucketed (i.e. not board-blocked) HandCombo to its
    bucket_id — built once here, at the point build_hand_buckets already
    knows the assignment, rather than re-derived by a caller later.

    `source_combos`/`equity_table` are the combo list (in the order
    `equity_table`'s rows/cols use) and the raw combo-level equity table
    this pool was bucketed from — carried here, not just handed back
    once and discarded, specifically so build_bucket_equity_table/
    bucket_equity_error don't each need it re-passed (and potentially
    mismatched) or rebuilt a second time at real cost; they're the exact
    same pairing `compute_combo_strengths` already returned internally.
    """

    buckets: list
    combo_to_bucket: dict
    source_combos: list
    equity_table: np.ndarray


def compute_combo_strengths(
    board: tuple,
    combos: list,
    samples: int = DEFAULT_BOARD_EQUITY_SAMPLES,
    rng: random.Random | None = None,
) -> tuple:
    """Per-combo scalar strength signal: combos[i]'s mean equity against
    every other unblocked combo in `combos` on `board` — nanmean of
    build_board_equity_table's row i (ignoring the diagonal/blocked NaN
    entries).

    Returns (strengths, equity_table): `strengths` is an ndarray shape
    (N,), NaN for any combo entirely blocked by the board (nanmean of an
    all-NaN row) — mirrors build_board_equity_table's own "blocked combo
    stays undefined, not silently 0" convention. `equity_table` is the
    raw N x N table this was derived from, returned rather than
    discarded so downstream callers (build_hand_buckets,
    build_bucket_equity_table) reuse it instead of rebuilding it.
    """
    rng = rng if rng is not None else random.Random(DEFAULT_SEED)
    equity_table = build_board_equity_table(board, combos, samples=samples, rng=rng)
    with warnings.catch_warnings():
        # nanmean on an all-NaN row (a board-blocked combo) is exactly
        # the intended "stays undefined" outcome, not a real warning —
        # suppressed rather than triggering "Mean of empty slice" noise
        # for a case this function documents as expected.
        warnings.simplefilter("ignore", category=RuntimeWarning)
        strengths = np.nanmean(equity_table, axis=1)
    return strengths, equity_table


def build_hand_buckets(
    board: tuple,
    combo_weights: dict,
    num_buckets: int,
    samples: int = DEFAULT_BOARD_EQUITY_SAMPLES,
    rng: random.Random | None = None,
) -> BucketedPool:
    """Buckets `combo_weights` (dict[HandCombo, float], same convention
    as solve_flop's hero_range/villain_range) into `num_buckets` equal-
    frequency (quantile) bins by compute_combo_strengths' signal.

    A combo entirely blocked by `board` (NaN strength) is dropped from
    bucketing entirely, not placed in a bucket — a caller looking it up
    in the returned BucketedPool.combo_to_bucket gets a KeyError, not a
    silently wrong bucket 0, mirroring build_board_equity_table's own
    "blocked combo has no valid row/col" treatment.

    Raises ValueError if `num_buckets < 1`, or if `num_buckets` exceeds
    the number of unblocked combos (every bucket must be non-empty, per
    HandBucket's own contract — there can't be more non-empty buckets
    than combos to put in them).
    """
    if num_buckets < 1:
        raise ValueError(f"num_buckets must be at least 1, got {num_buckets}")

    combos = sorted(combo_weights.keys(), key=str)
    strengths, equity_table = compute_combo_strengths(board, combos, samples=samples, rng=rng)

    unblocked = [(i, combo) for i, combo in enumerate(combos) if not np.isnan(strengths[i])]
    if num_buckets > len(unblocked):
        raise ValueError(
            f"num_buckets ({num_buckets}) exceeds the number of unblocked combos ({len(unblocked)})"
        )

    unblocked_sorted = sorted(unblocked, key=lambda item: strengths[item[0]])
    groups = np.array_split(np.arange(len(unblocked_sorted)), num_buckets)

    buckets = []
    combo_to_bucket = {}
    for bucket_id, positions in enumerate(groups):
        members = tuple(unblocked_sorted[pos][1] for pos in positions)
        member_strengths = [strengths[unblocked_sorted[pos][0]] for pos in positions]
        weight = sum(combo_weights[member] for member in members)
        buckets.append(
            HandBucket(bucket_id=bucket_id, members=members, weight=weight, strength=float(np.mean(member_strengths)))
        )
        for member in members:
            combo_to_bucket[member] = bucket_id

    return BucketedPool(
        buckets=buckets, combo_to_bucket=combo_to_bucket, source_combos=combos, equity_table=equity_table
    )


def build_bucket_equity_table(bucketed_pool: BucketedPool, combo_weights: dict) -> np.ndarray:
    """B x B bucket-vs-bucket equity table, same NaN-for-undefined
    convention as build_board_equity_table (same-bucket entries, i==j,
    are NaN — a bucket can't play against itself as two distinct hands).

    table[i, j] is the weight-weighted average, over every (member_a in
    bucket i, member_b in bucket j) pair, of `bucketed_pool.equity_table`'s
    corresponding combo-level entry — weighted by each *member's own*
    `combo_weights` weight (not member count), so a bucket dominated by
    one high-weight combo stays correctly dominated in the aggregate,
    not diluted by low-weight members.

    A case build_board_equity_table never had to handle, since it only
    ever compared one combo against one other: *partial* blocking within
    a bucket pair (some member pairs share a card with each other, other
    member pairs don't). A blocked member pair is excluded from the
    weighted average individually — not NaN-ing the whole cell, which
    would throw away real signal from every non-conflicting member pair.
    Only a bucket pair where *every* member pair is blocked (or the pool
    is otherwise degenerate) stays NaN.

    Reads `bucketed_pool.source_combos`/`.equity_table` — the exact
    table/list build_hand_buckets already built this pool from — rather
    than taking them as separate parameters a caller could accidentally
    pass mismatched, or pay to rebuild.
    """
    equity_table = bucketed_pool.equity_table
    combo_index = {combo: i for i, combo in enumerate(bucketed_pool.source_combos)}
    b = len(bucketed_pool.buckets)
    table = np.full((b, b), np.nan, dtype=float)

    for bi in range(b):
        bucket_i = bucketed_pool.buckets[bi]
        for bj in range(bi + 1, b):
            bucket_j = bucketed_pool.buckets[bj]
            weighted_sum = 0.0
            weight_total = 0.0
            for member_a in bucket_i.members:
                weight_a = combo_weights[member_a]
                idx_a = combo_index[member_a]
                for member_b in bucket_j.members:
                    value = equity_table[idx_a, combo_index[member_b]]
                    if np.isnan(value):
                        continue  # this specific member pair is blocked — excluded, not zeroed
                    pair_weight = weight_a * combo_weights[member_b]
                    weighted_sum += value * pair_weight
                    weight_total += pair_weight

            if weight_total > 0:
                equity_i = weighted_sum / weight_total
                table[bi, bj] = equity_i
                table[bj, bi] = 1.0 - equity_i

    return table


def bucket_equity_error(bucketed_pool: BucketedPool, bucket_equity_table: np.ndarray) -> dict:
    """Quantifies accuracy loss: for every valid (unblocked, distinct,
    bucketed) combo pair (i, j) in `bucketed_pool.source_combos`,
    compares `bucketed_pool.equity_table[i, j]` (ground truth) against
    `bucket_equity_table[bucket_of(i), bucket_of(j)]` (the bucket-
    approximated value that combo pair would actually see once solving
    operates at bucket granularity).

    Returns {'mean_absolute_error', 'max_absolute_error',
    'pairs_compared'} — mean absolute error is the headline number for a
    cost/accuracy write-up, max absolute error flags whether the
    approximation has an occasional bad outlier even when the mean looks
    fine, the same "report the extremes, not just the average" instinct
    M10's own two-number (23-combo/78-combo) cost write-up already
    models. All three are 0 if there's nothing to compare (e.g. every
    combo blocked).
    """
    original_combos = bucketed_pool.source_combos
    equity_table = bucketed_pool.equity_table
    errors = []
    n = len(original_combos)
    for i in range(n):
        combo_i = original_combos[i]
        bucket_i = bucketed_pool.combo_to_bucket.get(combo_i)
        if bucket_i is None:
            continue  # board-blocked, never bucketed
        for j in range(i + 1, n):
            combo_j = original_combos[j]
            bucket_j = bucketed_pool.combo_to_bucket.get(combo_j)
            if bucket_j is None:
                continue
            true_equity = equity_table[i, j]
            if np.isnan(true_equity):
                continue  # combo_i/combo_j share a card — no ground truth to compare against
            approx_equity = bucket_equity_table[bucket_i, bucket_j]
            if np.isnan(approx_equity):
                # same bucket, or every member pair between these two
                # buckets happens to be blocked — no bucket-level
                # approximation exists for this pair.
                continue
            errors.append(abs(true_equity - approx_equity))

    if not errors:
        return {"mean_absolute_error": 0.0, "max_absolute_error": 0.0, "pairs_compared": 0}
    errors_array = np.array(errors)
    return {
        "mean_absolute_error": float(errors_array.mean()),
        "max_absolute_error": float(errors_array.max()),
        "pairs_compared": len(errors),
    }


def bucket_reach_vector(bucketed_pool: BucketedPool, range_dict: dict) -> np.ndarray:
    """Per-bucket reach weight, in `bucketed_pool.buckets` order —
    bucket i's value is `sum(range_dict.get(combo, 0.0) for combo in
    bucket.members)`.

    Deliberately NOT `HandBucket.weight` (built from whatever single
    `combo_weights` dict `build_hand_buckets` was called with — see
    solver.py's `solve_flop_abstracted` for why that's a different,
    independent number: a bucket built over a *combined* hero+villain
    pool can contain combos that are "mostly hero's" and combos that
    are "mostly villain's," so one aggregate weight can't serve as
    either side's own reach vector).

    Uses `.get(combo, 0.0)`, not `range_dict[combo]` — a bucket's
    members can include combos entirely absent from this specific
    `range_dict` (e.g. villain's own combos, when computing hero's own
    reach vector over their combined pool) — the same "missing combo
    gets 0 weight for that position, not an error" convention
    `solve_flop` already established.
    """
    return np.array(
        [sum(range_dict.get(combo, 0.0) for combo in bucket.members) for bucket in bucketed_pool.buckets]
    )
