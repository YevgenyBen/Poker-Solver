import numpy as np
import pytest

from poker_solver.equity import MultiwayEquityCache, build_equity_table
from poker_solver.game_tree import GameConfig, build_game_tree
from poker_solver.solver import DEFAULT_ITERATIONS, StrategyResult, format_opening_range_grid, solve_preflop
from poker_solver.starting_hands import StartingHand

# A tiny hand set + freshly-built (small, fast) equity table, so these
# structural tests never touch the slow-to-build-the-first-time full
# 169x169 cached table.
_SMALL_HANDS = [StartingHand("A", "A"), StartingHand("K", "K"), StartingHand("7", "2", suited=False)]
_SMALL_EQUITY_TABLE = build_equity_table(hands=_SMALL_HANDS, samples=30)


# ---------------------------------------------------------------------------
# Structural correctness (small config + small hand set: fast, no
# dependency on the full 169x169 cached equity table).
# ---------------------------------------------------------------------------


def test_opening_range_covers_every_hand():
    config = GameConfig(raise_sizes=(), max_raises=1)
    result = solve_preflop(iterations=20, config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE)
    opening = result.opening_range()
    assert set(opening.keys()) == {str(hand) for hand in _SMALL_HANDS}


def test_opening_range_frequencies_sum_to_one():
    config = GameConfig(raise_sizes=(), max_raises=1)
    result = solve_preflop(iterations=20, config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE)
    opening = result.opening_range()
    for freqs in opening.values():
        assert freqs
        assert not any(np.isnan(freq) for freq in freqs.values())
        assert pytest.approx(sum(freqs.values()), abs=1e-9) == 1.0


def test_solve_preflop_records_elapsed_time_and_iterations():
    config = GameConfig(raise_sizes=(), max_raises=1)
    result = solve_preflop(iterations=17, config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE)
    assert result.iterations == 17
    assert result.elapsed_seconds >= 0.0


def test_custom_config_overrides_stack_bb_default():
    config = GameConfig(stack_bb=40.0, small_blind=0.5, big_blind=1.0, raise_sizes=(), max_raises=1)
    result = solve_preflop(iterations=10, config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE)
    assert result.config.stack_bb == 40.0


def test_format_opening_range_grid_runs_without_error():
    config = GameConfig(raise_sizes=(), max_raises=1)
    result = solve_preflop(iterations=10, config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE)
    text = format_opening_range_grid(result)
    assert "BTN opening range" in text
    assert "AA" in text


def test_strategy_at_falls_back_to_uniform_for_an_unvisited_node():
    # MCCFR (unlike the exact HU solver) only visits nodes actually
    # reached along a sampled/traversed path — a StrategyResult with
    # empty node_data simulates a node that solving never touched (e.g.
    # a low-probability combination of earlier actions never got
    # sampled within the iteration budget). strategy_at must fall back
    # to a uniform strategy, not raise KeyError — this was a real M9
    # bug, caught by the nine_max_result fixture's low iteration budget.
    config = GameConfig(raise_sizes=(), max_raises=1)
    root = build_game_tree(config)
    result = StrategyResult(
        config=config, root=root, hands=_SMALL_HANDS, node_data={}, iterations=0, elapsed_seconds=0.0
    )
    strategy = result.strategy_at(root)
    num_actions = len(root.legal_actions)
    for freqs in strategy.values():
        assert len(freqs) == num_actions
        assert pytest.approx(sum(freqs.values()), abs=1e-9) == 1.0
        for freq in freqs.values():
            assert freq == pytest.approx(1.0 / num_actions)


# ---------------------------------------------------------------------------
# Directional GTO sanity checks against known heads-up preflop intuition.
# Full 169-hand solve, backed by the real (cached) preflop equity table —
# the equity table build is a one-time cost per machine (see equity.py);
# once cached, this is fast.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def deep_stack_result():
    config = GameConfig()  # default: 100bb, standard blinds, max_raises=4
    return solve_preflop(iterations=DEFAULT_ITERATIONS, config=config)


def test_btn_opens_very_wide_at_100bb(deep_stack_result):
    opening = deep_stack_result.opening_range()
    non_fold_count = sum(1 for freqs in opening.values() if freqs.get("fold", 0.0) < 0.5)
    # BTN is the sole opener in heads-up with no one else to act first —
    # deep-stacked BTN should be opening (raising or at least not
    # folding) the clear majority of hands.
    assert non_fold_count > len(opening) * 0.6


def test_premium_hands_almost_never_fold(deep_stack_result):
    opening = deep_stack_result.opening_range()
    for label in ["AA", "KK", "AKs"]:
        assert opening[label]["fold"] < 0.05


def test_weakest_hands_fold_far_more_than_premium_hands(deep_stack_result):
    opening = deep_stack_result.opening_range()
    assert opening["72o"]["fold"] > opening["AA"]["fold"]
    assert opening["32o"]["fold"] > opening["AKs"]["fold"]


# ---------------------------------------------------------------------------
# M8 deliverable: a real 3-handed (3-max) preflop solve through the full
# pipeline (game_tree + equity + MCCFR + solver), not a toy/stub. Uses a
# small hand subset (not the full 169) to stay fast — MCCFR's lazy
# per-matchup equity cache means a full 169-hand 3-handed solve pays a
# real, non-trivial cost per *distinct* opponent-hand combination
# touched (measured during M8: several seconds each, at the default
# 200-sample precision), and a full solve can touch many combinations —
# a real scaling concern flagged for M9, not something this milestone's
# test suite should have to pay for on every run.
#
# The hand subset is deliberately NOT just "a few premium hands + two
# trash hands": an earlier version used 4 pocket pairs (AA/KK/QQ/JJ) out
# of 8 total hands, making pairs ~47% of the opponent pool by combo
# weight — wildly unrepresentative of real poker (where pairs are a small
# fraction of any range) and enough to make hands like AKs and QQ face a
# genuinely tougher-than-real-life field. That was diagnosed empirically
# during M8 (measured AKs's true average all-in equity against that pool
# at ~0.34 — a real number, not a bug) and traded for a broader pool with
# fewer pairs and real trash hands, so "premium hands rarely fold" is a
# meaningful assertion again for the hands it's checked against. Iteration
# count is set high enough (see EXPLORATION_EPSILON's docstring in cfr.py
# for why MCCFR needs more iterations than it used to) for AA/KK to
# reliably converge near their true near-zero fold frequency — confirmed
# empirically to be stable, not still trending, at this count.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def three_max_result():
    small_hands = [
        StartingHand("A", "A"),
        StartingHand("K", "K"),
        StartingHand("A", "K", suited=True),
        StartingHand("Q", "Q"),
        StartingHand("A", "K", suited=False),
        StartingHand("T", "9", suited=False),
        StartingHand("7", "2", suited=False),
        StartingHand("3", "2", suited=False),
    ]
    config = GameConfig(positions=("BTN", "SB", "BB"))
    equity_cache = MultiwayEquityCache(hands=small_hands, samples=200, seed=1)
    return solve_preflop(
        config=config, hands=small_hands, equity_cache=equity_cache, iterations=200_000, seed=1
    )


def test_three_max_solve_covers_every_hand(three_max_result):
    opening = three_max_result.opening_range()
    assert set(opening.keys()) == {str(hand) for hand in three_max_result.hands}


def test_three_max_solve_frequencies_sum_to_one(three_max_result):
    opening = three_max_result.opening_range()
    for freqs in opening.values():
        assert not any(np.isnan(freq) for freq in freqs.values())
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


def test_three_max_btn_premium_hands_rarely_fold(three_max_result):
    opening = three_max_result.opening_range()
    # AA/KK are unambiguously top-tier even against this pair-heavy pool
    # — both converge to a near-zero fold frequency, same directional
    # intuition as the HU test's tighter 0.05 bound, just with a looser
    # bound reflecting MCCFR's sampling noise vs. the exact HU solver.
    assert opening["AA"]["fold"] < 0.05
    assert opening["KK"]["fold"] < 0.15


def test_three_max_btn_weak_hands_fold_far_more_than_premium(three_max_result):
    opening = three_max_result.opening_range()
    assert opening["32o"]["fold"] > opening["AA"]["fold"]
    assert opening["72o"]["fold"] > opening["KK"]["fold"]
    assert opening["T9o"]["fold"] > opening["AKs"]["fold"]


def test_node_for_position_of_first_actor_is_the_root(three_max_result):
    assert three_max_result.node_for_position("BTN") is three_max_result.root


def test_node_for_position_matches_the_sb_facing_action_test_node(three_max_result):
    root = three_max_result.root
    raise_action = next(a for a in root.legal_actions if a.kind == "raise")
    # node_for_position walks call_or_check, not raise, so SB's "everyone
    # limped to me" node is a different (also real) decision node than
    # the "BTN raised" node test_three_max_sb_facing_action_is_a_real_decision
    # exercises — both should exist and be genuine SB decisions.
    sb_after_limp = three_max_result.node_for_position("SB")
    sb_after_raise = root.children[raise_action]
    assert sb_after_limp.player_to_act == "SB"
    assert sb_after_raise.player_to_act == "SB"
    assert sb_after_limp is not sb_after_raise


def test_node_for_position_unknown_position_raises(three_max_result):
    with pytest.raises(ValueError):
        three_max_result.node_for_position("UTG")


def test_strategy_for_position_matches_opening_range_for_first_actor(three_max_result):
    assert three_max_result.strategy_for_position("BTN") == three_max_result.opening_range()


def test_strategy_for_position_bb_is_well_formed(three_max_result):
    strategy = three_max_result.strategy_for_position("BB")
    for freqs in strategy.values():
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


def test_three_max_sb_facing_action_is_a_real_decision(three_max_result):
    # SB's node after BTN's opening raise should exist, be a real
    # multi-action decision, and produce well-formed frequencies —
    # exercises a second infoset beyond just the root, still using the
    # real 3-handed tree/equity/MCCFR pipeline end to end.
    root = three_max_result.root
    raise_action = next(a for a in root.legal_actions if a.kind == "raise")
    sb_node = root.children[raise_action]
    assert sb_node.player_to_act == "SB"
    strategy = three_max_result.strategy_at(sb_node)
    for freqs in strategy.values():
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


# ---------------------------------------------------------------------------
# M9 deliverable: real 6-max and 9-max preflop solves through the full
# pipeline, using the *default* max_raises=4 (not a reduced cap) — see
# poker_solver/game_tree.py's LazyChildren for how the betting tree stays
# buildable at these sizes (the eager tree explodes combinatorially with
# player count: measured at ~333K terminals for 6-max, into the tens of
# millions for 9-max, before laziness), and poker_solver/hand_eval.py's
# best_hand_rank_batch for how equity computation itself was sped up
# enough to make MCCFR at these player counts tractable at all.
#
# Same curated 8-hand pool as three_max_result above (see its comment for
# why it's not pair-heavy) — kept identical across table sizes so this
# file's own tests are each other's cross-check, not independent guesses.
#
# Iteration budgets differ sharply by table size, and that's not
# arbitrary: MultiwayEquityCache's cache-hit rate collapses as opponent
# count grows (the space of possible opponent-hand combinations is
# roughly hand_pool_size^opponent_count — small enough to reuse heavily
# at 3-max's 2 opponents, large enough at 9-max's 8 that a cache hit is
# rare regardless of how fast any single computation is). Measured during
# M9: 6-max reaches tight convergence at 30K iterations in ~2.5 minutes;
# at 9-max, per-iteration cost was too variable to safely budget a large
# count (some iterations touch far more distinct combinations than
# others), so it's capped at a much smaller, empirically-verified-
# reliable count. It's still genuinely multiway MCCFR (not a stub), just
# correspondingly noisier — its assertions below are looser for exactly
# that reason, not because the underlying solve is expected to be wrong.
# ---------------------------------------------------------------------------

_M9_HANDS = [
    StartingHand("A", "A"),
    StartingHand("K", "K"),
    StartingHand("A", "K", suited=True),
    StartingHand("Q", "Q"),
    StartingHand("A", "K", suited=False),
    StartingHand("T", "9", suited=False),
    StartingHand("7", "2", suited=False),
    StartingHand("3", "2", suited=False),
]


@pytest.fixture(scope="module")
def six_max_result():
    config = GameConfig(positions=("UTG", "MP", "CO", "BTN", "SB", "BB"))
    equity_cache = MultiwayEquityCache(hands=_M9_HANDS, samples=200, seed=1)
    return solve_preflop(config=config, hands=_M9_HANDS, equity_cache=equity_cache, iterations=30_000, seed=1)


def test_six_max_solve_covers_every_hand(six_max_result):
    opening = six_max_result.opening_range()
    assert set(opening.keys()) == {str(hand) for hand in _M9_HANDS}


def test_six_max_solve_frequencies_sum_to_one(six_max_result):
    opening = six_max_result.opening_range()
    for freqs in opening.values():
        assert not any(np.isnan(freq) for freq in freqs.values())
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


def test_six_max_utg_premium_hands_rarely_fold(six_max_result):
    # UTG opens tighter than BTN in real poker (more players left to act
    # behind), but AA/KK/AKs/QQ are still comfortably premium even from
    # first position — measured during M9 at 30K iterations: AA=0.000,
    # KK=0.007, AKs=0.012, QQ=0.020, tight enough for a strict bound.
    opening = six_max_result.opening_range()
    for label in ["AA", "KK", "AKs", "QQ"]:
        assert opening[label]["fold"] < 0.05


def test_six_max_utg_weak_hands_fold_far_more_than_premium(six_max_result):
    opening = six_max_result.opening_range()
    assert opening["72o"]["fold"] > opening["AA"]["fold"]
    assert opening["32o"]["fold"] > opening["KK"]["fold"]
    assert opening["T9o"]["fold"] > opening["AKs"]["fold"]


def test_six_max_strategy_for_position_bb_is_well_formed(six_max_result):
    strategy = six_max_result.strategy_for_position("BB")
    for freqs in strategy.values():
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


@pytest.fixture(scope="module")
def nine_max_result():
    config = GameConfig(positions=("UTG", "UTG1", "MP1", "MP2", "MP3", "CO", "BTN", "SB", "BB"))
    equity_cache = MultiwayEquityCache(hands=_M9_HANDS, samples=200, seed=1)
    return solve_preflop(config=config, hands=_M9_HANDS, equity_cache=equity_cache, iterations=300, seed=1)


def test_nine_max_solve_covers_every_hand(nine_max_result):
    opening = nine_max_result.opening_range()
    assert set(opening.keys()) == {str(hand) for hand in _M9_HANDS}


def test_nine_max_solve_frequencies_sum_to_one(nine_max_result):
    opening = nine_max_result.opening_range()
    for freqs in opening.values():
        assert not any(np.isnan(freq) for freq in freqs.values())
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


def test_nine_max_utg_aa_rarely_folds(nine_max_result):
    # Only AA is asserted tightly here — at 9-max's much smaller
    # iteration budget every other hand carries more sampling noise (see
    # this section's header comment), but AA folding meaningfully is
    # still a strong enough signal to be worth catching.
    opening = nine_max_result.opening_range()
    assert opening["AA"]["fold"] < 0.2


def test_nine_max_utg_weakest_hand_folds_far_more_than_aa(nine_max_result):
    opening = nine_max_result.opening_range()
    assert opening["32o"]["fold"] > opening["AA"]["fold"]


def test_nine_max_strategy_for_position_bb_is_well_formed(nine_max_result):
    strategy = nine_max_result.strategy_for_position("BB")
    for freqs in strategy.values():
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0
