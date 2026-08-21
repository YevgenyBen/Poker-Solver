import random

import numpy as np
import pytest

from poker_solver.abstraction import BucketedPool, HandBucket, build_hand_buckets
from poker_solver.cards import Card, remaining_deck
from poker_solver.cfr import InfoSetTable
from poker_solver.combos import HandCombo, combos_for_class, range_from_class_frequencies
from poker_solver.equity import MultiwayEquityCache, build_equity_table
from poker_solver.game_tree import (
    ALL_IN,
    CALL_OR_CHECK,
    FOLD,
    RAISE,
    Action,
    DecisionNode,
    GameConfig,
    TerminalNode,
    build_game_tree,
    postflop_action_order,
    walk,
)
from poker_solver.solver import (
    DEFAULT_ITERATIONS,
    FlopScenario,
    PathScenario,
    StrategyResult,
    derive_flop_scenario,
    derive_ranges_from_path,
    ensure_flop_turn_multiway_branch,
    ensure_mccfr_chance_branch,
    expand_bucket_strategy,
    format_opening_range_grid,
    solve_flop,
    solve_flop_abstracted,
    solve_flop_multiway,
    solve_flop_to_river,
    solve_flop_to_river_multiway,
    solve_flop_turn,
    solve_flop_turn_multiway,
    solve_preflop,
)
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


def test_trained_hands_is_false_for_an_unvisited_node():
    # The confidence-signal counterpart to the test above (M28,
    # docs/full-table-diagnostic-2026-08.md's SS3.3): strategy_at's
    # uniform fallback and trained_hands's False must agree — an
    # unvisited node's uniform numbers are the untrained default, not a
    # coincidentally-flat real strategy.
    config = GameConfig(raise_sizes=(), max_raises=1)
    root = build_game_tree(config)
    result = StrategyResult(
        config=config, root=root, hands=_SMALL_HANDS, node_data={}, iterations=0, elapsed_seconds=0.0
    )
    trained = result.trained_hands(root)
    assert set(trained.keys()) == {str(hand) for hand in _SMALL_HANDS}
    assert not any(trained.values())


def test_trained_hands_true_where_strategy_sum_was_actually_accumulated():
    config = GameConfig(raise_sizes=(), max_raises=1)
    result = solve_preflop(iterations=20, config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE)
    trained = result.trained_hands(result.root)
    # The exact HU solver visits the whole tree exhaustively every
    # iteration — every hand at the root should be trained.
    assert set(trained.keys()) == {str(hand) for hand in _SMALL_HANDS}
    assert all(trained.values())


def test_trained_for_position_matches_trained_hands_at_node_for_position():
    config = GameConfig(raise_sizes=(), max_raises=1)
    result = solve_preflop(iterations=20, config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE)
    position = result.root.player_to_act
    assert result.trained_for_position(position) == result.trained_hands(result.node_for_position(position))


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
    # iterations=300 (down from 30,000 pre-M27) matches api/main.py's own
    # MULTIWAY_TABLE_CONFIGS[6] — see its comment there, and CLAUDE.md's
    # M27 entry, for why: 30,000 iterations was believed since M9 to
    # reach good convergence, but turned out to expose a pre-existing
    # MCCFR instability at 6-max with this hand pool (a hand's fold rate
    # that should stabilize instead grows with more iterations). No
    # iteration count tested was fully stable, so this mirrors 9-max's
    # own already-conservative budget rather than a number specifically
    # validated as sufficient.
    config = GameConfig(positions=("UTG", "MP", "CO", "BTN", "SB", "BB"))
    equity_cache = MultiwayEquityCache(hands=_M9_HANDS, samples=200, seed=1)
    return solve_preflop(config=config, hands=_M9_HANDS, equity_cache=equity_cache, iterations=300, seed=1)


def test_six_max_solve_covers_every_hand(six_max_result):
    opening = six_max_result.opening_range()
    assert set(opening.keys()) == {str(hand) for hand in _M9_HANDS}


def test_six_max_solve_frequencies_sum_to_one(six_max_result):
    opening = six_max_result.opening_range()
    for freqs in opening.values():
        assert not any(np.isnan(freq) for freq in freqs.values())
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


def test_six_max_utg_aa_rarely_folds(six_max_result):
    # Only AA is asserted tightly here, mirroring 9-max's own
    # test_nine_max_utg_aa_rarely_folds pattern — before M27 this test
    # also tightly asserted KK/AKs/QQ (at the old 30,000-iteration
    # budget), but M27 found those specifically are NOT reliably stable
    # at 6-max with this hand pool (a pre-existing MCCFR convergence
    # sensitivity, not something the iteration-budget cut alone fixes —
    # see CLAUDE.md's M27 entry and api/main.py's MULTIWAY_TABLE_CONFIGS
    # comment). AA held up consistently across seeds during that
    # investigation, unlike the others, so it's the one hand still worth
    # a strict bound.
    opening = six_max_result.opening_range()
    assert opening["AA"]["fold"] < 0.05


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


def test_nine_max_bb_has_genuinely_untrained_hands(nine_max_result):
    # The exact scenario M28 exists to surface (docs/full-table-
    # diagnostic-2026-08.md's SS3.3): at 9-max's small iteration budget,
    # a deep position reached only via many earlier players' actions
    # first genuinely has hands MCCFR never sampled there at all — not
    # a hypothetical, a real property of this fixture's own result.
    trained = nine_max_result.trained_for_position("BB")
    assert set(trained.keys()) == {str(hand) for hand in _M9_HANDS}
    assert not all(trained.values())


# ---------------------------------------------------------------------------
# M11 deliverable: a real flop-only preflop-to-postflop handoff through
# the full pipeline (combos + board_equity + StreetConfig/build_street_tree
# + cfr.solve's generalizations), not a toy/stub. Reuses cfr.py's exact
# tensor solver (same shape as heads-up preflop), so a small curated
# combo pool per side stays fast — board_equity.py's own module comment
# has the measured O(N^2) cost this is deliberately staying well under.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def small_flop_result():
    # A deliberately shallow (15bb) effective stack, not the earlier
    # ~97bb draft of this fixture: at very deep stacks relative to the
    # pot, the *only* sane play with a hero range this small/polarized
    # is an immediate shove, which collapses the tree to little more
    # than a shove-or-not decision and makes facing-a-raise assertions
    # meaningless (any real hand and any air both end up folding to a
    # massive overbet almost regardless of relative strength — verified
    # empirically while writing this fixture, not assumed). At a normal
    # stack-to-pot ratio, "raise:7.50" is a real mid-sized value bet a
    # villain can meaningfully call-or-fold with, which is what the
    # directional test below actually needs.
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    hero_range = {
        HandCombo(Card("7", "s"), Card("7", "c")): 1.0,  # flopped a set of sevens
        HandCombo(Card("K", "s"), Card("Q", "d")): 1.0,  # complete air, no pair or draw
    }
    villain_range = {
        HandCombo(Card("9", "d"), Card("8", "d")): 1.0,  # top pair + a straight draw
        HandCombo(Card("Q", "c"), Card("5", "c")): 1.0,  # pure air, no pair or draw
    }
    return solve_flop(
        board=board,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=10.0,
        effective_stack_bb=15.0,
        positions=("OOP", "IP"),
        raise_sizes=(0.75, 2.5),
        max_raises=3,
        iterations=3000,
        equity_samples=300,
        equity_seed=1,
    )


def test_solve_flop_covers_the_union_of_both_ranges(small_flop_result):
    opening = small_flop_result.opening_range()
    expected = {"7s7c", "KsQd", "9d8d", "Qc5c"}
    assert set(opening.keys()) == expected


def test_solve_flop_frequencies_sum_to_one(small_flop_result):
    opening = small_flop_result.opening_range()
    for freqs in opening.values():
        assert not any(np.isnan(freq) for freq in freqs.values())
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


def test_solve_flop_a_real_hand_folds_to_a_bet_far_less_than_air(small_flop_result):
    # Walk from the root through OOP's raise action to reach IP's
    # facing-a-bet decision, and compare IP's own two hands there:
    # 9d8d (top pair + a straight draw, a real calling hand) should
    # fold far less than Qc5c (pure air, no pair or draw) facing the
    # same bet — the classic "value continues, air folds" pattern, and
    # much more robust than comparing bet/raise frequency *at the
    # opening node itself*, where a genuinely polarized 2-hand hero
    # range (nuts + air, nothing in between) can legitimately shove
    # both extremes at similar rates under a real Nash equilibrium
    # (verified empirically while writing this test — not a solver bug,
    # just the wrong node to assert this particular fact at).
    root = small_flop_result.root
    raise_action = next(a for a in root.legal_actions if a.kind == RAISE)
    facing_bet_node = root.children[raise_action]
    strategy = small_flop_result.strategy_at(facing_bet_node)
    assert strategy["Qc5c"]["fold"] > strategy["9d8d"]["fold"]


def test_solve_flop_config_reflects_pot_and_stack(small_flop_result):
    assert small_flop_result.config.pot == pytest.approx(10.0)
    assert small_flop_result.config.stack_bb == pytest.approx(15.0)
    assert small_flop_result.config.positions == ("OOP", "IP")


def test_solve_flop_root_is_oop_with_nothing_invested_yet(small_flop_result):
    root = small_flop_result.root
    assert root.player_to_act == "OOP"
    assert root.invested == {"OOP": 0.0, "IP": 0.0}
    assert root.pot == pytest.approx(10.0)


def test_solve_flop_deterministic_given_the_same_equity_seed():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    hero_range = {HandCombo(Card("7", "s"), Card("7", "c")): 1.0}
    villain_range = {HandCombo(Card("A", "h"), Card("K", "h")): 1.0}
    kwargs = dict(
        board=board,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=10.0,
        effective_stack_bb=15.0,
        max_raises=1,
        raise_sizes=(),
        iterations=50,
        equity_samples=50,
        equity_seed=7,
    )
    result_1 = solve_flop(**kwargs)
    result_2 = solve_flop(**kwargs)
    assert result_1.opening_range() == result_2.opening_range()


def test_solve_flop_uses_default_iterations_when_omitted():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    hero_range = {HandCombo(Card("7", "s"), Card("7", "c")): 1.0}
    villain_range = {HandCombo(Card("A", "h"), Card("K", "h")): 1.0}
    result = solve_flop(
        board=board,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=10.0,
        effective_stack_bb=15.0,
        max_raises=1,
        raise_sizes=(),
        equity_samples=50,
    )
    assert result.iterations > 0
    assert result.elapsed_seconds >= 0.0


def test_solve_flop_combo_missing_from_one_range_gets_zero_weight_there(small_flop_result):
    # 7s7c is only in hero_range, never villain_range — the combined
    # pool still includes it (both positions share one combo list), but
    # IP's reach for it must be 0, not an error and not a silent
    # fallback to some nonzero default. Observable indirectly: IP's
    # facing-a-bet node strategy for 7s7c is well-formed (not NaN), and
    # 7s7c isn't one of IP's own real hands (9d8d/Qc5c), so it never
    # actually gets any of IP's reach mass — confirmed structurally
    # instead by checking villain_range's own dict has no 7s7c entry
    # and hero_range's has no 9d8d/Qc5c entries, i.e. the two ranges
    # really are disjoint inputs, not accidentally overlapping ones
    # that would make this whole test moot.
    hero_combos = {"7s7c", "KsQd"}
    villain_combos = {"9d8d", "Qc5c"}
    assert hero_combos.isdisjoint(villain_combos)
    assert set(small_flop_result.opening_range().keys()) == hero_combos | villain_combos


# ---------------------------------------------------------------------------
# M35: solve_flop_multiway — the direct N-position generalization of
# solve_flop, via cfr.mccfr_solve + multiway_board_equity.
# NwayBoardEquityCache (M30-M32) instead of cfr.solve + board_equity.
# build_board_equity_table. Mirrors small_flop_result's own "one real
# hand, one pure air" shape, per position, generalized to 3 positions.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def small_flop_multiway_result():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    position_ranges = {
        "OOP": {
            HandCombo(Card("7", "s"), Card("7", "c")): 1.0,  # flopped a set of sevens
            HandCombo(Card("K", "s"), Card("Q", "d")): 1.0,  # complete air
        },
        "MID": {
            HandCombo(Card("A", "h"), Card("A", "d")): 1.0,  # overpair
            HandCombo(Card("3", "h"), Card("2", "h")): 1.0,  # complete air
        },
        "IP": {
            HandCombo(Card("9", "d"), Card("8", "d")): 1.0,  # top pair + a straight draw
            HandCombo(Card("Q", "c"), Card("5", "c")): 1.0,  # pure air
        },
    }
    return solve_flop_multiway(
        board=board,
        position_ranges=position_ranges,
        pot=9.0,
        effective_stack_bb=15.0,
        positions=("OOP", "MID", "IP"),
        raise_sizes=(0.75, 2.5),
        max_raises=3,
        iterations=300,
        equity_samples=50,
        equity_seed=1,
    )


def test_solve_flop_multiway_covers_the_union_of_all_ranges(small_flop_multiway_result):
    opening = small_flop_multiway_result.opening_range()
    expected = {"7s7c", "KsQd", "AhAd", "3h2h", "9d8d", "Qc5c"}
    assert set(opening.keys()) == expected


def test_solve_flop_multiway_frequencies_sum_to_one(small_flop_multiway_result):
    opening = small_flop_multiway_result.opening_range()
    for freqs in opening.values():
        assert not any(np.isnan(freq) for freq in freqs.values())
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


def test_solve_flop_multiway_a_real_hand_folds_to_a_bet_far_less_than_air(small_flop_multiway_result):
    # Same "value continues, air folds" pattern solve_flop's own test
    # establishes, now at a node a 2-position test structurally can't
    # reach: MID facing OOP's opening bet — the *second* actor at a
    # genuine 3-position table, not the only other one.
    root = small_flop_multiway_result.root
    raise_action = next(a for a in root.legal_actions if a.kind == RAISE)
    facing_bet_node = root.children[raise_action]
    assert facing_bet_node.player_to_act == "MID"
    strategy = small_flop_multiway_result.strategy_at(facing_bet_node)
    assert strategy["3h2h"]["fold"] > strategy["AhAd"]["fold"]


def test_solve_flop_multiway_config_reflects_pot_and_stack(small_flop_multiway_result):
    assert small_flop_multiway_result.config.pot == pytest.approx(9.0)
    assert small_flop_multiway_result.config.stack_bb == pytest.approx(15.0)
    assert small_flop_multiway_result.config.positions == ("OOP", "MID", "IP")


def test_solve_flop_multiway_root_is_oop_with_nothing_invested_yet(small_flop_multiway_result):
    root = small_flop_multiway_result.root
    assert root.player_to_act == "OOP"
    assert root.invested == {"OOP": 0.0, "MID": 0.0, "IP": 0.0}
    assert root.pot == pytest.approx(9.0)


def test_solve_flop_multiway_deterministic_given_the_same_seed():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    position_ranges = {
        "OOP": {HandCombo(Card("7", "s"), Card("7", "c")): 1.0},
        "MID": {HandCombo(Card("A", "h"), Card("A", "d")): 1.0},
        "IP": {HandCombo(Card("K", "h"), Card("K", "d")): 1.0},
    }
    kwargs = dict(
        board=board, position_ranges=position_ranges, pot=9.0, effective_stack_bb=15.0,
        positions=("OOP", "MID", "IP"), raise_sizes=(), max_raises=1,
        iterations=50, equity_samples=50, equity_seed=7, seed=3,
    )
    result_1 = solve_flop_multiway(**kwargs)
    result_2 = solve_flop_multiway(**kwargs)
    assert result_1.opening_range() == result_2.opening_range()


def test_solve_flop_multiway_uses_default_iterations_when_omitted():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    position_ranges = {
        "OOP": {HandCombo(Card("7", "s"), Card("7", "c")): 1.0},
        "MID": {HandCombo(Card("A", "h"), Card("A", "d")): 1.0},
        "IP": {HandCombo(Card("K", "h"), Card("K", "d")): 1.0},
    }
    result = solve_flop_multiway(
        board=board, position_ranges=position_ranges, pot=9.0, effective_stack_bb=15.0,
        positions=("OOP", "MID", "IP"), raise_sizes=(), max_raises=1, equity_samples=50,
    )
    assert result.iterations > 0
    assert result.elapsed_seconds >= 0.0


def test_solve_flop_multiway_combo_missing_from_a_range_gets_zero_weight_there(small_flop_multiway_result):
    # Mirrors solve_flop's own analogous test: each position's own
    # combos are disjoint from every other position's, so the union
    # pool is exactly the 6 combos, none shared.
    oop_combos = {"7s7c", "KsQd"}
    mid_combos = {"AhAd", "3h2h"}
    ip_combos = {"9d8d", "Qc5c"}
    assert oop_combos.isdisjoint(mid_combos)
    assert mid_combos.isdisjoint(ip_combos)
    assert set(small_flop_multiway_result.opening_range().keys()) == oop_combos | mid_combos | ip_combos


def test_solve_flop_multiway_rejects_positions_and_position_ranges_mismatch():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    position_ranges = {
        "OOP": {HandCombo(Card("7", "s"), Card("7", "c")): 1.0},
        "MID": {HandCombo(Card("A", "h"), Card("A", "d")): 1.0},
        # "IP" deliberately missing
    }
    with pytest.raises(ValueError):
        solve_flop_multiway(
            board=board, position_ranges=position_ranges, pot=9.0, effective_stack_bb=15.0,
            positions=("OOP", "MID", "IP"), raise_sizes=(), max_raises=1,
        )


def test_derive_ranges_from_path_pipeline_open_call_call_feeds_solve_flop_multiway(three_max_result):
    # The required end-to-end test: real (already-solved, module-scoped —
    # no new slow preflop solve) 3-max preflop result -> a genuine 3-step
    # path where BTN opens and BOTH SB and BB call, leaving all 3
    # positions live (the shape a 2-position pipeline test structurally
    # can't exercise) -> derive_ranges_from_path -> per-position combo
    # expansion -> real solve_flop_multiway.
    root = three_max_result.root
    open_raise = next(a for a in root.legal_actions if a.kind == RAISE)
    sb_node = root.children[open_raise]
    sb_call = next(a for a in sb_node.legal_actions if a.kind == CALL_OR_CHECK)
    bb_node = sb_node.children[sb_call]
    bb_call = next(a for a in bb_node.legal_actions if a.kind == CALL_OR_CHECK)

    scenario = derive_ranges_from_path(three_max_result, [open_raise, sb_call, bb_call])
    assert isinstance(scenario.node, TerminalNode)
    assert set(scenario.live_positions) == {"BTN", "SB", "BB"}

    kk = StartingHand("K", "K")
    trash = StartingHand("7", "2", suited=False)
    # Measured directly (not assumed) before writing this assertion, the
    # same discipline M15/M16 already established for this exact
    # pipeline shape: AA is NOT the robust comparator here, even for
    # BTN's own *opening* frequency — AA's own weight at BB (facing the
    # open, deciding whether to call) is a tiny 0.0004, smaller than
    # trash's own 0.305, because AA prefers 3-betting/jamming over flat-
    # calling (M15's own already-documented "premium hands don't just
    # call" pattern). KK, a real flatting/opening hand at every one of
    # these 3 positions in this pool (measured: 0.69-0.85 across BTN/SB/
    # BB, vs. trash's 0.0017-0.305), is the comparator that actually
    # behaves the way naive "premium continues more" intuition expects —
    # exactly the fix M15/M16 already reached for in the analogous
    # 2-position case, applied here rather than re-discovering it the
    # hard way a third time.
    for position in scenario.live_positions:
        assert scenario.ranges[position][kk] > scenario.ranges[position][trash]

    # PathScenario.live_positions stays in preflop acting order (BTN,
    # SB, BB) — postflop_action_order (M29) derives the correct postflop
    # seating order from it, the same real bug M32's own design pass
    # caught the tempting-but-wrong alternative for (see chance.py's
    # build_mccfr_chance_branch, which explicitly does NOT reach for
    # this function on an already-postflop tuple — this call site is the
    # correct one: converting a *preflop* GameConfig.positions ordering).
    postflop_positions = postflop_action_order(three_max_result.config.positions, scenario.live_positions)

    board = (Card("2", "h"), Card("6", "d"), Card("9", "c"))
    exclude = frozenset(board)
    position_ranges = {
        position: range_from_class_frequencies(scenario.ranges[position], exclude=exclude)
        for position in postflop_positions
    }

    stacks = {scenario.stacks[p] for p in scenario.live_positions}
    assert len(stacks) == 1  # every live position's remaining stack matches (proven N-general via M23)
    effective_stack_bb = next(iter(stacks))

    flop_result = solve_flop_multiway(
        board=board,
        position_ranges=position_ranges,
        pot=scenario.pot,
        effective_stack_bb=effective_stack_bb,
        positions=postflop_positions,
        max_raises=1,
        raise_sizes=(),
        iterations=50,
        equity_samples=50,
    )
    opening = flop_result.opening_range()
    assert len(opening) > 0
    for freqs in opening.values():
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


# ---------------------------------------------------------------------------
# M36: solve_flop_turn_multiway — a flop showdown-eligible terminal chains
# into a real multiway turn betting round (via a real, sampled chance
# branch — cfr.mccfr_solve's own board/chance_fn/chance_data, M32)
# instead of solve_flop_multiway's "average every remaining runout inside
# NwayBoardEquityCache" shortcut. The direct N-position generalization of
# solve_flop_turn, mirroring solve_flop_multiway's own relationship to
# solve_flop.
#
# Deliberately the smallest possible combo pool (1 combo per position, 3
# total) — measured during M36's own scoping pass (see DEFAULT_FLOP_TURN_
# MULTIWAY_ITERATIONS's own comment in solver.py): pool size is still the
# dominant cost driver for this solving path, exactly as M35 already
# found for the flop-only case, so this stays at the same tiny scale
# solve_flop_turn's own M12 tests do.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tiny_flop_turn_multiway_result():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    position_ranges = {
        "OOP": {HandCombo(Card("7", "s"), Card("7", "c")): 1.0},
        "MID": {HandCombo(Card("A", "h"), Card("A", "d")): 1.0},
        "IP": {HandCombo(Card("9", "d"), Card("8", "d")): 1.0},
    }
    return solve_flop_turn_multiway(
        board=board,
        position_ranges=position_ranges,
        pot=9.0,
        effective_stack_bb=15.0,
        positions=("OOP", "MID", "IP"),
        raise_sizes=(),
        max_raises=1,
        iterations=30,
        equity_samples=50,
    )


def test_solve_flop_turn_multiway_covers_all_three_ranges(tiny_flop_turn_multiway_result):
    opening = tiny_flop_turn_multiway_result.opening_range()
    assert set(opening.keys()) == {"7s7c", "AhAd", "9d8d"}


def test_solve_flop_turn_multiway_frequencies_sum_to_one(tiny_flop_turn_multiway_result):
    opening = tiny_flop_turn_multiway_result.opening_range()
    for freqs in opening.values():
        assert not any(np.isnan(freq) for freq in freqs.values())
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


def test_solve_flop_turn_multiway_root_is_the_flop_root(tiny_flop_turn_multiway_result):
    root = tiny_flop_turn_multiway_result.root
    assert isinstance(root, DecisionNode)
    assert root.player_to_act == "OOP"
    assert root.pot == pytest.approx(9.0)


def test_solve_flop_turn_multiway_chance_data_reaches_a_real_turn_decision_node(tiny_flop_turn_multiway_result):
    # Proves chaining actually happened, not just "ran without crashing".
    # chance_data is keyed by (id(terminal), card) — M32's own per-
    # sampled-card memoization, unlike solve_flop_turn's own one-
    # ChanceNode-per-terminal shape: mccfr_solve only ever builds the ONE
    # branch actually sampled that iteration, never all ~49 possible next
    # cards, so chance_data's own entry count reflects distinct sampled
    # (terminal, card) pairs, not distinct terminals.
    chance_data = tiny_flop_turn_multiway_result.chance_data
    assert len(chance_data) > 0
    real_decisions = [b.root for b in chance_data.values() if isinstance(b.root, DecisionNode)]
    assert real_decisions  # at least one branch reached a genuine turn decision, not just an all-in-reused terminal
    strategy = tiny_flop_turn_multiway_result.strategy_at(real_decisions[0])
    for freqs in strategy.values():
        assert not any(np.isnan(freq) for freq in freqs.values())
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


def test_solve_flop_turn_multiway_deterministic_given_the_same_seed():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    position_ranges = {
        "OOP": {HandCombo(Card("7", "s"), Card("7", "c")): 1.0},
        "MID": {HandCombo(Card("A", "h"), Card("A", "d")): 1.0},
        "IP": {HandCombo(Card("9", "d"), Card("8", "d")): 1.0},
    }
    kwargs = dict(
        board=board, position_ranges=position_ranges, pot=9.0, effective_stack_bb=15.0,
        positions=("OOP", "MID", "IP"), raise_sizes=(), max_raises=1,
        iterations=20, equity_samples=50, equity_seed=7, seed=3,
    )
    result_1 = solve_flop_turn_multiway(**kwargs)
    result_2 = solve_flop_turn_multiway(**kwargs)
    assert result_1.opening_range() == result_2.opening_range()


def test_solve_flop_turn_multiway_uses_default_iterations_when_omitted():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    position_ranges = {
        "OOP": {HandCombo(Card("7", "s"), Card("7", "c")): 1.0},
        "MID": {HandCombo(Card("A", "h"), Card("A", "d")): 1.0},
        "IP": {HandCombo(Card("9", "d"), Card("8", "d")): 1.0},
    }
    result = solve_flop_turn_multiway(
        board=board, position_ranges=position_ranges, pot=9.0, effective_stack_bb=15.0,
        positions=("OOP", "MID", "IP"), raise_sizes=(), max_raises=1, equity_samples=50,
    )
    assert result.iterations > 0
    assert result.elapsed_seconds >= 0.0


def test_solve_flop_turn_multiway_rejects_positions_and_position_ranges_mismatch():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    position_ranges = {
        "OOP": {HandCombo(Card("7", "s"), Card("7", "c")): 1.0},
        "MID": {HandCombo(Card("A", "h"), Card("A", "d")): 1.0},
        # "IP" deliberately missing
    }
    with pytest.raises(ValueError):
        solve_flop_turn_multiway(
            board=board, position_ranges=position_ranges, pot=9.0, effective_stack_bb=15.0,
            positions=("OOP", "MID", "IP"), raise_sizes=(), max_raises=1,
        )


# ---------------------------------------------------------------------------
# M44: ensure_flop_turn_multiway_branch — solve_flop_turn_multiway's own
# chance_data only ever contains the (terminal, card) pairs MCCFR actually
# happened to sample while solving (unlike solve_flop_turn's exact-solver
# chance_data, which eagerly builds every possible next card). This
# closes that real, structural gap: given a genuine miss, it builds and
# caches exactly the branch MCCFR would have built had it sampled that
# pair itself.
# ---------------------------------------------------------------------------


def test_ensure_flop_turn_multiway_branch_returns_the_cached_branch_on_a_hit(tiny_flop_turn_multiway_result):
    # Any real (terminal, card) pair MCCFR actually sampled works as a
    # "hit" fixture — walk the whole (tiny) tree to map chance_data's own
    # id()-keys back to real TerminalNode objects, then pick one.
    (terminal_id, card), branch = next(iter(tiny_flop_turn_multiway_result.chance_data.items()))
    nodes_by_id = {id(node): node for node in walk(tiny_flop_turn_multiway_result.root)}
    terminal = nodes_by_id[terminal_id]
    position_ranges = {
        "OOP": {HandCombo(Card("7", "s"), Card("7", "c")): 1.0},
        "MID": {HandCombo(Card("A", "h"), Card("A", "d")): 1.0},
        "IP": {HandCombo(Card("9", "d"), Card("8", "d")): 1.0},
    }
    result = ensure_flop_turn_multiway_branch(
        tiny_flop_turn_multiway_result, terminal, card,
        board=(Card("7", "h"), Card("2", "d"), Card("9", "c")),
        position_ranges=position_ranges, positions=("OOP", "MID", "IP"),
        effective_stack_bb=15.0, raise_sizes=(), max_raises=1, equity_samples=50,
    )
    assert result is branch  # the exact cached object, not a rebuild


def test_ensure_flop_turn_multiway_branch_builds_and_caches_on_a_miss():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    position_ranges = {
        "OOP": {HandCombo(Card("7", "s"), Card("7", "c")): 1.0},
        "MID": {HandCombo(Card("A", "h"), Card("A", "d")): 1.0},
        "IP": {HandCombo(Card("9", "d"), Card("8", "d")): 1.0},
    }
    result = solve_flop_turn_multiway(
        board=board, position_ranges=position_ranges, pot=9.0, effective_stack_bb=15.0,
        positions=("OOP", "MID", "IP"), raise_sizes=(), max_raises=1,
        iterations=20, equity_samples=50, seed=1,
    )
    terminal = _find_a_showdown_terminal(result.root)
    already_sampled = {card for (tid, card) in result.chance_data if tid == id(terminal)}
    unsampled_card = next(c for c in remaining_deck(board) if c not in already_sampled)
    before_count = len(result.chance_data)

    branch = ensure_flop_turn_multiway_branch(
        result, terminal, unsampled_card, board=board, position_ranges=position_ranges,
        positions=("OOP", "MID", "IP"), effective_stack_bb=15.0, raise_sizes=(), max_raises=1,
        equity_samples=50,
    )

    assert len(result.chance_data) == before_count + 1
    assert result.chance_data[(id(terminal), unsampled_card)] is branch
    if isinstance(branch.root, DecisionNode):
        # A freshly-built, MCCFR-untouched node — every hand correctly
        # falls back to the untrained uniform default (M28's own existing
        # strategy_at/trained_hands behavior, no special-casing needed).
        trained = result.trained_hands(branch.root)
        assert all(is_trained is False for is_trained in trained.values())
        strategy = result.strategy_at(branch.root)
        for freqs in strategy.values():
            assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


def test_ensure_flop_turn_multiway_branch_second_call_hits_the_cache_it_just_built():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    position_ranges = {
        "OOP": {HandCombo(Card("7", "s"), Card("7", "c")): 1.0},
        "MID": {HandCombo(Card("A", "h"), Card("A", "d")): 1.0},
        "IP": {HandCombo(Card("9", "d"), Card("8", "d")): 1.0},
    }
    result = solve_flop_turn_multiway(
        board=board, position_ranges=position_ranges, pot=9.0, effective_stack_bb=15.0,
        positions=("OOP", "MID", "IP"), raise_sizes=(), max_raises=1,
        iterations=20, equity_samples=50, seed=1,
    )
    terminal = _find_a_showdown_terminal(result.root)
    already_sampled = {card for (tid, card) in result.chance_data if tid == id(terminal)}
    unsampled_card = next(c for c in remaining_deck(board) if c not in already_sampled)

    kwargs = dict(
        board=board, position_ranges=position_ranges, positions=("OOP", "MID", "IP"),
        effective_stack_bb=15.0, raise_sizes=(), max_raises=1, equity_samples=50,
    )
    first = ensure_flop_turn_multiway_branch(result, terminal, unsampled_card, **kwargs)
    before_count = len(result.chance_data)
    second = ensure_flop_turn_multiway_branch(result, terminal, unsampled_card, **kwargs)
    assert second is first
    assert len(result.chance_data) == before_count  # no duplicate build


def test_ensure_flop_turn_multiway_branch_raises_for_an_illegal_card():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    position_ranges = {
        "OOP": {HandCombo(Card("7", "s"), Card("7", "c")): 1.0},
        "MID": {HandCombo(Card("A", "h"), Card("A", "d")): 1.0},
        "IP": {HandCombo(Card("9", "d"), Card("8", "d")): 1.0},
    }
    result = solve_flop_turn_multiway(
        board=board, position_ranges=position_ranges, pot=9.0, effective_stack_bb=15.0,
        positions=("OOP", "MID", "IP"), raise_sizes=(), max_raises=1,
        iterations=20, equity_samples=50, seed=1,
    )
    terminal = _find_a_showdown_terminal(result.root)
    with pytest.raises(ValueError):
        ensure_flop_turn_multiway_branch(
            result, terminal, Card("7", "h"),  # already on the board
            board=board, position_ranges=position_ranges, positions=("OOP", "MID", "IP"),
            effective_stack_bb=15.0, raise_sizes=(), max_raises=1, equity_samples=50,
        )


def test_ensure_mccfr_chance_branch_is_the_same_object_as_the_m44_alias():
    # M53 renamed the function once it was proven hop-agnostic; the old
    # name stays as an alias so nothing that imported it breaks.
    assert ensure_flop_turn_multiway_branch is ensure_mccfr_chance_branch


def test_ensure_mccfr_chance_branch_builds_a_river_hop_from_a_four_card_board():
    # M44 left open whether a SECOND chained hop needs structurally
    # different treatment. M53's answer, proven here rather than argued:
    # the SAME function, handed a 4-card (flop+turn) board, produces a
    # real river branch — 5-card board, and chance_fn correctly None
    # because build_mccfr_chance_branch self-guards len(next_board) < 5.
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    position_ranges = {
        "OOP": {HandCombo(Card("7", "s"), Card("7", "c")): 1.0},
        "MID": {HandCombo(Card("A", "h"), Card("A", "d")): 1.0},
        "IP": {HandCombo(Card("9", "d"), Card("8", "d")): 1.0},
    }
    positions = ("OOP", "MID", "IP")
    result = solve_flop_to_river_multiway(
        board=board, position_ranges=position_ranges, pot=9.0, effective_stack_bb=15.0,
        positions=positions, raise_sizes=(), max_raises=1,
        iterations=20, equity_samples=50, seed=1,
    )
    flop_terminal = _find_a_showdown_terminal(result.root)
    turn_card = next(c for c in remaining_deck(board))
    turn_branch = ensure_mccfr_chance_branch(
        result, flop_terminal, turn_card, board=board, position_ranges=position_ranges,
        positions=positions, effective_stack_bb=15.0, raise_sizes=(), max_raises=1,
        equity_samples=50, chain_to_river=True,
    )
    assert len(turn_branch.board) == 4

    turn_root = turn_branch.root
    if isinstance(turn_root, DecisionNode):
        turn_terminal = _find_a_showdown_terminal(turn_root)
        four_card_board = board + (turn_card,)
        river_card = next(c for c in remaining_deck(four_card_board))
        river_branch = ensure_mccfr_chance_branch(
            result, turn_terminal, river_card, board=four_card_board,
            position_ranges=position_ranges, positions=positions,
            effective_stack_bb=15.0, raise_sizes=(), max_raises=1, equity_samples=50,
        )
        assert len(river_branch.board) == 5
        # No cards left to deal past a complete board — the guard that
        # makes the second hop the LAST one, not an infinite chain.
        assert river_branch.chance_fn is None
        assert (id(turn_terminal), river_card) in result.chance_data


# ---------------------------------------------------------------------------
# M39: solve_flop_to_river_multiway — a second chance-branch hop on top of
# solve_flop_turn_multiway, chaining all the way to a real multiway river
# showdown (chance.build_mccfr_chance_branch's chain_to_river, M39) — the
# direct N-position generalization of solve_flop_to_river (M13).
#
# A real, measured surprise (see DEFAULT_FLOP_TO_RIVER_MULTIWAY_
# ITERATIONS's own comment in solver.py): unlike the 2-position exact
# solver, where the second hop is dramatically MORE expensive (M13
# measured ~63-105s vs. solve_flop_turn's own ~18-26s), the MCCFR-native
# version is actually CHEAPER at a matching pool/tree than solve_flop_
# turn_multiway's own numbers — build_mccfr_chance_branch's lazy,
# one-sampled-card-at-a-time design never pays the exact solver's own
# ~44x44 eager-branch combinatorial cost. So this fixture uses the SAME
# tiny scale solve_flop_turn_multiway's own fixture does, not a shrunk
# one.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tiny_flop_to_river_multiway_result():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    position_ranges = {
        "OOP": {HandCombo(Card("7", "s"), Card("7", "c")): 1.0},
        "MID": {HandCombo(Card("A", "h"), Card("A", "d")): 1.0},
        "IP": {HandCombo(Card("9", "d"), Card("8", "d")): 1.0},
    }
    return solve_flop_to_river_multiway(
        board=board,
        position_ranges=position_ranges,
        pot=9.0,
        effective_stack_bb=15.0,
        positions=("OOP", "MID", "IP"),
        raise_sizes=(),
        max_raises=1,
        iterations=30,
        equity_samples=50,
    )


def test_solve_flop_to_river_multiway_covers_all_three_ranges(tiny_flop_to_river_multiway_result):
    opening = tiny_flop_to_river_multiway_result.opening_range()
    assert set(opening.keys()) == {"7s7c", "AhAd", "9d8d"}


def test_solve_flop_to_river_multiway_frequencies_sum_to_one(tiny_flop_to_river_multiway_result):
    opening = tiny_flop_to_river_multiway_result.opening_range()
    for freqs in opening.values():
        assert not any(np.isnan(freq) for freq in freqs.values())
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


def test_solve_flop_to_river_multiway_root_is_the_flop_root(tiny_flop_to_river_multiway_result):
    root = tiny_flop_to_river_multiway_result.root
    assert isinstance(root, DecisionNode)
    assert root.player_to_act == "OOP"
    assert root.pot == pytest.approx(9.0)


def test_solve_flop_to_river_multiway_chance_data_reaches_a_real_river_level(tiny_flop_to_river_multiway_result):
    # This is the test that actually proves the SECOND hop happened, not
    # just the first (already covered by solve_flop_turn_multiway's own
    # analogous test): a real, naturally-reached-during-solving branch
    # whose own board is a complete 5-card river (chance_data's own
    # entries carry their own `board` field — no tree-walking needed to
    # tell turn-level from river-level entries apart).
    chance_data = tiny_flop_to_river_multiway_result.chance_data
    river_branches = [b for b in chance_data.values() if len(b.board) == 5]
    assert river_branches  # at least one branch actually reached the river during real solving

    real_river_decisions = [b.root for b in river_branches if isinstance(b.root, DecisionNode)]
    assert real_river_decisions  # at least one is a genuine river decision, not just an all-in-reused terminal
    strategy = tiny_flop_to_river_multiway_result.strategy_at(real_river_decisions[0])
    for freqs in strategy.values():
        assert not any(np.isnan(freq) for freq in freqs.values())
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0

    # No branch's own chance_fn survives past a complete river board —
    # the direct regression test for chance.py's own "chain_to_river
    # never populates chance_fn once len(next_board) == 5" guard.
    assert all(b.chance_fn is None for b in river_branches)


def test_solve_flop_to_river_multiway_deterministic_given_the_same_seed():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    position_ranges = {
        "OOP": {HandCombo(Card("7", "s"), Card("7", "c")): 1.0},
        "MID": {HandCombo(Card("A", "h"), Card("A", "d")): 1.0},
        "IP": {HandCombo(Card("9", "d"), Card("8", "d")): 1.0},
    }
    kwargs = dict(
        board=board, position_ranges=position_ranges, pot=9.0, effective_stack_bb=15.0,
        positions=("OOP", "MID", "IP"), raise_sizes=(), max_raises=1,
        iterations=20, equity_samples=50, equity_seed=7, seed=3,
    )
    result_1 = solve_flop_to_river_multiway(**kwargs)
    result_2 = solve_flop_to_river_multiway(**kwargs)
    assert result_1.opening_range() == result_2.opening_range()


def test_solve_flop_to_river_multiway_uses_default_iterations_when_omitted():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    position_ranges = {
        "OOP": {HandCombo(Card("7", "s"), Card("7", "c")): 1.0},
        "MID": {HandCombo(Card("A", "h"), Card("A", "d")): 1.0},
        "IP": {HandCombo(Card("9", "d"), Card("8", "d")): 1.0},
    }
    result = solve_flop_to_river_multiway(
        board=board, position_ranges=position_ranges, pot=9.0, effective_stack_bb=15.0,
        positions=("OOP", "MID", "IP"), raise_sizes=(), max_raises=1, equity_samples=50,
    )
    assert result.iterations > 0
    assert result.elapsed_seconds >= 0.0


def test_solve_flop_to_river_multiway_rejects_positions_and_position_ranges_mismatch():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    position_ranges = {
        "OOP": {HandCombo(Card("7", "s"), Card("7", "c")): 1.0},
        "MID": {HandCombo(Card("A", "h"), Card("A", "d")): 1.0},
        # "IP" deliberately missing
    }
    with pytest.raises(ValueError):
        solve_flop_to_river_multiway(
            board=board, position_ranges=position_ranges, pot=9.0, effective_stack_bb=15.0,
            positions=("OOP", "MID", "IP"), raise_sizes=(), max_raises=1,
        )


# ---------------------------------------------------------------------------
# M18 deliverable: solve_flop_abstracted — the same betting tree/parameters
# as small_flop_result above, but solved over abstraction.HandBucket
# buckets instead of real combos. num_buckets=2 on this fixture's 4-combo
# pool is the meaningful non-trivial case (fewer buckets than combos, so
# real aggregation happens) while still small enough to hand-verify.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def small_flop_abstracted_result():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    hero_range = {
        HandCombo(Card("7", "s"), Card("7", "c")): 1.0,
        HandCombo(Card("K", "s"), Card("Q", "d")): 1.0,
    }
    villain_range = {
        HandCombo(Card("9", "d"), Card("8", "d")): 1.0,
        HandCombo(Card("Q", "c"), Card("5", "c")): 1.0,
    }
    return solve_flop_abstracted(
        board=board,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=10.0,
        effective_stack_bb=15.0,
        num_buckets=2,
        positions=("OOP", "IP"),
        raise_sizes=(0.75, 2.5),
        max_raises=3,
        iterations=3000,
        equity_samples=300,
        equity_seed=1,
    )


def test_solve_flop_abstracted_hands_are_buckets_covering_the_whole_pool(small_flop_abstracted_result):
    assert len(small_flop_abstracted_result.hands) == 2
    for hand in small_flop_abstracted_result.hands:
        assert isinstance(hand, HandBucket)


def test_solve_flop_abstracted_opening_range_keys_match_str_of_each_bucket(small_flop_abstracted_result):
    opening = small_flop_abstracted_result.opening_range()
    expected = {str(bucket) for bucket in small_flop_abstracted_result.hands}
    assert set(opening.keys()) == expected


def test_solve_flop_abstracted_frequencies_sum_to_one(small_flop_abstracted_result):
    opening = small_flop_abstracted_result.opening_range()
    for freqs in opening.values():
        assert not any(np.isnan(freq) for freq in freqs.values())
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


def test_solve_flop_abstracted_config_reflects_pot_and_stack(small_flop_abstracted_result):
    assert small_flop_abstracted_result.config.pot == pytest.approx(10.0)
    assert small_flop_abstracted_result.config.stack_bb == pytest.approx(15.0)
    assert small_flop_abstracted_result.config.positions == ("OOP", "IP")


def test_solve_flop_abstracted_deterministic_given_the_same_equity_seed():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    hero_range = {HandCombo(Card("7", "s"), Card("7", "c")): 1.0}
    villain_range = {HandCombo(Card("A", "h"), Card("K", "h")): 1.0}
    kwargs = dict(
        board=board,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=10.0,
        effective_stack_bb=15.0,
        num_buckets=2,
        max_raises=1,
        raise_sizes=(),
        iterations=50,
        equity_samples=50,
        equity_seed=7,
    )
    result_1 = solve_flop_abstracted(**kwargs)
    result_2 = solve_flop_abstracted(**kwargs)
    assert result_1.opening_range() == result_2.opening_range()


def test_solve_flop_abstracted_uses_default_iterations_when_omitted():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    hero_range = {HandCombo(Card("7", "s"), Card("7", "c")): 1.0}
    villain_range = {HandCombo(Card("A", "h"), Card("K", "h")): 1.0}
    result = solve_flop_abstracted(
        board=board,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=10.0,
        effective_stack_bb=15.0,
        num_buckets=2,
        max_raises=1,
        raise_sizes=(),
        equity_samples=50,
    )
    assert result.iterations > 0
    assert result.elapsed_seconds >= 0.0


def test_solve_flop_abstracted_rejects_more_buckets_than_unblocked_combos():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    hero_range = {HandCombo(Card("7", "s"), Card("7", "c")): 1.0}
    villain_range = {HandCombo(Card("A", "h"), Card("K", "h")): 1.0}
    # Only 2 combos total in the combined pool — build_hand_buckets'
    # existing ValueError (num_buckets > len(unblocked combos)) must
    # propagate unmodified through solve_flop_abstracted.
    with pytest.raises(ValueError):
        solve_flop_abstracted(
            board=board,
            hero_range=hero_range,
            villain_range=villain_range,
            pot=10.0,
            effective_stack_bb=15.0,
            num_buckets=5,
            max_raises=1,
            raise_sizes=(),
            equity_samples=50,
        )


def test_expand_bucket_strategy_fans_each_combo_out_to_its_bucket_entry():
    combo_a = HandCombo(Card("7", "s"), Card("7", "c"))
    combo_b = HandCombo(Card("K", "s"), Card("Q", "d"))
    combo_c = HandCombo(Card("9", "d"), Card("8", "d"))
    bucket_weak = HandBucket(bucket_id=0, members=(combo_b,), weight=1.0, strength=0.2)
    bucket_strong = HandBucket(bucket_id=1, members=(combo_a, combo_c), weight=2.0, strength=0.8)
    pool = BucketedPool(
        buckets=(bucket_weak, bucket_strong),
        combo_to_bucket={combo_b: 0, combo_a: 1, combo_c: 1},
        source_combos=(combo_a, combo_b, combo_c),
        equity_table=np.full((3, 3), np.nan),
    )
    bucket_strategy = {
        str(bucket_weak): {"fold": 1.0, "call": 0.0},
        str(bucket_strong): {"fold": 0.1, "call": 0.9},
    }
    expanded = expand_bucket_strategy(bucket_strategy, pool)

    assert expanded[str(combo_b)] == {"fold": 1.0, "call": 0.0}
    assert expanded[str(combo_a)] == {"fold": 0.1, "call": 0.9}
    assert expanded[str(combo_c)] == {"fold": 0.1, "call": 0.9}


def test_expand_bucket_strategy_raises_on_a_mismatched_pool():
    combo_a = HandCombo(Card("7", "s"), Card("7", "c"))
    bucket = HandBucket(bucket_id=0, members=(combo_a,), weight=1.0, strength=0.5)
    pool = BucketedPool(
        buckets=(bucket,),
        combo_to_bucket={combo_a: 0},
        source_combos=(combo_a,),
        equity_table=np.full((1, 1), np.nan),
    )
    # bucket_strategy keyed by a str() that doesn't match this pool's own
    # bucket at all — simulates passing a strategy dict from a different
    # solve_flop_abstracted call/pool.
    with pytest.raises(ValueError):
        expand_bucket_strategy({"bucket99(n=1, strength=0.500)": {"fold": 1.0}}, pool)


def test_solve_flop_abstracted_expanded_strategy_is_a_coarse_regression_guard_against_the_real_solve(
    small_flop_result, small_flop_abstracted_result
):
    # A coarse regression guard, not the real accuracy measurement (see
    # the M18 PR/CLAUDE.md for that) — num_buckets=2 on this fixture's 4
    # combos is deliberately lossy, so a generous bound is the point:
    # this only needs to catch a wiring bug (e.g. reach vectors swapped,
    # wrong bucket used), not assert tight numerical accuracy.
    real_opening = small_flop_result.opening_range()
    # Rebuild the same bucketed pool solve_flop_abstracted used internally
    # so expand_bucket_strategy has something to fan out against — the
    # fixture only exposes the already-bucket-keyed StrategyResult, not
    # the BucketedPool itself.
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    hero_range = {
        HandCombo(Card("7", "s"), Card("7", "c")): 1.0,
        HandCombo(Card("K", "s"), Card("Q", "d")): 1.0,
    }
    villain_range = {
        HandCombo(Card("9", "d"), Card("8", "d")): 1.0,
        HandCombo(Card("Q", "c"), Card("5", "c")): 1.0,
    }
    combined_weights = {c: hero_range.get(c, 0.0) + villain_range.get(c, 0.0) for c in set(hero_range) | set(villain_range)}
    bucketed_pool = build_hand_buckets(board, combined_weights, num_buckets=2, samples=300, rng=random.Random(1))

    abstracted_opening = small_flop_abstracted_result.opening_range()
    expanded = expand_bucket_strategy(abstracted_opening, bucketed_pool)

    for combo_key in real_opening:
        real_freqs = real_opening[combo_key]
        approx_freqs = expanded[combo_key]
        actions = set(real_freqs) | set(approx_freqs)
        total_variation = 0.5 * sum(abs(real_freqs.get(a, 0.0) - approx_freqs.get(a, 0.0)) for a in actions)
        assert total_variation <= 0.9  # generous — a wiring-bug guard, not an accuracy bound


# ---------------------------------------------------------------------------
# M12 deliverable: solve_flop_turn — a flop showdown-eligible terminal
# chains into a real turn betting round (via a real chance node) instead
# of solve_flop's "average every remaining runout immediately" shortcut.
# See chance.py's/cfr.py's module docstrings for the design.
#
# Deliberately the smallest possible combo pool (1 hero combo x 1 villain
# combo, raise_sizes=(), max_raises=1) — every chance node this milestone
# builds costs one board_equity table per undealt card (~49 of them, see
# chance.py), and a flop tree can have several distinct showdown
# terminals, each needing its own ~49-table chance node. Measured at this
# fixture's scale (2-combo pool): ~0.3s just for one chance node's 49
# tables (board_equity's exact-remaining_needed==1 fix makes this cheap —
# see board_equity.py); measured separately at solve_flop's actual demo
# scale (~34 combos, the full DEMO_FLOP_HERO/VILLAIN_CLASSES expansion):
# well past what's reasonable for a live request (see the M12 PR for the
# exact number) — real confirmation of the O(N^2)-in-combo-count cost
# board_equity.py's own module comment already flags, now multiplied by
# "however many distinct showdown terminals the flop tree has." This is
# exactly why M12 ships engine + tests only, no API/frontend slice.
# ---------------------------------------------------------------------------


def _find_a_showdown_terminal(root: DecisionNode):
    """Walk call_or_check from `root` until a showdown-eligible terminal
    is reached (the "checked through" line) — same call_or_check-walking
    idiom StrategyResult.node_for_position already uses."""
    node = root
    while isinstance(node, DecisionNode):
        call_action = next(a for a in node.legal_actions if a.kind == CALL_OR_CHECK)
        node = node.children[call_action]
    return node


@pytest.fixture(scope="module")
def tiny_flop_turn_result():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    hero_range = {HandCombo(Card("7", "s"), Card("7", "c")): 1.0}
    villain_range = {HandCombo(Card("9", "d"), Card("8", "d")): 1.0}
    return solve_flop_turn(
        board=board,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=10.0,
        effective_stack_bb=15.0,
        positions=("OOP", "IP"),
        raise_sizes=(),
        max_raises=1,
        iterations=20,
    )


def test_solve_flop_turn_covers_the_union_of_both_ranges(tiny_flop_turn_result):
    opening = tiny_flop_turn_result.opening_range()
    assert set(opening.keys()) == {"7s7c", "9d8d"}


def test_solve_flop_turn_frequencies_sum_to_one(tiny_flop_turn_result):
    opening = tiny_flop_turn_result.opening_range()
    for freqs in opening.values():
        assert not any(np.isnan(freq) for freq in freqs.values())
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


def test_solve_flop_turn_root_is_the_flop_root(tiny_flop_turn_result):
    root = tiny_flop_turn_result.root
    assert isinstance(root, DecisionNode)
    assert root.player_to_act == "OOP"
    assert root.pot == pytest.approx(10.0)


def test_solve_flop_turn_chance_data_reaches_a_real_turn_decision_node(tiny_flop_turn_result):
    # This is the test that actually proves chaining happened, not just
    # "solve_flop_turn ran without crashing": a real showdown-eligible
    # flop terminal shows up in chance_data, and at least one of its
    # branches leads to a real, well-formed turn DecisionNode.
    terminal = _find_a_showdown_terminal(tiny_flop_turn_result.root)
    assert terminal.is_showdown
    assert id(terminal) in tiny_flop_turn_result.chance_data

    chance_node = tiny_flop_turn_result.chance_data[id(terminal)]
    any_branch = next(iter(chance_node.branches.values()))
    assert isinstance(any_branch.root, DecisionNode)
    assert any_branch.root.player_to_act == "OOP"

    strategy = tiny_flop_turn_result.strategy_at(any_branch.root)
    for freqs in strategy.values():
        assert not any(np.isnan(freq) for freq in freqs.values())
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


def test_solve_flop_turn_deterministic_given_the_same_inputs():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    hero_range = {HandCombo(Card("7", "s"), Card("7", "c")): 1.0}
    villain_range = {HandCombo(Card("9", "d"), Card("8", "d")): 1.0}
    kwargs = dict(
        board=board,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=10.0,
        effective_stack_bb=15.0,
        positions=("OOP", "IP"),
        raise_sizes=(),
        max_raises=1,
        iterations=20,
    )
    # No equity_samples/equity_seed to pin here — every board_equity
    # table this builds is a turn-board (remaining_needed==1) table,
    # which is exact per the board_equity.py fix, so there's no sampling
    # randomness left to control.
    result_1 = solve_flop_turn(**kwargs)
    result_2 = solve_flop_turn(**kwargs)
    assert result_1.opening_range() == result_2.opening_range()


def test_solve_flop_turn_uses_default_iterations_when_omitted():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    hero_range = {HandCombo(Card("7", "s"), Card("7", "c")): 1.0}
    villain_range = {HandCombo(Card("9", "d"), Card("8", "d")): 1.0}
    result = solve_flop_turn(
        board=board,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=10.0,
        effective_stack_bb=15.0,
        positions=("OOP", "IP"),
        raise_sizes=(),
        max_raises=1,
    )
    assert result.iterations > 0
    assert result.elapsed_seconds >= 0.0


# ---------------------------------------------------------------------------
# M13 deliverable: solve_flop_to_river — a turn showdown-eligible terminal
# also chains into a real river betting round (chance.build_chance_node's
# chain_to_river), not just flop->turn. Same tiny fixture as
# tiny_flop_turn_result (real-measured cost ~4.3s at these params, see the
# M13 PR) — a full demo-scale (~33 combos) solve was reasoned, not
# separately re-measured, to be far past viable for a live request, same
# conclusion M12 already reached one milestone earlier — so, same as
# solve_flop_turn, this ships engine + tests only, no API/frontend slice.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tiny_flop_to_river_result():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    hero_range = {HandCombo(Card("7", "s"), Card("7", "c")): 1.0}
    villain_range = {HandCombo(Card("9", "d"), Card("8", "d")): 1.0}
    return solve_flop_to_river(
        board=board,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=10.0,
        effective_stack_bb=15.0,
        positions=("OOP", "IP"),
        raise_sizes=(),
        max_raises=1,
        iterations=20,
    )


def test_solve_flop_to_river_covers_the_union_of_both_ranges(tiny_flop_to_river_result):
    opening = tiny_flop_to_river_result.opening_range()
    assert set(opening.keys()) == {"7s7c", "9d8d"}


def test_solve_flop_to_river_frequencies_sum_to_one(tiny_flop_to_river_result):
    opening = tiny_flop_to_river_result.opening_range()
    for freqs in opening.values():
        assert not any(np.isnan(freq) for freq in freqs.values())
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


def test_solve_flop_to_river_root_is_the_flop_root(tiny_flop_to_river_result):
    root = tiny_flop_to_river_result.root
    assert isinstance(root, DecisionNode)
    assert root.player_to_act == "OOP"
    assert root.pot == pytest.approx(10.0)


def test_solve_flop_to_river_chance_data_reaches_a_real_turn_decision_node(tiny_flop_to_river_result):
    # Confirms the first (flop->turn) leg still works exactly as it does
    # for solve_flop_turn — unaffected by the new river hop.
    terminal = _find_a_showdown_terminal(tiny_flop_to_river_result.root)
    assert terminal.is_showdown
    assert id(terminal) in tiny_flop_to_river_result.chance_data

    chance_node = tiny_flop_to_river_result.chance_data[id(terminal)]
    any_branch = next(iter(chance_node.branches.values()))
    assert isinstance(any_branch.root, DecisionNode)
    assert any_branch.root.player_to_act == "OOP"


def test_solve_flop_to_river_chance_data_reaches_a_real_river_level(tiny_flop_to_river_result):
    # This is the test that actually proves the *second* hop happened:
    # walk flop showdown terminal -> its ChanceNode -> a branch with a
    # real turn DecisionNode -> call-or-check-walk to a turn showdown
    # terminal -> confirm that terminal is *also* in chance_data (the
    # same flat dict, one level deeper) -> confirm its own branches lead
    # to well-formed river structure.
    flop_terminal = _find_a_showdown_terminal(tiny_flop_to_river_result.root)
    flop_chance_node = tiny_flop_to_river_result.chance_data[id(flop_terminal)]
    turn_branch = next(b for b in flop_chance_node.branches.values() if isinstance(b.root, DecisionNode))

    turn_terminal = _find_a_showdown_terminal(turn_branch.root)
    assert turn_terminal.is_showdown
    assert id(turn_terminal) in tiny_flop_to_river_result.chance_data

    river_chance_node = tiny_flop_to_river_result.chance_data[id(turn_terminal)]
    any_river_branch = next(iter(river_chance_node.branches.values()))
    # Either a real river DecisionNode (stack remained) or the turn
    # terminal reused (all-in by the turn) — both are valid, well-formed
    # outcomes; strategy_at handles either via its own uniform fallback
    # for nodes solving never visited (unrelated to chance dispatch, see
    # StrategyResult.strategy_at's own docstring).
    if isinstance(any_river_branch.root, DecisionNode):
        strategy = tiny_flop_to_river_result.strategy_at(any_river_branch.root)
        for freqs in strategy.values():
            assert not any(np.isnan(freq) for freq in freqs.values())
            assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


def test_solve_flop_to_river_all_in_flop_terminal_branches_never_get_a_populated_chance_fn(
    tiny_flop_to_river_result,
):
    # Solver-level version of chance.py's own regression guard: proves
    # the "all-in branch must never get a chance_fn" rule holds through
    # the real solve_flop_to_river call path, not just direct
    # build_chance_node calls. This fixture naturally has an all-in-at-
    # the-flop line (OOP jams, IP calls) — find it and check every one
    # of its branches.
    root = tiny_flop_to_river_result.root
    allin_action = next(a for a in root.legal_actions if a.kind == "all_in")
    after_allin = root.children[allin_action]
    call_action = next(a for a in after_allin.legal_actions if a.kind == CALL_OR_CHECK)
    allin_terminal = after_allin.children[call_action]
    assert allin_terminal.invested == {"OOP": 15.0, "IP": 15.0}

    chance_node = tiny_flop_to_river_result.chance_data[id(allin_terminal)]
    for branch in chance_node.branches.values():
        assert branch.root is allin_terminal
        assert branch.chance_fn is None


def test_solve_flop_to_river_deterministic_given_the_same_inputs():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    hero_range = {HandCombo(Card("7", "s"), Card("7", "c")): 1.0}
    villain_range = {HandCombo(Card("9", "d"), Card("8", "d")): 1.0}
    kwargs = dict(
        board=board,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=10.0,
        effective_stack_bb=15.0,
        positions=("OOP", "IP"),
        raise_sizes=(),
        max_raises=1,
        iterations=20,
    )
    # Every board_equity table this builds — flop, turn, and now river —
    # is either exact (turn/river, per M12's fix) or, for the flop
    # equity_table itself, unused for showdown valuation (every showdown
    # terminal routes through chance dispatch instead) — so there's no
    # sampling randomness left to control here either.
    result_1 = solve_flop_to_river(**kwargs)
    result_2 = solve_flop_to_river(**kwargs)
    assert result_1.opening_range() == result_2.opening_range()


def test_solve_flop_to_river_uses_default_iterations_when_omitted():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    hero_range = {HandCombo(Card("7", "s"), Card("7", "c")): 1.0}
    villain_range = {HandCombo(Card("9", "d"), Card("8", "d")): 1.0}
    result = solve_flop_to_river(
        board=board,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=10.0,
        effective_stack_bb=15.0,
        positions=("OOP", "IP"),
        raise_sizes=(),
        max_raises=1,
    )
    assert result.iterations > 0
    assert result.elapsed_seconds >= 0.0


# ---------------------------------------------------------------------------
# M15 deliverable: StrategyResult.continuing_frequencies + derive_flop_
# scenario — a real preflop solve's per-class continue-frequency, bridged
# into a postflop range via combos.range_from_class_frequencies (M10's own
# documented "bridge" role for that function, never actually exercised by
# a real preflop result until now). Engine only — no api/main.py/frontend
# changes this milestone.
# ---------------------------------------------------------------------------


def _stub_preflop_result(config):
    """A StrategyResult with an empty node_data (nothing solved) — fine
    for tests that only check tree-shape-derived facts (pot, stack,
    which positions/actions exist), not actual solved frequencies."""
    root = build_game_tree(config)
    return StrategyResult(config=config, root=root, hands=_SMALL_HANDS, node_data={}, iterations=0, elapsed_seconds=0.0)


def test_continuing_frequencies_keys_are_starting_hand_objects():
    config = GameConfig(raise_sizes=(), max_raises=1)
    result = solve_preflop(iterations=20, config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE)
    freqs = result.continuing_frequencies(result.root)
    assert all(isinstance(hand, StartingHand) for hand in freqs)
    assert set(freqs.keys()) == set(_SMALL_HANDS)


def test_continuing_frequencies_default_matches_one_minus_fold():
    config = GameConfig(raise_sizes=(), max_raises=1)
    result = solve_preflop(iterations=20, config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE)
    freqs = result.continuing_frequencies(result.root)
    strategy = result.strategy_at(result.root)
    for hand in _SMALL_HANDS:
        expected = 1.0 - strategy[str(hand)]["fold"]
        assert freqs[hand] == pytest.approx(expected)


def test_continuing_frequencies_specific_action_kind_isolates_that_action():
    # Default raise_sizes/max_raises gives root both a sized RAISE and an
    # ALL_IN simultaneously — the exact case action_kind exists to
    # disambiguate (see continuing_frequencies' own docstring).
    config = GameConfig()
    result = solve_preflop(iterations=20, config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE)
    raise_only = result.continuing_frequencies(result.root, action_kind=RAISE)
    overall = result.continuing_frequencies(result.root)
    for hand in _SMALL_HANDS:
        assert raise_only[hand] <= overall[hand] + 1e-9
    # At least one hand's raise-only mass is strictly less than its
    # overall continue mass — proves action_kind actually filters,
    # rather than accidentally summing everything regardless.
    assert any(raise_only[hand] < overall[hand] - 1e-9 for hand in _SMALL_HANDS)


def test_continuing_frequencies_rejects_an_action_kind_not_present_at_the_node():
    config = GameConfig(raise_sizes=(), max_raises=1)  # no sized RAISE at root
    result = solve_preflop(iterations=20, config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE)
    with pytest.raises(ValueError):
        result.continuing_frequencies(result.root, action_kind=RAISE)


def test_continuing_frequencies_falls_back_to_uniform_for_an_unvisited_node():
    config = GameConfig(raise_sizes=(), max_raises=1)
    result = _stub_preflop_result(config)
    freqs = result.continuing_frequencies(result.root)
    num_actions = len(result.root.legal_actions)
    # Uniform strategy -> continue-frequency = every action except fold,
    # i.e. (num_actions - 1) / num_actions (exactly one legal action is
    # fold at this node).
    expected = (num_actions - 1) / num_actions
    for hand in _SMALL_HANDS:
        assert freqs[hand] == pytest.approx(expected)


def test_derive_flop_scenario_rejects_a_multiway_config():
    config = GameConfig(positions=("BTN", "SB", "BB"))
    result = _stub_preflop_result(config)
    with pytest.raises(ValueError):
        derive_flop_scenario(result, "BTN", "BB")


def test_derive_flop_scenario_rejects_a_position_not_in_the_config():
    result = _stub_preflop_result(GameConfig())
    with pytest.raises(ValueError):
        derive_flop_scenario(result, "UTG", "BB")


def test_derive_flop_scenario_rejects_a_raiser_that_isnt_first_to_act():
    # positions=(BTN, BB) by default — BTN acts first, not BB. Passing BB
    # as the raiser would (without this guard) silently walk through
    # BTN's *limp* via node_for_position and derive a scenario for an
    # out-of-scope limped pot instead of raising a clear error.
    result = _stub_preflop_result(GameConfig())
    with pytest.raises(ValueError):
        derive_flop_scenario(result, "BB", "BTN")


def test_derive_flop_scenario_rejects_a_config_with_no_sized_raise():
    config = GameConfig(raise_sizes=(), max_raises=1)
    result = _stub_preflop_result(config)
    with pytest.raises(ValueError):
        derive_flop_scenario(result, "BTN", "BB")


def test_derive_flop_scenario_rejects_a_caller_that_isnt_the_real_next_actor():
    result = _stub_preflop_result(GameConfig())
    with pytest.raises(ValueError):
        derive_flop_scenario(result, "BTN", "BTN")


def test_derive_flop_scenario_happy_path_pot_and_stack_match_hand_computed_values():
    config = GameConfig(stack_bb=100.0, small_blind=0.5, big_blind=1.0, raise_sizes=(2.5, 3.0, 2.2), max_raises=4)
    result = _stub_preflop_result(config)
    scenario = derive_flop_scenario(result, "BTN", "BB")

    raise_size = 2.5 * config.big_blind  # preflop's open_size_reference is big_blind
    assert scenario.raiser_position == "BTN"
    assert scenario.caller_position == "BB"
    assert scenario.pot == pytest.approx(2 * raise_size)
    assert scenario.effective_stack_bb == pytest.approx(config.stack_bb - raise_size)

    # The load-bearing invariant that makes the [raiser_position]
    # indexing choice for effective_stack_bb correct, not arbitrary: a
    # call by definition matches the raiser's total investment.
    raise_action = next(a for a in result.root.legal_actions if a.kind == RAISE)
    caller_node = result.root.children[raise_action]
    call_action = next(a for a in caller_node.legal_actions if a.kind == CALL_OR_CHECK)
    after_call = caller_node.children[call_action]
    assert after_call.invested["BTN"] == pytest.approx(after_call.invested["BB"])


def test_derive_flop_scenario_pipeline_a_premium_hand_continues_far_more_than_trash():
    # Real (small, fast) preflop solve -> derive_flop_scenario -> real
    # combo expansion -> real solve_flop, proving real numbers flow
    # through the whole pipeline, not just that hardcoded ranges still
    # work.
    config = GameConfig(raise_sizes=(2.5,), max_raises=2)
    preflop_result = solve_preflop(iterations=300, config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE)
    scenario = derive_flop_scenario(preflop_result, "BTN", "BB")

    aa = StartingHand("A", "A")
    trash = StartingHand("7", "2", suited=False)
    assert scenario.raiser_range[aa] > scenario.raiser_range[trash]

    # NOT scenario.caller_range[aa] > scenario.caller_range[trash]: caller_range
    # is deliberately CALL_OR_CHECK-*specific* (see derive_flop_scenario's
    # docstring), and a premium hand facing a raise often prefers to
    # 3-bet/jam rather than flat-call — measured here: AA's own
    # call-frequency (~0.005) is actually *lower* than 72o's (~0.19),
    # since almost all of AA's non-fold mass at this node goes to raising
    # instead, exactly the real-poker "premium hands don't just call"
    # intuition, not a bug. The robust check instead uses overall
    # continue-frequency (fold vs. not, action_kind=None) at the same
    # node, which behaves the way intuition expects.
    raise_action = next(a for a in preflop_result.root.legal_actions if a.kind == RAISE)
    caller_node = preflop_result.root.children[raise_action]
    caller_overall_continue = preflop_result.continuing_frequencies(caller_node)
    assert caller_overall_continue[aa] > caller_overall_continue[trash]

    board = (Card("2", "h"), Card("6", "d"), Card("9", "c"))
    exclude = frozenset(board)
    hero_combos = range_from_class_frequencies(scenario.raiser_range, exclude=exclude)
    villain_combos = range_from_class_frequencies(scenario.caller_range, exclude=exclude)

    flop_result = solve_flop(
        board=board,
        hero_range=hero_combos,
        villain_range=villain_combos,
        pot=scenario.pot,
        effective_stack_bb=scenario.effective_stack_bb,
        positions=("OOP", "IP"),
        max_raises=1,
        raise_sizes=(),
        iterations=50,
        equity_samples=50,
    )
    opening = flop_result.opening_range()
    assert len(opening) > 0
    for freqs in opening.values():
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0

    # AA's own combos should carry far more weight in the derived hero
    # range than 72o's — the same directional fact as the class-level
    # check above, now confirmed to survive all the way through combo
    # expansion into the range solve_flop actually solved.
    aa_combos = combos_for_class(aa, exclude=exclude)
    trash_combos = combos_for_class(trash, exclude=exclude)
    aa_weight = sum(hero_combos.get(c, 0.0) for c in aa_combos)
    trash_weight = sum(hero_combos.get(c, 0.0) for c in trash_combos)
    assert aa_weight > trash_weight


# ---------------------------------------------------------------------------
# M16 deliverable: derive_ranges_from_path — generalizes derive_flop_
# scenario (M15) beyond its fixed 2-step "raiser opens, caller calls"
# line to an arbitrary sequence of actions, with any position acting any
# number of times along the way. derive_flop_scenario is now a thin
# wrapper around this (see solver.py); the M15 tests above already prove
# that refactor didn't change its behavior.
# ---------------------------------------------------------------------------


def test_derive_ranges_from_path_rejects_an_action_with_the_wrong_size():
    # Right kind, wrong size — must not be fuzzy-matched by kind alone.
    config = GameConfig()  # default raise_sizes -> root has a real sized RAISE
    result = _stub_preflop_result(config)
    bogus_raise = Action(RAISE, 999.0)
    with pytest.raises(ValueError):
        derive_ranges_from_path(result, [bogus_raise])


def test_derive_ranges_from_path_rejects_continuing_past_a_terminal_node():
    config = GameConfig()
    result = _stub_preflop_result(config)
    fold_action = next(a for a in result.root.legal_actions if a.kind == FOLD)
    with pytest.raises(ValueError):
        derive_ranges_from_path(result, [fold_action, Action(CALL_OR_CHECK)])


def test_derive_ranges_from_path_rejects_fewer_than_two_live_positions():
    config = GameConfig()
    result = _stub_preflop_result(config)
    fold_action = next(a for a in result.root.legal_actions if a.kind == FOLD)
    with pytest.raises(ValueError):
        derive_ranges_from_path(result, [fold_action])


def test_derive_ranges_from_path_three_handed_rejects_a_fold_down_to_one(three_max_result):
    root = three_max_result.root
    raise_action = next(a for a in root.legal_actions if a.kind == RAISE)
    sb_node = root.children[raise_action]
    sb_fold = next(a for a in sb_node.legal_actions if a.kind == FOLD)
    bb_node = sb_node.children[sb_fold]
    bb_fold = next(a for a in bb_node.legal_actions if a.kind == FOLD)
    with pytest.raises(ValueError):
        derive_ranges_from_path(three_max_result, [raise_action, sb_fold, bb_fold])


def test_derive_ranges_from_path_three_handed_all_stay_live(three_max_result):
    root = three_max_result.root
    raise_action = next(a for a in root.legal_actions if a.kind == RAISE)
    sb_node = root.children[raise_action]
    sb_call = next(a for a in sb_node.legal_actions if a.kind == CALL_OR_CHECK)
    bb_node = sb_node.children[sb_call]
    bb_call = next(a for a in bb_node.legal_actions if a.kind == CALL_OR_CHECK)

    scenario = derive_ranges_from_path(three_max_result, [raise_action, sb_call, bb_call])
    assert scenario.live_positions == ("BTN", "SB", "BB")
    assert set(scenario.ranges.keys()) == {"BTN", "SB", "BB"}
    for position in scenario.live_positions:
        assert scenario.stacks[position] == pytest.approx(
            three_max_result.config.stack_bb - scenario.node.invested[position]
        )


def test_derive_ranges_from_path_three_handed_a_fold_reduces_live_positions(three_max_result):
    root = three_max_result.root
    raise_action = next(a for a in root.legal_actions if a.kind == RAISE)
    sb_node = root.children[raise_action]
    sb_fold = next(a for a in sb_node.legal_actions if a.kind == FOLD)
    bb_node = sb_node.children[sb_fold]
    bb_call = next(a for a in bb_node.legal_actions if a.kind == CALL_OR_CHECK)

    scenario = derive_ranges_from_path(three_max_result, [raise_action, sb_fold, bb_call])
    assert scenario.live_positions == ("BTN", "BB")
    assert "SB" not in scenario.ranges


def test_derive_ranges_from_path_multiplies_reach_across_a_positions_own_nodes():
    # BTN opens, BB 3-bets, BTN calls the 3-bet — BTN acts *twice* along
    # this path. Hand-built node_data (not a real solve) with chosen
    # strategy_sum values at both of BTN's own nodes, so the expected
    # answer is exactly hand-computable: this is the direct proof that
    # derive_ranges_from_path multiplies BTN's per-node frequencies
    # together, rather than just reading the last node BTN acted at.
    config = GameConfig(raise_sizes=(2.5, 2.5), max_raises=3)
    root = build_game_tree(config)

    open_raise = next(a for a in root.legal_actions if a.kind == RAISE)
    bb_node = root.children[open_raise]
    three_bet_raise = next(a for a in bb_node.legal_actions if a.kind == RAISE)
    btn_node = bb_node.children[three_bet_raise]
    call_action = next(a for a in btn_node.legal_actions if a.kind == CALL_OR_CHECK)

    def _table_with_one_hand_set(node, action, freq):
        table = InfoSetTable.zeros(len(_SMALL_HANDS), len(node.legal_actions))
        target_idx = node.legal_actions.index(action)
        table.strategy_sum[0, target_idx] = freq
        remaining_actions = len(node.legal_actions) - 1
        for a_idx in range(len(node.legal_actions)):
            if a_idx != target_idx:
                table.strategy_sum[0, a_idx] = (1.0 - freq) / remaining_actions
        return table

    node_data = {
        id(root): _table_with_one_hand_set(root, open_raise, 0.6),
        id(btn_node): _table_with_one_hand_set(btn_node, call_action, 0.5),
        id(bb_node): _table_with_one_hand_set(bb_node, three_bet_raise, 0.7),
    }
    result = StrategyResult(
        config=config, root=root, hands=_SMALL_HANDS, node_data=node_data, iterations=0, elapsed_seconds=0.0
    )

    scenario = derive_ranges_from_path(result, [open_raise, three_bet_raise, call_action])
    aa = _SMALL_HANDS[0]

    # The product (0.6 * 0.5), not either node's reading alone, drives
    # BTN's range for a hand that acted at both of its own nodes.
    assert scenario.ranges["BTN"][aa] == pytest.approx(0.6 * 0.5)

    # BB's range is the single-step control case: BB only acts once
    # along this path, so it must equal continuing_frequencies read
    # directly at BB's own node — no accidental double-multiplication
    # for a position that only acted once.
    direct_bb_freqs = result.continuing_frequencies(bb_node, action_kind=RAISE)
    assert scenario.ranges["BB"][aa] == pytest.approx(direct_bb_freqs[aa])
    assert scenario.ranges["BB"][aa] == pytest.approx(0.7)


def test_derive_ranges_from_path_trained_true_for_a_position_that_never_acted():
    # A position still waiting their own turn at the resulting node has
    # an unconditioned (1.0) range for every hand — nothing derived from
    # solving touched it, so it's trivially trained=True, regardless of
    # whether the *other* position's own nodes were trained or not.
    config = GameConfig(raise_sizes=(), max_raises=1)
    root = build_game_tree(config)
    open_jam = next(a for a in root.legal_actions if a.kind == ALL_IN)
    node_data = {}  # root itself untrained too — irrelevant to BB, who hasn't acted
    result = StrategyResult(
        config=config, root=root, hands=_SMALL_HANDS, node_data=node_data, iterations=0, elapsed_seconds=0.0
    )
    scenario = derive_ranges_from_path(result, [open_jam])
    assert scenario.live_positions == ("BTN", "BB")
    assert all(scenario.trained["BB"].values())


def test_derive_ranges_from_path_trained_reflects_node_training_and_composes_across_steps():
    # BTN opens, BB 3-bets, BTN calls the 3-bet — BTN acts twice. Only
    # the root gets real node_data; btn_node is left untrained
    # (absent from node_data entirely, mirroring an MCCFR path a
    # sampled solve never happened to reach). The direct proof this is
    # meant to be: BTN's own overall trained status is the AND across
    # BOTH of its nodes, not just its last one — one untrained step
    # anywhere along the path is enough to mark the whole derived
    # frequency suspect, the same way one bad factor corrupts a product.
    config = GameConfig(raise_sizes=(2.5, 2.5), max_raises=3)
    root = build_game_tree(config)
    open_raise = next(a for a in root.legal_actions if a.kind == RAISE)
    bb_node = root.children[open_raise]
    three_bet_raise = next(a for a in bb_node.legal_actions if a.kind == RAISE)
    btn_node = bb_node.children[three_bet_raise]
    call_action = next(a for a in btn_node.legal_actions if a.kind == CALL_OR_CHECK)

    real_table = InfoSetTable.zeros(len(_SMALL_HANDS), len(root.legal_actions))
    real_table.strategy_sum[:, root.legal_actions.index(open_raise)] = 1.0
    bb_table = InfoSetTable.zeros(len(_SMALL_HANDS), len(bb_node.legal_actions))
    bb_table.strategy_sum[:, bb_node.legal_actions.index(three_bet_raise)] = 1.0
    # btn_node deliberately absent from node_data — untrained.
    node_data = {id(root): real_table, id(bb_node): bb_table}
    result = StrategyResult(
        config=config, root=root, hands=_SMALL_HANDS, node_data=node_data, iterations=0, elapsed_seconds=0.0
    )

    scenario = derive_ranges_from_path(result, [open_raise, three_bet_raise, call_action])
    aa = _SMALL_HANDS[0]

    # BTN acted at root (trained) and at btn_node (untrained) — overall
    # False, even though its FIRST node was genuinely trained.
    assert scenario.trained["BTN"][aa] is False
    # BB only acted at bb_node, which was trained — overall True.
    assert scenario.trained["BB"][aa] is True


def test_derive_ranges_from_path_trained_keys_match_ranges_keys():
    config = GameConfig(raise_sizes=(), max_raises=1)
    root = build_game_tree(config)
    open_jam = next(a for a in root.legal_actions if a.kind == ALL_IN)
    result = StrategyResult(
        config=config, root=root, hands=_SMALL_HANDS, node_data={}, iterations=0, elapsed_seconds=0.0
    )
    scenario = derive_ranges_from_path(result, [open_jam])
    assert set(scenario.trained.keys()) == set(scenario.ranges.keys())
    for position in scenario.live_positions:
        assert set(scenario.trained[position].keys()) == set(scenario.ranges[position].keys())


def test_derive_ranges_from_path_pipeline_open_3bet_call_feeds_solve_flop():
    # Real (small, fast) preflop solve -> a genuine 3-step path (open,
    # 3-bet, call — one step longer than M15's own 2-step pipeline
    # test, exercising a position, BTN, that acts twice) -> real combo
    # expansion -> real solve_flop.
    config = GameConfig(raise_sizes=(2.5, 2.5), max_raises=3)
    preflop_result = solve_preflop(iterations=300, config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE)

    root = preflop_result.root
    open_raise = next(a for a in root.legal_actions if a.kind == RAISE)
    bb_node = root.children[open_raise]
    three_bet_raise = next(a for a in bb_node.legal_actions if a.kind == RAISE)
    btn_node = bb_node.children[three_bet_raise]
    call_action = next(a for a in btn_node.legal_actions if a.kind == CALL_OR_CHECK)

    scenario = derive_ranges_from_path(preflop_result, [open_raise, three_bet_raise, call_action])
    assert scenario.live_positions == ("BTN", "BB")

    aa = StartingHand("A", "A")
    kk = StartingHand("K", "K")
    trash = StartingHand("7", "2", suited=False)

    # BB's range here is a single-step read (BB's own 3-betting
    # frequency facing the open) — the same directional fact M15 already
    # established: a premium hand 3-bets far more than trash.
    assert scenario.ranges["BB"][aa] > scenario.ranges["BB"][trash]

    # BTN's range here is the *compound* (open AND call-the-3-bet)
    # frequency — NOT simply "premium continues most": measured
    # directly, AA's own weight here is actually tiny (facing a 3-bet
    # with the nuts, AA prefers to 4-bet/jam rather than flat-call — the
    # same "premium hands don't just call" pattern M15's own writeup
    # already found, now showing up again one street of aggression
    # deeper). KK, a strong-but-not-the-nuts hand, is the one that
    # actually wants to flat-call a 3-bet here — so the robust
    # directional check compares KK to trash instead of AA to trash.
    assert scenario.ranges["BTN"][kk] > scenario.ranges["BTN"][trash]

    board = (Card("2", "h"), Card("6", "d"), Card("9", "c"))
    exclude = frozenset(board)
    btn_combos = range_from_class_frequencies(scenario.ranges["BTN"], exclude=exclude)
    bb_combos = range_from_class_frequencies(scenario.ranges["BB"], exclude=exclude)

    flop_result = solve_flop(
        board=board,
        hero_range=btn_combos,
        villain_range=bb_combos,
        pot=scenario.pot,
        effective_stack_bb=scenario.stacks["BTN"],
        positions=("OOP", "IP"),
        max_raises=1,
        raise_sizes=(),
        iterations=50,
        equity_samples=50,
    )
    opening = flop_result.opening_range()
    assert len(opening) > 0
    for freqs in opening.values():
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


# ---------------------------------------------------------------------------
# M63: a CHARACTERIZATION test for a known, unresolved defect.
#
# It asserts that 6-max MCCFR convergence still DIVERGES with more
# iterations — deliberately pinning broken behavior, so that:
#   * the constraint behind MULTIWAY_TABLE_CONFIGS' small budgets is a
#     live, runnable fact rather than a comment nobody executes, and
#   * whoever eventually fixes convergence gets a LOUD failure here
#     telling them the iteration budgets can finally be raised.
#
# If this test fails, that is very likely GOOD NEWS. Re-measure, then
# revisit api/config.py's budgets and docs/project-audit-2026-08-21.md
# recommendation #5.
# ---------------------------------------------------------------------------


def test_six_max_demo_pool_degrades_with_more_iterations():
    # M27 measured AKs's UTG-open fold rate climbing 22.8% (300 iters) ->
    # 69.2% (3k) -> 94.8% (30k) instead of settling. M63 re-measured on
    # current code — AFTER M33/M34's equity fixes, M48's evaluator
    # rewrite and M55's memoization — and reproduced it: 15.6% -> 48.7%
    # -> 92.4%. None of those changes touched the cause.
    #
    # M66 FOUND THE CAUSE, and it is not the solver: it is THIS HAND POOL.
    # _M9_HANDS is 48.6% premium by combo weight (AA/KK/QQ/AKs/AKo out of
    # 8 classes). At 6-max the traverser faces 5 opponents drawn from it,
    # so ~97% of the time at least one holds a premium hand — and folding
    # AKs under the gun really is close to correct in that game. More
    # iterations converge HARDER to a correct answer to a distorted
    # question. See test_six_max_converges_with_a_realistic_pool below,
    # which is the other half of this pair and the actual evidence.
    #
    # This test therefore pins a known property of the shipped demo pool,
    # NOT a solver defect. It is why api/config.py's 6-max budget is 300
    # rather than something larger, and it should keep passing until the
    # demo pool itself is replaced.
    hands = list(_M9_HANDS)
    positions = ("UTG", "MP", "CO", "BTN", "SB", "BB")

    def utg_fold_rate(iterations):
        result = solve_preflop(
            config=GameConfig(positions=positions, stack_bb=100.0),
            hands=hands,
            equity_cache=MultiwayEquityCache(hands=hands, seed=1),
            iterations=iterations,
            seed=1,
        )
        return result.opening_range()["AKs"]["fold"]

    at_shipped_budget = utg_fold_rate(300)
    at_ten_times = utg_fold_rate(3_000)

    # The shipped budget is where the answer is still sane — that is the
    # whole reason it is 300 rather than something larger.
    assert at_shipped_budget < 0.35, (
        f"AKs UTG fold rate at the shipped budget is {at_shipped_budget:.1%}; "
        "if this ever drifts high, the budget itself is no longer a safe choice"
    )
    # ...and 10x more solving makes it materially WORSE, not better.
    assert at_ten_times > at_shipped_budget + 0.15, (
        f"AKs UTG fold rate went {at_shipped_budget:.1%} -> {at_ten_times:.1%} with 10x "
        "the iterations. If this assertion now fails, the demo pool may have been "
        "replaced with a more realistic one — re-measure and revisit the iteration "
        "budgets in api/config.py, which are small only because of this effect."
    )


def test_six_max_converges_with_a_realistic_pool():
    """The other half of the pair above, and the evidence that 6-max MCCFR
    itself is sound: run the SAME solver, at the SAME table size, over a
    hand pool whose premium density is realistic rather than ~49%, and the
    'divergence' disappears entirely.

    M66 measured this three ways. Diluting to 34 classes / 10.2% premium
    made AKs's UTG fold rate 2.5% -> 1.2% -> 1.7% across 300 / 3k / 30k
    iterations — flat at 100x the shipped budget, where the demo pool went
    25.2% -> 67.8% -> 94.5%. A control at the demo pool's own SIZE (8
    classes) but premium-light still degraded, so pool coarseness matters
    too, not density alone; the pool used here is the cheapest
    configuration found that still shows clean convergence, so the suite
    pays ~35s for the finding rather than the ~2.5 minutes the 34-class
    version costs.
    """
    hands = [
        StartingHand("A", "A"), StartingHand("K", "K"), StartingHand("Q", "Q"),
        StartingHand("A", "K", suited=True), StartingHand("A", "K", suited=False),
        StartingHand("T", "9", suited=False), StartingHand("9", "6", suited=False),
        StartingHand("8", "5", suited=False), StartingHand("7", "2", suited=False),
        StartingHand("6", "3", suited=False), StartingHand("3", "2", suited=False),
        StartingHand("J", "8", suited=False), StartingHand("5", "4", suited=False),
        StartingHand("Q", "9", suited=False),
    ]
    positions = ("UTG", "MP", "CO", "BTN", "SB", "BB")

    def utg_fold_rates(iterations):
        result = solve_preflop(
            config=GameConfig(positions=positions, stack_bb=100.0),
            hands=hands,
            equity_cache=MultiwayEquityCache(hands=hands, seed=1),
            iterations=iterations,
            seed=1,
        )
        opening = result.opening_range()
        return {hand: opening[hand]["fold"] for hand in ("AKs", "QQ", "KK")}

    at_shipped_budget = utg_fold_rates(300)
    at_ten_times = utg_fold_rates(3_000)

    for hand in ("AKs", "QQ", "KK"):
        base, more = at_shipped_budget[hand], at_ten_times[hand]
        # The demo pool's failure signature is a LARGE upward climb. Here
        # the rates should stay put or improve — a small upward wobble is
        # ordinary Monte Carlo noise, a 15-point climb is the defect.
        assert more < base + 0.15, (
            f"{hand} UTG fold rate went {base:.1%} -> {more:.1%} with 10x the iterations. "
            "A realistic pool is supposed to converge; if this fails, the instability is "
            "NOT just a demo-pool artifact after all and M66's diagnosis needs revisiting."
        )
        # A strong hand should not be folding under the gun at any point.
        assert more < 0.35, f"{hand} folds {more:.1%} at UTG — implausible for a real pool"
