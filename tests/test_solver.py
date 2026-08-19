import numpy as np
import pytest

from poker_solver.cards import Card
from poker_solver.combos import HandCombo
from poker_solver.equity import MultiwayEquityCache, build_equity_table
from poker_solver.game_tree import CALL_OR_CHECK, RAISE, DecisionNode, GameConfig, build_game_tree
from poker_solver.solver import (
    DEFAULT_ITERATIONS,
    StrategyResult,
    format_opening_range_grid,
    solve_flop,
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
