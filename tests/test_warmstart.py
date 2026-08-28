"""M158: reusing a solved tree's accumulators for a related solve.

A flop request cost ~17s at the median and 64.5s at p90 (M155), against
the 15-30 seconds a player at a table has to act — so one decision in ten
could not be answered before it had to be made. The CFR solve is 86% of
that cost and 71 of 73 flop requests paid it cold, because hero's combo
is force-included before the cap and hero's class therefore partitions
the cache (M76).

Warm-starting from an earlier solve of the same canonical spot takes a
repeat request to ~2.7s. These tests pin the two properties that make it
sound rather than merely fast.
"""
import numpy as np
import pytest

from poker_solver.cards import Card
from poker_solver.cfr import InfoSetTable, solve
from poker_solver.combos import HandCombo
from poker_solver.game_tree import StreetConfig, build_street_tree
from poker_solver.board_equity import build_board_equity_table
from poker_solver.warmstart import graft_node_data, index_by_path

BOARD = tuple(Card.from_str(t) for t in ("Kd", "7c", "2h"))
POSITIONS = ("OOP", "IP")


def _combos(specs):
    return [HandCombo(Card.from_str(a), Card.from_str(b)) for a, b in specs]


POOL = _combos([("9s", "9d"), ("Qd", "Qh"), ("Ah", "Kh"), ("Ts", "Tc"),
                ("5s", "4d"), ("Jc", "Jh"), ("8s", "8d"), ("Ac", "Qc")])
HERO = HandCombo(Card.from_str("6s"), Card.from_str("6h"))


def _setup(combos, iterations, initial_node_data=None):
    table = np.nan_to_num(
        build_board_equity_table(BOARD, combos, samples=12), nan=0.5)
    config = StreetConfig(positions=POSITIONS, pot=6.0, stack_bb=40.0,
                          raise_sizes=(2.5, 3.0, 2.2), max_raises=4)
    root = build_street_tree(config)
    reach = {p: np.ones(len(combos)) for p in POSITIONS}
    node_data = solve(root, combos, table, iterations=iterations,
                      positions=POSITIONS, initial_reach=reach,
                      initial_node_data=initial_node_data)
    return root, node_data


def _row(root, combos, node_data, hand):
    from poker_solver.solver import StrategyResult
    config = StreetConfig(positions=POSITIONS, pot=6.0, stack_bb=40.0,
                          raise_sizes=(2.5, 3.0, 2.2), max_raises=4)
    result = StrategyResult(config=config, root=root, hands=combos,
                            node_data=node_data, iterations=1, elapsed_seconds=0.0)
    return result.strategy_at(root).get(str(hand)) or {}


def test_a_warm_started_solve_lands_where_a_cold_one_does():
    """The property the speedup rests on.

    Warm-starting is only legitimate if refining a cached solve of the
    same spot reaches the answer a cold solve reaches. Measured at
    production settings across three boards, hero's row differed by
    0.0011-0.0155 — inside the 0.024-0.112 that a seed-only re-run of the
    IDENTICAL solve moves hands (M155). Here the same comparison runs at
    a scale the suite can afford.

    The tolerance is deliberately not zero: this solver does not deliver
    exact agreement across equity seeds either, so demanding it of a warm
    start would assert something the cold path cannot meet.
    """
    with_hero = sorted(POOL + [HERO], key=str)

    # Cold: the full solve every request pays today.
    cold_root, cold_data = _setup(with_hero, 400)
    cold = _row(cold_root, with_hero, cold_data, HERO)

    # Warm: solve the spot WITHOUT hero, then graft onto a fresh tree and
    # refine briefly. The graft must key onto the tree actually being
    # solved, since node_data is keyed by id(node).
    spot_root, spot_data = _setup(POOL, 400)
    by_path = index_by_path(spot_root, spot_data)
    warm_root = build_street_tree(
        StreetConfig(positions=POSITIONS, pot=6.0, stack_bb=40.0,
                     raise_sizes=(2.5, 3.0, 2.2), max_raises=4))
    grafted = graft_node_data(warm_root, by_path, POOL, with_hero)
    assert grafted, "nothing grafted — the path keys did not line up"

    table = np.nan_to_num(build_board_equity_table(BOARD, with_hero, samples=12), nan=0.5)
    warm_data = solve(warm_root, with_hero, table, iterations=50, positions=POSITIONS,
                      initial_reach={p: np.ones(len(with_hero)) for p in POSITIONS},
                      initial_node_data=grafted)
    warm = _row(warm_root, with_hero, warm_data, HERO)

    keys = set(cold) | set(warm)
    assert keys, "hero got no row from either path"
    drift = max(abs(cold.get(k, 0.0) - warm.get(k, 0.0)) for k in keys)
    assert drift <= 0.15, (
        f"a warm-started hero row drifted {drift:.4f} from a cold solve — the shared "
        "start is biasing the answer rather than reusing work"
    )


def test_grafting_gives_a_new_hand_no_inherited_regret():
    """A hand the cached solve never saw must start from zero.

    Otherwise hero would inherit whichever combo happened to occupy its
    index in the cached pool — advice for someone else's hand, which is
    M76's bug arriving by a different route.
    """
    root, node_data = _setup(POOL, 30)
    by_path = index_by_path(root, node_data)
    with_hero = sorted(POOL + [HERO], key=str)
    fresh = build_street_tree(
        StreetConfig(positions=POSITIONS, pot=6.0, stack_bb=40.0,
                     raise_sizes=(2.5, 3.0, 2.2), max_raises=4))
    grafted = graft_node_data(fresh, by_path, POOL, with_hero)

    hero_index = with_hero.index(HERO)
    assert grafted, "nothing grafted"
    for table in grafted.values():
        assert not table.regret_sum[hero_index].any(), (
            "hero inherited regret from a hand it is not — the graft is "
            "mis-indexing the new pool"
        )
        assert not table.strategy_sum[hero_index].any()


def test_grafting_drops_tables_whose_tree_shape_changed():
    """A cached table from a different game is not a starting point.

    A differing action count means a different tree — another stack depth
    or raise menu — so those tables are dropped rather than reshaped.
    `_solve_recurse` creates a zero table for anything absent, so dropping
    is always safe.
    """
    root, node_data = _setup(POOL, 20)
    by_path = index_by_path(root, node_data)
    # Corrupt every cached table's action count.
    wrong = {path: InfoSetTable(regret_sum=np.zeros((len(POOL), 99)),
                                strategy_sum=np.zeros((len(POOL), 99)),
                                last_regret=None, last_strategy=None)
             for path in by_path}
    fresh = build_street_tree(
        StreetConfig(positions=POSITIONS, pot=6.0, stack_bb=40.0,
                     raise_sizes=(2.5, 3.0, 2.2), max_raises=4))
    assert graft_node_data(fresh, wrong, POOL, POOL) == {}


def test_solve_rejects_warm_data_shaped_for_a_different_pool():
    """Silent broadcasting would corrupt every row.

    Passing tables sized for another hand count is a programming error,
    and this session has seen enough fixes that silently did nothing.
    """
    root, node_data = _setup(POOL, 20)
    table = np.nan_to_num(build_board_equity_table(BOARD, POOL, samples=8), nan=0.5)
    wrong_pool = {key: InfoSetTable(regret_sum=np.zeros((len(POOL) + 3, 2)),
                                    strategy_sum=np.zeros((len(POOL) + 3, 2)),
                                    last_regret=None, last_strategy=None)
                  for key in list(node_data)[:1]}
    with pytest.raises(ValueError, match="hand rows"):
        # `initial_reach` is required for combo-keyed pools: without it
        # `solve` falls back to `hand.combo_weight`, which only
        # StartingHand has (documented in cfr.solve). Supplying it keeps
        # this test on the assertion it is actually about.
        solve(root, POOL, table, iterations=1, positions=POSITIONS,
              initial_reach={p: np.ones(len(POOL)) for p in POSITIONS},
              initial_node_data=wrong_pool)
