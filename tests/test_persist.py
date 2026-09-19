"""M284: a solved tree survives the process that solved it.

The property that matters is not "it loads" but "it answers identically":
a stored solve that differs from a fresh one is a different product with
this engine's name on it. So every test here compares STRATEGIES, and the
headline one compares every stored node, bit for bit.
"""
import numpy as np
import pytest

from poker_solver import persist
from poker_solver.equity import MultiwayEquityCache
from poker_solver.game_tree import GameConfig, build_game_tree
from poker_solver.solver import solve_preflop
from poker_solver.starting_hands import StartingHand

HANDS = [
    StartingHand("A", "A"),
    StartingHand("K", "K"),
    StartingHand("A", "K", suited=True),
    StartingHand("T", "9", suited=False),
    StartingHand("7", "2", suited=False),
]
CONFIG = GameConfig(positions=("BTN", "SB", "BB"), stack_bb=40.0)


@pytest.fixture(scope="module")
def solved():
    cache = MultiwayEquityCache(hands=HANDS, samples=60, seed=1)
    result = solve_preflop(config=CONFIG, hands=HANDS, equity_cache=cache,
                           iterations=400, seed=1)
    result.prune_empty_nodes()
    return result


def _by_path(result):
    return {path: node for node, path in persist.built_walk(result.root)}


def test_a_stored_solve_answers_bit_identically(solved):
    arrays = persist.to_portable(solved)
    back = persist.from_portable(arrays, CONFIG, HANDS, build_game_tree)

    original, rebuilt = _by_path(solved), _by_path(back)
    compared = 0
    for path, node in original.items():
        table = solved.node_data.get(id(node))
        if table is None:
            continue
        other = back.node_data[id(rebuilt[path])]
        assert np.array_equal(table.regret_sum, other.regret_sum)
        assert np.array_equal(table.strategy_sum, other.strategy_sum)
        assert np.array_equal(table.average_strategy(), other.average_strategy())
        compared += 1
    assert compared == len(solved.node_data) > 0
    assert len(back.node_data) == len(solved.node_data)


def test_every_table_is_reachable_through_the_built_tree(solved):
    """The whole design rests on this: a solve's tables all sit on nodes
    the solve BUILT, so walking `_built` finds every one of them."""
    reached = {id(node) for node, _ in persist.built_walk(solved.root)}
    assert set(solved.node_data) <= reached


def test_the_walk_builds_nothing(solved):
    """Walking every child of a 9-max tree materialises it and died with
    MemoryError (M216). The walk must read `_built` and never `children[a]`."""
    def built_count(root):
        return sum(len(n.children._built) for n, _ in persist.built_walk(root))

    before = built_count(solved.root)
    list(persist.built_walk(solved.root))
    assert built_count(solved.root) == before
    # And a fresh, never-solved tree has nothing below the root to walk.
    fresh = build_game_tree(CONFIG)
    assert [p for _, p in persist.built_walk(fresh)] == [()]


def test_a_different_hand_pool_is_refused_not_reshaped(solved):
    """Row i of every table means hand i. Reading a solve over another
    pool would hand one hand another's strategy, silently."""
    arrays = persist.to_portable(solved)
    with pytest.raises(ValueError, match="different hand pool"):
        persist.from_portable(arrays, CONFIG, list(reversed(HANDS)), build_game_tree)


def test_a_different_tree_is_refused(solved):
    """A stored path that does not exist in the tree being rebuilt means
    the solve belongs to another game."""
    arrays = persist.to_portable(solved)
    other = GameConfig(positions=("BTN", "BB"), stack_bb=40.0)
    with pytest.raises(ValueError):
        persist.from_portable(arrays, other, HANDS, build_game_tree)


def test_an_unreachable_table_refuses_to_be_stored(solved):
    """Storing the rest would publish a solve with holes in it."""
    import copy
    broken = copy.copy(solved)
    broken.node_data = dict(solved.node_data)
    broken.node_data[-1] = next(iter(solved.node_data.values()))
    with pytest.raises(ValueError, match="not reachable"):
        persist.to_portable(broken)


def test_the_format_version_is_checked(solved):
    arrays = dict(persist.to_portable(solved))
    meta = persist.portable_meta(arrays)
    meta["version"] = persist.FORMAT_VERSION + 1
    import json
    arrays["meta"] = np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8)
    with pytest.raises(ValueError, match="format"):
        persist.from_portable(arrays, CONFIG, HANDS, build_game_tree)
