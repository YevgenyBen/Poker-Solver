"""EV loss: pricing a frequency error in chips."""
import numpy as np
import pytest

from poker_solver.ev import action_values, ev_loss, strategy_ev
from poker_solver.game_tree import StreetConfig, build_street_tree


def _tree(raise_sizes=(2.0,), max_raises=2, pot=10.0, stack=20.0):
    # game_tree requires exactly max_raises - 1 sizes, so one size means
    # max_raises=2 — a bet and the all-in that can follow it.
    return build_street_tree(StreetConfig(
        positions=("OOP", "IP"), pot=pot, stack_bb=stack,
        raise_sizes=raise_sizes, max_raises=max_raises))


def _uniform(_node):
    return None          # strategy_fn returning None means "uniform"


def test_a_hand_that_always_wins_prefers_putting_money_in():
    """The sanity floor: with 100% equity, every action that grows the pot
    must be worth more than checking it down."""
    root = _tree()
    equity = np.array([[1.0]])
    values = action_values(
        root, hero_position=root.player_to_act, hero_index=0, hero_is_a=True,
        equity_table=equity, opp_reach=np.array([1.0]), strategy_fn=_uniform)
    assert values, "no legal actions"
    check = next((v for a, v in values.items() if a.kind == "call_or_check"), None)
    bets = [v for a, v in values.items() if a.kind in ("raise", "all_in")]
    assert check is not None and bets
    assert max(bets) > check, (
        f"a hand with 100% equity should profit from betting: {values}")


def _never_folds(node):
    """An opponent who always takes call_or_check — no fold equity."""
    actions = node.legal_actions
    row = np.zeros(len(actions))
    for i, action in enumerate(actions):
        if action.kind == "call_or_check":
            row[i] = 1.0
    if not row.any():
        row[:] = 1.0 / len(actions)
    return np.tile(row, (1, 1))


def test_a_hand_that_never_wins_prefers_not_to_build_a_pot_it_cannot_win():
    """Against an opponent who NEVER FOLDS, 0% equity must prefer checking.

    The uniform opponent is the wrong control here and the first version of
    this test used it: a uniform opponent folds to the all-in half the
    time, and that fold equity exactly offsets having no showdown equity —
    both actions priced at -5.0, and the code was right. Bluffing is only
    unprofitable when nobody folds.
    """
    root = _tree()
    values = action_values(
        root, hero_position=root.player_to_act, hero_index=0, hero_is_a=True,
        equity_table=np.array([[0.0]]), opp_reach=np.array([1.0]),
        strategy_fn=_never_folds)
    check = next(v for a, v in values.items() if a.kind == "call_or_check")
    bets = [v for a, v in values.items() if a.kind in ("raise", "all_in")]
    assert max(bets) < check, (
        f"0% equity with no fold equity should not build a pot: {values}")


def test_the_second_position_is_priced_in_its_OWN_chips():
    """`equity_table[a, b]` is position A's equity, so B's is one minus the
    transposed entry. Getting that backwards prices hero's hand as the
    opponent's — and it is invisible at 50% equity, so the fixture is
    deliberately lopsided.
    """
    root = _tree()
    equity = np.array([[0.9]])          # A wins 90%, so B wins 10%
    common = dict(equity_table=equity, opp_reach=np.array([1.0]),
                  strategy_fn=_uniform, hero_index=0)
    as_a = action_values(root, hero_position="OOP", hero_is_a=True, **common)
    as_b = action_values(root, hero_position="IP", hero_is_a=False, **common)
    bet_a = max(v for a, v in as_a.items() if a.kind in ("raise", "all_in"))
    bet_b = max(v for a, v in as_b.items() if a.kind in ("raise", "all_in"))
    assert bet_a > bet_b, (
        "the 90% side must price a bet higher than the 10% side; equal or "
        f"inverted means the equity axis is transposed ({bet_a} vs {bet_b})")


def test_loss_is_zero_when_the_two_strategies_agree():
    root = _tree()
    values = action_values(
        root, hero_position=root.player_to_act, hero_index=0, hero_is_a=True,
        equity_table=np.array([[0.6]]), opp_reach=np.array([1.0]),
        strategy_fn=_uniform)
    actions = root.legal_actions
    row = np.full(len(actions), 1.0 / len(actions))
    out = ev_loss(row, row, values, actions)
    assert out["loss_bb"] == pytest.approx(0.0, abs=1e-12)


def test_a_big_frequency_error_between_EQUAL_actions_costs_nothing():
    """The property this whole module exists for.

    A frequency distance of 1.0 — the largest possible — costs exactly
    zero when the actions it moves between are worth the same. Solvers mix
    precisely when actions are near-indifferent, so that is where large
    frequency errors actually live, and it is why frequency distance is a
    convergence measure rather than a quality one.
    """
    actions = ["a", "b"]
    values = {"a": 1.25, "b": 1.25}
    out = ev_loss(np.array([1.0, 0.0]), np.array([0.0, 1.0]), values, actions)
    assert out["loss_bb"] == pytest.approx(0.0)
    assert out["value_spread_bb"] == pytest.approx(0.0)

    # The same frequency error where the actions differ costs the spread.
    values = {"a": 0.0, "b": 2.0}
    out = ev_loss(np.array([1.0, 0.0]), np.array([0.0, 1.0]), values, actions)
    assert out["loss_bb"] == pytest.approx(2.0)
    assert out["value_spread_bb"] == pytest.approx(2.0)


def test_loss_is_signed_so_the_shipped_row_can_price_better():
    """A metric that can only find fault is not measuring."""
    actions = ["a", "b"]
    values = {"a": 3.0, "b": 0.0}
    out = ev_loss(np.array([1.0, 0.0]), np.array([0.0, 1.0]), values, actions)
    assert out["loss_bb"] < 0
    assert out["best_action"] == "a"


def test_loss_ignores_a_constant_offset_in_the_payoffs():
    """`cfr.py` values the second position as minus the first's, offsetting
    it by the dead pot (F45). This module computes the true payoff instead
    — but the loss must not depend on that choice, since a constant across
    hero's actions cancels from a difference of two expectations.
    """
    actions = ["a", "b"]
    shipped, reference = np.array([0.8, 0.2]), np.array([0.1, 0.9])
    base = {"a": 1.0, "b": 2.5}
    shifted = {k: v + 7.5 for k, v in base.items()}
    assert (ev_loss(shipped, reference, base, actions)["loss_bb"]
            == pytest.approx(ev_loss(shipped, reference, shifted, actions)["loss_bb"]))


def test_strategy_ev_weights_by_the_row():
    values = {"a": 1.0, "b": 3.0}
    assert strategy_ev(np.array([0.5, 0.5]), values, ["a", "b"]) == pytest.approx(2.0)
    assert strategy_ev(np.array([0.0, 1.0]), values, ["a", "b"]) == pytest.approx(3.0)


# --- M201: pricing a CHAINED tree -------------------------------------
#
# `ev.py` valued a showdown terminal straight off the equity table, which
# is right for a single-street solve and wrong for `solve_flop_turn` /
# `solve_flop_to_river`, where that terminal is replaced by a whole
# further betting round. Every street-isolation number in M195-M200 is
# denominated in AGGRESSION rather than chips for exactly this reason.

def _chance(branches, pot=10.0, invested=None):
    """A ChanceNode over `branches`, each an (equity_table, subtree) pair."""
    from poker_solver.cards import Card
    from poker_solver.chance import ChanceBranch, ChanceNode
    cards = [Card.from_str(c) for c in ("2c", "3d", "4h", "5s")]
    return ChanceNode(
        pot=pot, invested=invested or {"OOP": 5.0, "IP": 5.0},
        branches={cards[i]: ChanceBranch(card=cards[i], equity_table=eq, root=sub)
                  for i, (eq, sub) in enumerate(branches)})


def test_a_chance_node_is_the_uniform_average_of_its_branches():
    """M12's approximation, and the one ev.py has to reproduce: every
    branch is equally likely, so the node is worth their mean."""
    from poker_solver.ev import _value
    hot, cold = _tree(), _tree()
    node = _chance([(np.array([[1.0]]), hot), (np.array([[0.0]]), cold)])

    def val(n, eq):
        return _value(n, hero_position="OOP", hero_index=0, hero_is_a=True,
                      equity_table=eq, opp_reach=np.array([1.0]),
                      strategy_fn=_uniform, hero_override=None,
                      override_node=None)

    got = val(node, np.array([[0.5]]))
    expected = (val(hot, np.array([[1.0]])) + val(cold, np.array([[0.0]]))) / 2
    assert got == pytest.approx(expected), (
        f"a chance node must average its branches: {got} vs {expected}")


def test_each_branch_is_valued_with_ITS_OWN_equity_table():
    """M165's lesson, transplanted. A branch's board is one card richer
    than the node's, so valuing it on the parent's table answers a
    different question confidently.

    Both branches say hero ALWAYS wins while the node's own table says he
    never does, so the two cannot agree. An earlier version of this test
    used branches of 1.0 and 0.0 against a parent of 0.5 - and passed
    while the bug was present, because EV is linear in equity here and
    their average IS the parent value. A test that a defect can satisfy
    by arithmetic coincidence proves nothing.
    """
    from poker_solver.ev import _value
    node = _chance([(np.array([[1.0]]), _tree()), (np.array([[1.0]]), _tree())])
    parent_table = np.array([[0.0]])

    got = _value(node, hero_position="OOP", hero_index=0, hero_is_a=True,
                 equity_table=parent_table, opp_reach=np.array([1.0]),
                 strategy_fn=_uniform, hero_override=None, override_node=None)

    wrong = _value(_tree(), hero_position="OOP", hero_index=0, hero_is_a=True,
                   equity_table=parent_table, opp_reach=np.array([1.0]),
                   strategy_fn=_uniform, hero_override=None, override_node=None)
    assert got != pytest.approx(wrong), (
        "the branches were valued on the PARENT's equity table - a "
        "one-card-poorer board - which is M165's defect")


def test_a_showdown_terminal_dispatches_only_where_the_solve_BUILT_a_branch():
    """The switch that keeps a chained tree honest.

    cfr turns dispatch off inside a branch's own subtree by passing
    `branch.chance_fn` rather than the ambient one. ev.py gets the same
    behaviour for free by keying on whether `chance_data` HAS an entry:
    `solve_flop_turn` leaves turn terminals unchained, so they must fall
    through to the branch's own equity table rather than dealing a fifth
    street.
    """
    from poker_solver.game_tree import TerminalNode
    from poker_solver.ev import _value
    root = _tree()

    def showdowns(node, out):
        if isinstance(node, TerminalNode):
            if node.is_showdown:
                out.append(node)
            return out
        for child in node.children.values():
            showdowns(child, out)
        return out

    found = showdowns(root, [])
    assert found, "fixture has no showdown terminal to dispatch from"
    term = found[0]

    def val(chance_data):
        return _value(root, hero_position="OOP", hero_index=0, hero_is_a=True,
                      equity_table=np.array([[0.5]]), opp_reach=np.array([1.0]),
                      strategy_fn=_uniform, hero_override=None,
                      override_node=None, chance_data=chance_data)

    plain = val(None)
    assert val({}) == pytest.approx(plain), "an empty dict must not dispatch"
    assert val({id(object()): _chance([(np.array([[1.0]]), _tree())])}) == \
        pytest.approx(plain), "dispatch fired on a terminal with no entry"

    chained = val({id(term): _chance([(np.array([[1.0]]), _tree())])})
    assert chained != pytest.approx(plain), (
        "an entry for this terminal must be recursed into, not ignored")


def test_pricing_a_chained_tree_differs_from_pricing_it_as_one_street():
    """The point of the whole change, on a REAL solve.

    M195-M200 measured street isolation in aggression because this could
    not be priced. If chained and unchained valuations agreed, there would
    have been nothing to measure.
    """
    from poker_solver import solver
    from poker_solver.cards import Card
    from poker_solver.combos import HandCombo, range_from_class_frequencies
    from poker_solver.starting_hands import all_starting_hands
    from poker_solver.warmstart import _walk, index_by_path

    board = tuple(Card.from_str(c) for c in ("Th", "5s", "7c"))
    hero = HandCombo(Card.from_str("Ah"), Card.from_str("Qd"))
    rng = range_from_class_frequencies(
        {h: 1.0 for h in all_starting_hands()[:4]}, exclude=frozenset(board))
    rng.setdefault(hero, min(rng.values()))

    res = solver.solve_flop_turn(
        board=board, hero_range=rng, villain_range=rng, pot=6.0,
        effective_stack_bb=20.0, iterations=2, raise_sizes=(2.0,), max_raises=2)
    assert res.chance_data, "the chained solve recorded no chance branches"

    names = [str(h) for h in res.hands]
    hero_i = names.index(str(hero))
    by_path = index_by_path(res.root, res.node_data)

    def strat(node):
        key = next((p for m, p in _walk(res.root) if m is node), None)
        t = by_path.get(key)
        return None if t is None else t.average_strategy()

    opp = np.array([rng.get(h, 0.0) for h in res.hands], dtype=float)
    eq = np.nan_to_num(res.equity_table, nan=0.5).astype(float) \
        if getattr(res, "equity_table", None) is not None \
        else np.full((len(res.hands), len(res.hands)), 0.5)

    common = dict(hero_position=res.root.player_to_act, hero_index=hero_i,
                  hero_is_a=res.root.player_to_act == res.config.positions[0],
                  equity_table=eq, opp_reach=opp, strategy_fn=strat)
    flat = action_values(res.root, **common)
    chained = action_values(res.root, **common, chance_data=res.chance_data)

    assert set(flat) == set(chained)
    assert any(flat[a] != pytest.approx(chained[a]) for a in flat), (
        "valuing through the turn's betting gave identical numbers to "
        "collapsing it into an averaged equity figure, which would mean "
        "the chance_data was never reached")


def test_a_branch_whose_root_IS_the_terminal_it_replaced_terminates():
    """The bug that an id-only dispatch rule cannot survive.

    On an all-in flop line the turn has no betting left, so
    `build_chance_node` hands back **the very terminal it replaced** as
    every branch's root - measured on a real `solve_flop_turn`: one of
    the seven chance nodes had all 49 branch roots identical to its own
    dispatch key. Deciding to dispatch from `id(node) in chance_data`
    alone then recurses until the stack blows.

    cfr never hits this because it threads `branch.chance_fn`, which is
    None unless the solve chains a further street. ev.py mirrors that with
    its `dispatch` flag. This pins the shape directly rather than through
    a solve, so it stays fast and cannot stop reproducing the case.
    """
    from poker_solver.ev import _value
    from poker_solver.game_tree import TerminalNode

    root = _tree()

    def showdowns(node, out):
        if isinstance(node, TerminalNode):
            if node.is_showdown:
                out.append(node)
            return out
        for child in node.children.values():
            showdowns(child, out)
        return out

    term = showdowns(root, [])[0]
    # Every branch root IS the terminal being replaced — the real shape.
    self_referential = _chance([(np.array([[1.0]]), term),
                                (np.array([[0.0]]), term)])

    value = _value(root, hero_position="OOP", hero_index=0, hero_is_a=True,
                   equity_table=np.array([[0.5]]), opp_reach=np.array([1.0]),
                   strategy_fn=_uniform, hero_override=None, override_node=None,
                   chance_data={id(term): self_referential})
    assert np.isfinite(value), value


def test_dispatch_follows_the_branchs_own_chance_fn_not_the_ambient_one():
    """cfr's per-branch switch, asserted as behaviour rather than trusted.

    A branch with `chance_fn=None` must NOT dispatch inside its subtree
    even when `chance_data` holds an entry for a terminal it contains;
    a branch that chains a further street must. Getting this backwards is
    what `chance.py`'s docstring warns would "double-deal a card off the
    wrong board".
    """
    from poker_solver.cards import Card
    from poker_solver.chance import ChanceBranch, ChanceNode
    from poker_solver.ev import _value
    from poker_solver.game_tree import TerminalNode

    inner_tree = _tree()

    def showdowns(node, out):
        if isinstance(node, TerminalNode):
            if node.is_showdown:
                out.append(node)
            return out
        for child in node.children.values():
            showdowns(child, out)
        return out

    inner_term = showdowns(inner_tree, [])[0]
    deeper = _chance([(np.array([[1.0]]), _tree())])
    data = {id(inner_term): deeper}

    def node_with(chance_fn):
        card = Card.from_str("2c")
        return ChanceNode(pot=10.0, invested={"OOP": 5.0, "IP": 5.0},
                          branches={card: ChanceBranch(
                              card=card, equity_table=np.array([[0.5]]),
                              root=inner_tree, chance_fn=chance_fn)})

    def val(node):
        return _value(node, hero_position="OOP", hero_index=0, hero_is_a=True,
                      equity_table=np.array([[0.5]]), opp_reach=np.array([1.0]),
                      strategy_fn=_uniform, hero_override=None,
                      override_node=None, chance_data=data)

    off = val(node_with(None))
    on = val(node_with(lambda _t: deeper))
    assert off != pytest.approx(on), (
        "dispatch inside a branch ignored the branch's own chance_fn - "
        "an unchained branch must fall through to its equity table")
