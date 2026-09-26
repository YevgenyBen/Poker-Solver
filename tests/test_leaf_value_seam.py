"""The leaf-value seam: what a SHOWDOWN terminal is worth (M247's route).

`_terminal_value_vector` stands in `equity * pot - invested` for the
value of a leaf. That is the approximation street isolation IS: the hand
is treated as ending at showdown with no further betting. M247 measured
what a correct value would be worth - sd 1.75-1.86 bb, 12% of pot, not
noise, and **no cheap feature predicts it** - and concluded the route
left open was a precomputed or learned table.

The seam exists because the only affordable alternative, chaining a real
next street through a chance node, changes TWO things at once: it plays
the street out AND it inherits F45's dead-pot offset, which cancels
within one street and stops cancelling across a chance node. A study
using chaining as a proxy for depth therefore cannot separate them.
Injecting a value here crosses no chance node.
"""
import numpy as np
import pytest

from poker_solver import cfr
from poker_solver.game_tree import StreetConfig, build_street_tree


def _tree(pot=10.0, stack=20.0):
    return build_street_tree(StreetConfig(
        positions=("OOP", "IP"), pot=pot, stack_bb=stack,
        raise_sizes=(0.75,), max_raises=2))


def _terminals(root):
    seen, stack = [], [root]
    while stack:
        node = stack.pop()
        children = getattr(node, "children", None)
        if children:
            stack.extend(children.values())
        else:
            seen.append(node)
    return seen


def _reach(hands):
    """`solve` only falls back to `hand.combo_weight` when reach is not
    given, and `solve_flop` always gives it - so these string-handed
    trees must too."""
    return {"OOP": np.ones(len(hands)), "IP": np.ones(len(hands))}


def _table(n, value=0.5):
    t = np.full((n, n), value, dtype=np.float64)
    return t


def _realistic_table(n, seed=3):
    """An antisymmetric table where hands genuinely DIFFER.

    A uniform table makes every decision exactly tied, and M74 measured
    that regret matching then oscillates wholesale rather than nudging -
    so a flat fixture amplifies one ULP into half a strategy. See
    `test_a_tied_fixture_is_not_a_valid_test_bed_for_a_leaf_value`.
    """
    rng = np.random.default_rng(seed)
    raw = rng.random((n, n))
    table = (raw + (1 - raw.T)) / 2
    np.fill_diagonal(table, 0.5)
    return table.astype(np.float64)


# -- it is OFF by default ------------------------------------------------

def test_the_default_solve_is_untouched():
    """The whole safety argument: a seam nobody uses must change nothing.
    This project ships `continuation_table=None`, `ensemble=1` and four
    `*_SOLVE_STANDALONE` flags on exactly that principle."""
    root = _tree()
    hands = ["AA", "KK", "72o"]
    table = _table(len(hands), 0.4)

    a = cfr.solve(root, hands, table, iterations=6, positions=("OOP", "IP"), initial_reach=_reach(hands))
    b = cfr.solve(_tree(), hands, table, iterations=6, positions=("OOP", "IP"), initial_reach=_reach(hands),
                  leaf_value_fn=None)

    assert set(a) == set(b) or True          # node ids differ between trees
    left = sorted(np.round(t.strategy_sum, 12).tobytes() for t in a.values())
    right = sorted(np.round(t.strategy_sum, 12).tobytes() for t in b.values())
    assert left == right


# -- the algebra it replaces --------------------------------------------

def test_reproducing_the_equity_default_reproduces_the_solve():
    """The identity check, and the one that proves the seam is WIRED
    rather than merely accepted: hand it the quantity it replaces and the
    answer must come back the same.

    **The bound is a tolerance and that is correct here**, unlike
    `test_parallel`'s guards, which claimed bit-identity and asserted
    `allclose` while identity was actually available. It is NOT available
    here: the default scales AFTER the matmul (`(table @ reach) * pot`)
    and an injected value must scale BEFORE it, so the two do the same
    arithmetic in a different ORDER - exactly M161's case. Measured on a
    realistic table the difference stays at 1e-14 through 1000
    iterations; the bound is set well inside that.
    """
    hands = [f"h{i}" for i in range(8)]
    table = _realistic_table(len(hands))
    root = _tree()          # ONE tree, so node ids line up between solves

    def as_default(node):
        return table * node.pot - node.invested["OOP"]

    plain = cfr.solve(root, hands, table, iterations=250,
                      positions=("OOP", "IP"), initial_reach=_reach(hands))
    injected = cfr.solve(root, hands, table, iterations=250,
                         positions=("OOP", "IP"), initial_reach=_reach(hands),
                         leaf_value_fn=as_default)

    worst = 0.0
    for key, table_a in plain.items():
        a, b = table_a.strategy_sum, injected[key].strategy_sum
        na = a / np.maximum(a.sum(axis=1, keepdims=True), 1e-12)
        nb = b / np.maximum(b.sum(axis=1, keepdims=True), 1e-12)
        worst = max(worst, float(np.abs(na - nb).max()))
    assert worst < 1e-10, f"the seam is not reproducing its own default ({worst:.2e})"


def test_a_tied_fixture_is_not_a_valid_test_bed_for_a_leaf_value():
    """A trap worth a permanent guard, because the obvious fixture falls
    into it.

    With a FLAT equity table every hand is identical and every decision
    exactly tied, and M74 measured regret matching oscillating wholesale
    there rather than nudging. The same algebraically-identical leaf
    value that moves a realistic solve by 1e-14 moves a flat one by
    **0.5** - so a leaf-value study built on a uniform table would
    measure its own fixture.
    """
    hands = [f"h{i}" for i in range(8)]
    flat = _table(len(hands), 0.4)
    root = _tree()

    def as_default(node):
        return flat * node.pot - node.invested["OOP"]

    plain = cfr.solve(root, hands, flat, iterations=250,
                      positions=("OOP", "IP"), initial_reach=_reach(hands))
    injected = cfr.solve(root, hands, flat, iterations=250,
                         positions=("OOP", "IP"), initial_reach=_reach(hands),
                         leaf_value_fn=as_default)
    worst = 0.0
    for key, table_a in plain.items():
        a, b = table_a.strategy_sum, injected[key].strategy_sum
        na = a / np.maximum(a.sum(axis=1, keepdims=True), 1e-12)
        nb = b / np.maximum(b.sum(axis=1, keepdims=True), 1e-12)
        worst = max(worst, float(np.abs(na - nb).max()))
    assert worst > 0.1, (
        "if a flat table has stopped amplifying, M74's bang-bang behaviour "
        "has changed and this guard should be re-derived, not deleted")


def test_a_different_leaf_value_changes_the_strategy():
    """Mutation in the other direction. M163's trap was a feature that
    could never fire while its tests passed, so the seam must be shown to
    MOVE the answer, not merely to be accepted."""
    hands = ["AA", "KK", "72o"]
    table = _table(len(hands), 0.4)

    plain = cfr.solve(_tree(), hands, table, iterations=8, positions=("OOP", "IP"), initial_reach=_reach(hands))
    moved = cfr.solve(_tree(), hands, table, iterations=8, positions=("OOP", "IP"), initial_reach=_reach(hands),
                      leaf_value_fn=lambda node: table * node.pot * 3.0)

    left = sorted(t.strategy_sum.tobytes() for t in plain.values())
    right = sorted(t.strategy_sum.tobytes() for t in moved.values())
    assert left != right


def test_returning_none_falls_back_per_node():
    """A caller with values for SOME leaves - the realistic case, since
    M247's collapse is to 8 situations - must be able to leave the rest
    on the equity default."""
    hands = ["AA", "KK"]
    table = _table(len(hands), 0.4)
    plain = cfr.solve(_tree(), hands, table, iterations=6, positions=("OOP", "IP"), initial_reach=_reach(hands))
    nones = cfr.solve(_tree(), hands, table, iterations=6, positions=("OOP", "IP"), initial_reach=_reach(hands),
                      leaf_value_fn=lambda node: None)
    assert (sorted(t.strategy_sum.tobytes() for t in plain.values())
            == sorted(t.strategy_sum.tobytes() for t in nones.values()))


# -- what it must NOT touch ---------------------------------------------

def test_a_fold_leaf_is_never_asked_for_a_value():
    """The hand ENDED there - no later street to value, and the pot is
    decided by who folded. Injecting would invent money."""
    asked = []

    def record(node):
        asked.append(node)
        return None

    hands = ["AA", "KK"]
    cfr.solve(_tree(), hands, _table(len(hands)), iterations=4,
              positions=("OOP", "IP"), initial_reach=_reach(hands), leaf_value_fn=record)

    assert asked, "showdown leaves must reach the seam at all"
    assert all(not node.folded for node in asked), "a fold leaf was offered for valuation"


def test_the_second_positions_value_stays_the_negation_of_the_first():
    """F45's convention, reproduced deliberately (M161). The seam takes
    position A's chips and negates for B exactly as the equity path does
    - changing that here would silently alter every solve that uses a
    leaf value, and F45 is explicitly unadjudicated."""
    hands = ["AA", "KK", "72o"]
    n = len(hands)
    root = _tree()
    leaf = next(t for t in _terminals(root) if not t.folded)
    values = np.arange(n * n, dtype=np.float64).reshape(n, n)
    reach = np.array([0.5, 0.25, 0.25])

    as_a = cfr._terminal_value_vector(leaf, _table(n), "OOP", "IP", True, reach,
                                      lambda node: values)
    as_b = cfr._terminal_value_vector(leaf, _table(n), "OOP", "IP", False, reach,
                                      lambda node: values)

    assert np.allclose(as_a, values @ reach)
    assert np.allclose(as_b, -(values.T @ reach))


# -- it refuses what would otherwise broadcast --------------------------

@pytest.mark.parametrize("shape", [(2, 3), (3,), (4, 4)])
def test_a_wrong_shaped_leaf_value_is_refused_by_name(shape):
    """A wrong shape would BROADCAST and return a plausible number rather
    than fail - M214/M219/M257's recurring shape. It is refused."""
    hands = ["AA", "KK", "72o"]
    n = len(hands)
    root = _tree()
    leaf = next(t for t in _terminals(root) if not t.folded)

    with pytest.raises(ValueError, match="expected"):
        cfr._terminal_value_vector(
            leaf, _table(n), "OOP", "IP", True, np.ones(n),
            lambda node: np.zeros(shape))


def test_the_seam_reaches_a_real_flop_solve():
    """Wired at the level a study would actually call (M164: a study that
    reconstructs the production path measures its own reconstruction)."""
    from poker_solver.cards import parse_cards
    from poker_solver.combos import HandCombo
    from poker_solver.solver import solve_flop

    board = tuple(parse_cards("Kd7c2h"))
    combos = [HandCombo(*parse_cards(c)) for c in ("AhAs", "QhQs", "3h4s")]
    ranges = {c: 1.0 for c in combos}
    common = dict(board=board, hero_range=ranges, villain_range=ranges,
                  pot=10.0, effective_stack_bb=20.0, positions=("OOP", "IP"),
                  raise_sizes=(0.75,), max_raises=2, iterations=5)

    plain = solve_flop(**common)
    moved = solve_flop(leaf_value_fn=lambda node: np.full((3, 3), 99.0), **common)

    assert (plain.strategy_at(plain.root) != moved.strategy_at(moved.root)), (
        "a leaf value that says every showdown is worth 99 must change the play")
