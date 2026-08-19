import numpy as np
import pytest

from poker_solver.abstraction import (
    BucketedPool,
    HandBucket,
    build_bucket_equity_table,
    build_hand_buckets,
    bucket_equity_error,
    compute_combo_strengths,
)
from poker_solver.board_equity import build_board_equity_table
from poker_solver.cards import Card
from poker_solver.combos import HandCombo


def cards(text: str) -> list:
    return [Card.from_str(token) for token in text.split()]


# ---------------------------------------------------------------------------
# A complete (river) board, so equity is exact — no RNG involved anywhere in
# this fixture, same trick test_board_equity.py's own river tests use. Four
# combos with an unambiguous, known-by-construction strength hierarchy:
# trips > two pair > pair > high card.
# ---------------------------------------------------------------------------

_BOARD = tuple(cards("2c 7d 9h Kc 4d"))
_TRIPS = HandCombo(*cards("2h 2d"))  # trip deuces
_TWO_PAIR = HandCombo(*cards("7c 4h"))  # two pair, 77/44
_PAIR = HandCombo(*cards("9c 5h"))  # pair of nines
_HIGH_CARD = HandCombo(*cards("Th Jh"))  # no pair at all
_KNOWN_COMBOS = [_TRIPS, _TWO_PAIR, _PAIR, _HIGH_CARD]


def test_hand_verifiable_bucketing_splits_by_known_strength():
    weights = {combo: 1.0 for combo in _KNOWN_COMBOS}
    pool = build_hand_buckets(_BOARD, weights, num_buckets=2)

    assert len(pool.buckets) == 2
    weak_bucket, strong_bucket = pool.buckets  # ordered ascending by strength
    assert set(weak_bucket.members) == {_HIGH_CARD, _PAIR}
    assert set(strong_bucket.members) == {_TWO_PAIR, _TRIPS}
    assert weak_bucket.strength < strong_bucket.strength

    assert pool.combo_to_bucket[_HIGH_CARD] == weak_bucket.bucket_id
    assert pool.combo_to_bucket[_PAIR] == weak_bucket.bucket_id
    assert pool.combo_to_bucket[_TWO_PAIR] == strong_bucket.bucket_id
    assert pool.combo_to_bucket[_TRIPS] == strong_bucket.bucket_id

    assert weak_bucket.weight == pytest.approx(2.0)
    assert strong_bucket.weight == pytest.approx(2.0)


def test_hand_verifiable_bucket_equity_table_matches_expected_values():
    weights = {combo: 1.0 for combo in _KNOWN_COMBOS}
    pool = build_hand_buckets(_BOARD, weights, num_buckets=2)
    bucket_table = build_bucket_equity_table(pool, weights)

    weak_id = pool.combo_to_bucket[_HIGH_CARD]
    strong_id = pool.combo_to_bucket[_TRIPS]
    # Every member of the strong bucket (trips, two pair) beats every
    # member of the weak bucket (pair, high card) outright on this board —
    # the weighted average across all 4 cross-bucket pairs is exactly 1.0.
    assert bucket_table[strong_id, weak_id] == pytest.approx(1.0)
    assert bucket_table[weak_id, strong_id] == pytest.approx(0.0)
    assert np.isnan(bucket_table[weak_id, weak_id])
    assert np.isnan(bucket_table[strong_id, strong_id])


# ---------------------------------------------------------------------------
# compute_combo_strengths
# ---------------------------------------------------------------------------


def test_compute_combo_strengths_nan_for_a_board_blocked_combo():
    # A third, unblocked combo is required: with only two combos, the one
    # unblocked combo's only possible comparison point would be the
    # blocked one — itself undefined — so its own mean would wrongly come
    # out NaN too, not because of a bug but because there'd be nothing
    # valid left for it to average.
    board = tuple(cards("2c 7d 9h"))
    blocked = HandCombo(Card("2", "c"), Card("Q", "h"))  # 2c is already on the board
    other_a = HandCombo(*cards("Ah Ad"))
    other_b = HandCombo(*cards("Kh Kd"))
    strengths, _ = compute_combo_strengths(board, [blocked, other_a, other_b])
    assert np.isnan(strengths[0])
    assert not np.isnan(strengths[1])
    assert not np.isnan(strengths[2])


def test_compute_combo_strengths_deterministic_given_a_seed():
    import random

    board = tuple(cards("2c 7d 9h"))
    combos = [HandCombo(*cards("Ah Ad")), HandCombo(*cards("Kh Kd")), HandCombo(*cards("3h 4d"))]
    s1, _ = compute_combo_strengths(board, combos, samples=50, rng=random.Random(1))
    s2, _ = compute_combo_strengths(board, combos, samples=50, rng=random.Random(1))
    assert np.array_equal(s1, s2, equal_nan=True)


# ---------------------------------------------------------------------------
# build_hand_buckets
# ---------------------------------------------------------------------------


def test_build_hand_buckets_total_weight_is_conserved():
    weights = {_TRIPS: 1.0, _TWO_PAIR: 0.5, _PAIR: 2.0, _HIGH_CARD: 0.25}
    pool = build_hand_buckets(_BOARD, weights, num_buckets=2)
    assert sum(bucket.weight for bucket in pool.buckets) == pytest.approx(sum(weights.values()))


def test_build_hand_buckets_rejects_fewer_than_one_bucket():
    weights = {combo: 1.0 for combo in _KNOWN_COMBOS}
    with pytest.raises(ValueError):
        build_hand_buckets(_BOARD, weights, num_buckets=0)


def test_build_hand_buckets_rejects_more_buckets_than_unblocked_combos():
    weights = {combo: 1.0 for combo in _KNOWN_COMBOS}
    with pytest.raises(ValueError):
        build_hand_buckets(_BOARD, weights, num_buckets=5)


def test_build_hand_buckets_excludes_a_board_blocked_combo_entirely():
    blocked = HandCombo(Card("2", "c"), Card("Q", "h"))  # 2c is already on the board
    weights = {combo: 1.0 for combo in _KNOWN_COMBOS}
    weights[blocked] = 1.0
    pool = build_hand_buckets(_BOARD, weights, num_buckets=2)
    assert blocked not in pool.combo_to_bucket
    assert all(blocked not in bucket.members for bucket in pool.buckets)


def test_build_hand_buckets_orders_buckets_by_ascending_strength():
    weights = {combo: 1.0 for combo in _KNOWN_COMBOS}
    pool = build_hand_buckets(_BOARD, weights, num_buckets=4)
    strengths = [bucket.strength for bucket in pool.buckets]
    assert strengths == sorted(strengths)


def test_build_hand_buckets_combo_to_bucket_covers_every_unblocked_member_once():
    weights = {combo: 1.0 for combo in _KNOWN_COMBOS}
    pool = build_hand_buckets(_BOARD, weights, num_buckets=2)
    all_members = [member for bucket in pool.buckets for member in bucket.members]
    assert sorted(all_members, key=str) == sorted(_KNOWN_COMBOS, key=str)
    assert set(pool.combo_to_bucket.keys()) == set(_KNOWN_COMBOS)


def test_build_hand_buckets_pool_carries_its_own_source_combos_and_equity_table():
    weights = {combo: 1.0 for combo in _KNOWN_COMBOS}
    pool = build_hand_buckets(_BOARD, weights, num_buckets=2)
    assert sorted(pool.source_combos, key=str) == sorted(_KNOWN_COMBOS, key=str)
    n = len(pool.source_combos)
    assert pool.equity_table.shape == (n, n)


# ---------------------------------------------------------------------------
# build_bucket_equity_table
# ---------------------------------------------------------------------------


def test_build_bucket_equity_table_partial_blocking_within_a_bucket_pair():
    # Bucket 0 has two members (a1, a2); bucket 1 has two members (b1, b2).
    # Exactly one cross-bucket pair — (a1, b1) — shares a card (both hold
    # Ah) and is therefore blocked; the other three cross pairs are valid.
    # The cell must still be defined — a correctly-weighted average of
    # those 3 valid pairs — not NaN from treating any blocking as
    # poisoning the whole cell.
    a1 = HandCombo(*cards("Ah Ad"))
    a2 = HandCombo(*cards("Kh Kd"))
    b1 = HandCombo(*cards("Ah Qc"))  # shares Ah with a1 — this cross pair is blocked
    b2 = HandCombo(*cards("Qh Jh"))
    board = tuple(cards("2c 7d 9s"))
    combos = [a1, a2, b1, b2]
    weights = {c: 2.0 for c in combos}  # non-uniform-from-1.0 weight, so the average isn't trivially unweighted

    equity_table = build_board_equity_table(board, combos, samples=200)
    assert np.isnan(equity_table[combos.index(a1), combos.index(b1)])  # confirms a1/b1 really are blocked

    bucket_0 = HandBucket(bucket_id=0, members=(a1, a2), weight=4.0, strength=0.9)
    bucket_1 = HandBucket(bucket_id=1, members=(b1, b2), weight=4.0, strength=0.1)
    pool = BucketedPool(
        buckets=[bucket_0, bucket_1],
        combo_to_bucket={a1: 0, a2: 0, b1: 1, b2: 1},
        source_combos=combos,
        equity_table=equity_table,
    )

    bucket_table = build_bucket_equity_table(pool, weights)

    # Hand-recompute the expected cell directly from equity_table's own
    # values, using only the 3 valid cross pairs — this is the aggregation
    # logic under test, independent of what board_equity's sampled values
    # happen to be.
    valid_pairs = [(a1, b2), (a2, b1), (a2, b2)]
    weighted_sum = sum(
        equity_table[combos.index(x), combos.index(y)] * weights[x] * weights[y] for x, y in valid_pairs
    )
    weight_total = sum(weights[x] * weights[y] for x, y in valid_pairs)
    expected = weighted_sum / weight_total

    assert not np.isnan(bucket_table[0, 1])
    assert bucket_table[0, 1] == pytest.approx(expected)
    assert bucket_table[1, 0] == pytest.approx(1.0 - expected)


def test_build_bucket_equity_table_nan_when_every_member_pair_is_blocked():
    # A degenerate case: bucket 0 and bucket 1 each have one member, and
    # those two members share a card — every (and the only) cross pair is
    # blocked, so the cell has no valid pairs to average and stays NaN.
    a = HandCombo(*cards("Ah Ad"))
    b = HandCombo(*cards("Ah Qc"))  # shares Ah with a
    board = tuple(cards("2c 7d 9s"))
    combos = [a, b]
    weights = {a: 1.0, b: 1.0}
    equity_table = build_board_equity_table(board, combos, samples=50)
    pool = BucketedPool(
        buckets=[
            HandBucket(bucket_id=0, members=(a,), weight=1.0, strength=0.9),
            HandBucket(bucket_id=1, members=(b,), weight=1.0, strength=0.1),
        ],
        combo_to_bucket={a: 0, b: 1},
        source_combos=combos,
        equity_table=equity_table,
    )

    bucket_table = build_bucket_equity_table(pool, weights)
    assert np.isnan(bucket_table[0, 1])
    assert np.isnan(bucket_table[1, 0])


def test_build_bucket_equity_table_diagonal_is_nan():
    weights = {combo: 1.0 for combo in _KNOWN_COMBOS}
    pool = build_hand_buckets(_BOARD, weights, num_buckets=2)
    bucket_table = build_bucket_equity_table(pool, weights)
    n = len(pool.buckets)
    for i in range(n):
        assert np.isnan(bucket_table[i, i])


def test_build_bucket_equity_table_is_symmetric():
    weights = {combo: 1.0 for combo in _KNOWN_COMBOS}
    pool = build_hand_buckets(_BOARD, weights, num_buckets=2)
    bucket_table = build_bucket_equity_table(pool, weights)
    n = len(pool.buckets)
    for i in range(n):
        for j in range(n):
            if i != j:
                assert bucket_table[i, j] + bucket_table[j, i] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# bucket_equity_error
# ---------------------------------------------------------------------------


def test_bucket_equity_error_is_zero_for_a_lossless_one_combo_per_bucket():
    weights = {combo: 1.0 for combo in _KNOWN_COMBOS}
    pool = build_hand_buckets(_BOARD, weights, num_buckets=len(_KNOWN_COMBOS))
    bucket_table = build_bucket_equity_table(pool, weights)

    error = bucket_equity_error(pool, bucket_table)
    assert error["mean_absolute_error"] == pytest.approx(0.0)
    assert error["max_absolute_error"] == pytest.approx(0.0)
    assert error["pairs_compared"] == 6  # C(4, 2)


def test_bucket_equity_error_is_nonzero_for_a_genuinely_lossy_bucketing():
    # _KNOWN_COMBOS' clean total-ordering hierarchy (trips > two pair >
    # pair > high card) turns out to bucket losslessly even at
    # num_buckets=2 on a complete board — every member of the "strong"
    # bucket beats every member of the "weak" bucket uniformly, so there's
    # no within-bucket variance for aggregation to lose. A wider, more
    # varied pool (flop board, draws mixed with made hands, so different
    # members of the same bucket don't uniformly beat/lose to the same
    # opponents) is needed to exercise genuine lossiness.
    board = tuple(cards("7h 2d 9c"))
    combos = [
        HandCombo(*cards("7s 7c")),  # set of sevens
        HandCombo(*cards("9d 9h")),  # set of nines
        HandCombo(*cards("Jd Th")),  # gutshot, no pair
        HandCombo(*cards("Ks Qd")),  # complete air
        HandCombo(*cards("Ah Kh")),  # complete air, different blockers
        HandCombo(*cards("6d 5d")),  # open-ended draw, no pair
    ]
    weights = {combo: 1.0 for combo in combos}
    pool = build_hand_buckets(board, weights, num_buckets=2, samples=300)
    bucket_table = build_bucket_equity_table(pool, weights)

    error = bucket_equity_error(pool, bucket_table)
    assert error["mean_absolute_error"] > 0.0
    assert error["mean_absolute_error"] <= error["max_absolute_error"]
