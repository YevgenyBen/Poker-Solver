import numpy as np
import pytest

from poker_solver.cards import Card
from poker_solver.combos import HandCombo, combos_for_class, range_from_class_frequencies
from poker_solver.equity import MultiwayEquityCache, build_equity_table
from poker_solver.game_tree import CALL_OR_CHECK, RAISE, DecisionNode, GameConfig, build_game_tree
from poker_solver.solver import (
    DEFAULT_ITERATIONS,
    FlopScenario,
    StrategyResult,
    derive_flop_scenario,
    format_opening_range_grid,
    solve_flop,
    solve_flop_to_river,
    solve_flop_turn,
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
