"""`bench/leaf_values.py` - what a turn leaf is worth with the river played.

The load-bearing test here is the first one: the walker must reproduce
the engine's OWN terminal convention exactly. If it does not, a depth
study's two arms differ in their accounting as well as their depth, and
that is precisely the confound (F45) the leaf-value seam exists to
remove.
"""
import numpy as np
import pytest

from bench import leaf_values
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


def _equity(n, seed=5):
    rng = np.random.default_rng(seed)
    raw = rng.random((n, n))
    table = (raw + (1 - raw.T)) / 2
    np.fill_diagonal(table, 0.5)
    return table.astype(np.float64)


# -- the convention it must match ---------------------------------------

@pytest.mark.parametrize("n", [3, 6])
def test_a_terminal_is_valued_exactly_as_the_engine_values_it(n):
    """The engine's default is `equity * pot - invested_a`, with a fold
    paying `pot - invested_a` or `-invested_a`. Any other convention here
    would make a depth study measure its own accounting."""
    table = _equity(n)
    for terminal in _terminals(_tree()):
        got = leaf_values.value_matrix(
            terminal, position_a="OOP", position_b="IP",
            equity_table=table, strategy_of=lambda node: None)
        invested = terminal.invested["OOP"]
        if "OOP" in terminal.folded:
            want = np.full((n, n), -invested)
        elif "IP" in terminal.folded:
            want = np.full((n, n), terminal.pot - invested)
        else:
            want = table * terminal.pot - invested
        assert np.array_equal(got, want), "convention drifted from the engine's"


def test_the_matrix_walk_agrees_with_the_engines_own_vector_at_a_terminal():
    """Cross-checked against `cfr._terminal_value_vector` itself rather
    than against a re-derivation of it (M164: a study that rebuilds the
    production path measures its own reconstruction)."""
    n = 5
    table = _equity(n)
    reach = np.array([0.4, 0.3, 0.2, 0.05, 0.05])
    for terminal in _terminals(_tree()):
        matrix = leaf_values.value_matrix(
            terminal, position_a="OOP", position_b="IP",
            equity_table=table, strategy_of=lambda node: None)
        as_a = cfr._terminal_value_vector(terminal, table, "OOP", "IP", True, reach)
        as_b = cfr._terminal_value_vector(terminal, table, "OOP", "IP", False, reach)
        assert np.allclose(matrix @ reach, as_a, atol=1e-12)
        assert np.allclose(-(matrix.T @ reach), as_b, atol=1e-12)


# -- the acting player weights the right axis ---------------------------

def test_each_player_weights_its_own_axis():
    """The table is indexed [a_hand, b_hand], so which axis a strategy
    multiplies is not interchangeable - getting it backwards prices hero
    against their own range, which is the error M275 found costing 7.8 bb
    where the truth was 1.84."""
    n = 2
    table = _equity(n)
    root = _tree()

    # A pure strategy for whoever acts: always the FIRST action.
    def first_action(node):
        strategy = np.zeros((n, len(node.legal_actions)))
        strategy[:, 0] = 1.0
        return strategy

    got = leaf_values.value_matrix(root, position_a="OOP", position_b="IP",
                                   equity_table=table, strategy_of=first_action)
    # Following the first action from the root deterministically lands on
    # one leaf, so the value must equal that leaf's own matrix.
    node = root
    while getattr(node, "children", None):
        node = list(node.children.values())[0]
    want = leaf_values.value_matrix(node, position_a="OOP", position_b="IP",
                                    equity_table=table, strategy_of=first_action)
    assert np.allclose(got, want)


def test_a_hand_specific_strategy_is_applied_per_hand():
    """Row `i` of the strategy belongs to hand `i`. A walker that applied
    one averaged row to every hand would look right on a symmetric
    fixture and be wrong on a real one."""
    n = 2
    table = _equity(n)
    root = _tree()
    actions = len(root.legal_actions)

    def split(node):
        strategy = np.zeros((n, len(node.legal_actions)))
        strategy[0, 0] = 1.0                      # hand 0 takes the first
        strategy[1, min(1, len(node.legal_actions) - 1)] = 1.0
        return strategy

    got = leaf_values.value_matrix(root, position_a="OOP", position_b="IP",
                                   equity_table=table, strategy_of=split)
    assert actions > 1
    assert not np.allclose(got[0], got[1]), "the two hands took different lines"


# -- the collapse that makes it affordable ------------------------------

def test_leaves_that_are_the_same_river_situation_share_a_key():
    """M247 measured 27 showdown terminals collapsing to 8 situations.
    The key is what turns 27 river campaigns into 8."""
    root = _tree()
    showdowns = [t for t in _terminals(root) if not t.folded]
    keys = {leaf_values.leaf_key(t, "OOP") for t in showdowns}
    assert len(keys) < len(showdowns), "no collapse means no affordable study"


def test_two_leaves_with_different_money_do_not_share_a_key():
    """The collapse must not merge leaves whose river would be played at
    a different pot or depth - that is M124's bucket error one street
    later."""
    root = _tree()
    showdowns = [t for t in _terminals(root) if not t.folded]
    pots = {round(t.pot, 6) for t in showdowns}
    if len(pots) > 1:
        keyed = {leaf_values.leaf_key(t, "OOP"): t.pot for t in showdowns}
        assert len({round(p, 6) for p in keyed.values()}) == len(pots)


# -- what it refuses -----------------------------------------------------

def test_a_folded_leaf_is_never_given_a_river():
    """The hand ended there. Solving a river for it would invent a street
    that was never played."""
    root = _tree()
    folded = [t for t in _terminals(root) if t.folded]
    assert folded, "the fixture must contain a fold to test this"
    out = leaf_values.turn_leaf_values(
        turn_board=(), hero_range={}, villain_range={}, positions=("OOP", "IP"),
        effective_stack_bb=20.0, terminals=folded, raise_sizes=(0.75,),
        max_raises=2, iterations=1, deck=())
    assert out == {}


def test_an_all_in_leaf_has_no_river_betting_to_solve():
    """Nothing is behind, so the river has no decisions - valuing it by a
    solve would be solving an empty tree."""
    root = _tree(pot=10.0, stack=20.0)
    showdowns = [t for t in _terminals(root) if not t.folded]
    deepest = max(showdowns, key=lambda t: max(t.invested.values()))
    out = leaf_values.turn_leaf_values(
        turn_board=(), hero_range={}, villain_range={}, positions=("OOP", "IP"),
        effective_stack_bb=max(deepest.invested.values()), terminals=[deepest],
        raise_sizes=(0.75,), max_raises=2, iterations=1, deck=())
    assert out == {}


# -- end to end on a real board -----------------------------------------

def test_a_real_river_value_differs_from_its_equity_default():
    """The whole premise: playing the river out is not the same as
    valuing it at showdown equity. If these matched, M247's 1.75-1.86 bb
    spread could not exist and the route would be dead."""
    from poker_solver.board_equity import build_board_equity_table
    from poker_solver.cards import parse_cards
    from poker_solver.combos import HandCombo

    board = tuple(parse_cards("Kd7c2h9s4d"))
    combos = [HandCombo(*parse_cards(c)) for c in ("AhAs", "QhQd", "3h4s", "8c8d")]
    ranges = {c: 1.0 for c in combos}
    pot, stack = 10.0, 20.0

    solved = leaf_values.solved_value(
        board=board, hero_range=ranges, villain_range=ranges, pot=pot,
        effective_stack_bb=stack, positions=("OOP", "IP"),
        raise_sizes=(0.75,), max_raises=2, iterations=40)

    equity = np.nan_to_num(build_board_equity_table(board, sorted(combos, key=str)),
                           nan=0.5)
    default = equity * pot - 0.0

    assert solved.shape == default.shape
    assert not np.allclose(solved, default), (
        "a solved river equals its showdown-equity default - then there is "
        "nothing for a leaf value to carry")


# -- the fiction masking keeps out of the average ------------------------

def test_a_river_blocked_pair_is_averaged_only_over_rivers_it_can_exist_on():
    """A combo holding the river card cannot exist on that river. The
    engine's own convention gives such a pair equity 0.5 and solves
    anyway - fine for a turn table, where nothing is blocked by a card
    still to come, but averaging it INTO a leaf value imports the
    fiction. Each pair is averaged over the rivers it is live on.
    """
    from poker_solver.cards import Card, parse_cards
    from poker_solver.combos import HandCombo

    turn = tuple(parse_cards("Kd7c2h9s"))
    # One combo holds the 4d, so it is dead on exactly that river.
    combos = [HandCombo(*parse_cards(c)) for c in ("AhAs", "4d5d")]
    ranges = {c: 1.0 for c in combos}
    root = _tree(pot=6.0, stack=12.0)
    leaf = sorted((t for t in _terminals(root) if not t.folded),
                  key=lambda t: t.pot)[0]
    deck = [Card("4", "d"), Card("Qs"[0], "s"), Card("3", "c")]

    out = leaf_values.turn_leaf_values(
        turn_board=turn, hero_range=ranges, villain_range=ranges,
        positions=("OOP", "IP"), effective_stack_bb=12.0, terminals=[leaf],
        raise_sizes=(0.75,), max_raises=2, iterations=5, deck=deck)

    assert out, "the leaf should have a value"
    matrix = next(iter(out.values()))
    assert np.isfinite(matrix).all(), "masking must not leave a division by zero"


def test_a_pair_live_on_no_river_is_refused_rather_than_divided_by_zero():
    """A zero count means the deck was wrong, not that the pair is
    impossible - one card blocks at most two of forty-eight. Refused by
    name rather than returning a NaN that would propagate into a solve.
    """
    from poker_solver.cards import Card, parse_cards
    from poker_solver.combos import HandCombo

    turn = tuple(parse_cards("Kd7c2h9s"))
    combos = [HandCombo(*parse_cards("4d5d"))]
    ranges = {c: 1.0 for c in combos}
    root = _tree(pot=6.0, stack=12.0)
    leaf = sorted((t for t in _terminals(root) if not t.folded),
                  key=lambda t: t.pot)[0]

    with pytest.raises(ValueError, match="live on no river"):
        leaf_values.turn_leaf_values(
            turn_board=turn, hero_range=ranges, villain_range=ranges,
            positions=("OOP", "IP"), effective_stack_bb=12.0, terminals=[leaf],
            raise_sizes=(0.75,), max_raises=2, iterations=5,
            deck=[Card("4", "d")])          # the only river blocks the only combo


def test_each_rivers_equity_table_is_built_once_across_leaves():
    """Most of what this study costs. Cutting the river solve's iterations
    4x saved 10% of the time, so the cost is the TABLE and not CFR -
    M176's inversion one street over. The leaves differ only in pot and
    stack, so a table computed for river card X serves all of them, and
    seven leaves over 48 cards would otherwise build 336 tables where 48
    will do.
    """
    from poker_solver.cards import Card, parse_cards
    from poker_solver.combos import HandCombo

    turn = tuple(parse_cards("Kd7c2h9s"))
    combos = [HandCombo(*parse_cards(c)) for c in ("AhAs", "QhQd", "3h4s")]
    ranges = {c: 1.0 for c in combos}
    root = _tree(pot=6.0, stack=24.0)
    live = [t for t in _terminals(root)
            if not t.folded and 24.0 - max(t.invested.values()) > 1e-9]
    situations = {leaf_values.leaf_key(t, "OOP") for t in live}
    assert len(situations) > 1, "the fixture must have more than one leaf"

    built = []

    def counting(board, combos_, samples, seed=None):
        built.append(tuple(board))
        return np.nan_to_num(
            __import__("poker_solver.board_equity", fromlist=["x"])
            .build_board_equity_table(board, combos_), nan=0.5)

    deck = [Card("4", "d"), Card("5", "c")]
    leaf_values.turn_leaf_values(
        turn_board=turn, hero_range=ranges, villain_range=ranges,
        positions=("OOP", "IP"), effective_stack_bb=24.0, terminals=live,
        raise_sizes=(0.75,), max_raises=2, iterations=3, deck=deck,
        equity_table_fn=counting)

    assert len(set(built)) == len(deck), "one table per river board"
    assert len(built) == len(deck), (
        f"built {len(built)} tables for {len(deck)} boards across "
        f"{len(situations)} leaf situations - the cache is not holding")


# -- the identity that catches an offset ---------------------------------

def test_the_turns_own_investment_is_taken_off_the_river_value():
    """The guard that was missing, and the bug lived in the gap.

    `test_leaf_value_seam` has an identity test for the SEAM - hand it the
    default and the solve must not move. There was no identity test for
    the VALUE BUILDER, and it was wrong: the river subgame is solved with
    `pot = turn_pot` while its own `invested` restarts at zero, so it
    accounted for the turn pot as money A could win and never subtracted
    what A had already paid to reach the leaf.

    That offset differs per leaf, so unlike F45's it does not cancel out
    of regret differences - it flattens them. Left in, a whole campaign's
    depth arm came back as the exactly-uniform prior (0.8 = 4/5 aggressive
    at a five-action node), which is F43's signature and not a finding.

    Tested on ONE river card so the identity is exact and depends on no
    premise about the river's tree. A first version of this test tried to
    disable river betting with `raise_sizes=()` - which does not disable
    it, because all-in is always legal (F40).
    """
    from poker_solver.cards import Card, parse_cards
    from poker_solver.combos import HandCombo

    turn = tuple(parse_cards("Kd7c2h9s"))
    combos = [HandCombo(*parse_cards(c)) for c in ("AhAs", "QhQd", "3s4s")]
    ranges = {c: 1.0 for c in combos}
    stack = 24.0
    root = _tree(pot=6.0, stack=stack)
    live = [t for t in _terminals(root)
            if not t.folded and stack - max(t.invested.values()) > 1e-9]
    # A leaf where the two players put in DIFFERENT amounts: an
    # equal-investment leaf can hide an offset bug.
    leaf = max(live, key=lambda t: max(t.invested.values()))
    invested = leaf.invested["OOP"]
    assert invested > 0, "the fixture must have money in from the turn"

    card = Card("5", "c")
    assert card not in set(turn)
    behind = stack - max(leaf.invested.values())

    built = leaf_values.turn_leaf_values(
        turn_board=turn, hero_range=ranges, villain_range=ranges,
        positions=("OOP", "IP"), effective_stack_bb=stack, terminals=[leaf],
        raise_sizes=(0.75,), max_raises=2, iterations=20, deck=[card])

    raw = leaf_values.solved_value(
        board=tuple(turn) + (card,), hero_range=ranges, villain_range=ranges,
        pot=leaf.pot, effective_stack_bb=behind, positions=("OOP", "IP"),
        raise_sizes=(0.75,), max_raises=2, iterations=20)

    got = next(iter(built.values()))
    assert np.allclose(got, raw - invested, atol=1e-9), (
        "the built value must be the river's value MINUS what A already "
        f"paid on the turn (off by {np.abs(got - (raw - invested)).max():.4f})")
    assert not np.allclose(got, raw), "the investment was not taken off at all"


def test_a_supplied_equity_table_is_nan_scrubbed_like_one_it_builds_itself():
    """The bug the per-board cache introduced, and it is the project's
    most-repeated shape: an optimisation that silently changes the answer.

    A blocked pair's equity is NaN and `solve_flop` replaces it with 0.5
    before solving (M161). `solved_value` used to scrub only the table it
    built ITSELF, so the cache - added to make the study affordable -
    handed NaNs into the walk. NaN regrets make `current_strategy` return
    the uniform prior, so the depth arm came back as exactly 4/5
    aggressive at a five-action node: F43's signature, not a finding.
    """
    from poker_solver.board_equity import build_board_equity_table
    from poker_solver.cards import parse_cards
    from poker_solver.combos import HandCombo

    board = tuple(parse_cards("Kd7c2h9s4h"))
    combos = [HandCombo(*parse_cards(c)) for c in ("AhAs", "QhQd", "3s4s")]
    ordered = sorted(combos, key=str)
    ranges = {c: 1.0 for c in ordered}

    raw = build_board_equity_table(board, ordered)
    assert np.isnan(raw).any(), "the fixture must contain a blocked pair"

    supplied = leaf_values.solved_value(
        board=board, hero_range=ranges, villain_range=ranges, pot=10.0,
        effective_stack_bb=20.0, positions=("OOP", "IP"),
        raise_sizes=(0.75,), max_raises=2, iterations=20, equity_table=raw)
    built = leaf_values.solved_value(
        board=board, hero_range=ranges, villain_range=ranges, pot=10.0,
        effective_stack_bb=20.0, positions=("OOP", "IP"),
        raise_sizes=(0.75,), max_raises=2, iterations=20)

    assert np.isfinite(supplied).all(), "a supplied table must be scrubbed too"
    assert np.allclose(supplied, built), (
        "handing in the table must give the same answer as building it")


def test_a_non_finite_leaf_value_is_refused_at_the_boundary(monkeypatch):
    """A NaN reaching a solve comes back as the uniform prior rather than
    as an error, which is how the cache bug hid. The boundary refuses it.
    """
    from poker_solver.cards import parse_cards
    from poker_solver.combos import HandCombo

    board = tuple(parse_cards("Kd7c2h9s4c"))
    combos = [HandCombo(*parse_cards(c)) for c in ("AhAs", "QhQd")]
    ranges = {c: 1.0 for c in combos}

    monkeypatch.setattr(leaf_values, "value_matrix",
                        lambda *a, **k: np.full((2, 2), np.nan))
    with pytest.raises(ValueError, match="not finite"):
        leaf_values.solved_value(
            board=board, hero_range=ranges, villain_range=ranges, pot=10.0,
            effective_stack_bb=20.0, positions=("OOP", "IP"),
            raise_sizes=(0.75,), max_raises=2, iterations=5)
