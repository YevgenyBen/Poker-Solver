"""`bench/price.py` - pricing one real decision against a fuller solve.

The expensive half of that module is a solve and is exercised by the
study that uses it; what is tested here is every part that decides WHAT
is being priced, because each one has a matching failure recorded in
this project's own history: valuing a hand on the wrong board (M165),
building a facing-a-bet reference at the wrong pot (M177), scoring two
different action menus against each other (M202/M241), and leaving
hero's own cards inside the opponent's range (M256).
"""
import numpy as np
import pytest

from bench import price
from poker_solver.cards import parse_cards
from poker_solver.combos import HandCombo


class _Cfg:
    FLOP_RAISE_SIZES = ((0.33, 0.75, 2.5), 3.0, 2.2)
    FLOP_MAX_RAISES = 4
    TURN_STANDALONE_RAISE_SIZES = ((0.33, 0.75, 2.5), 2.0)
    TURN_STANDALONE_MAX_RAISES = 3
    RIVER_STANDALONE_RAISE_SIZES = ((0.33, 0.75, 2.5), 2.0)
    RIVER_STANDALONE_MAX_RAISES = 3


def _body(**extra):
    return {"board": "AhKd7c", "turn_card": "2s", "river_card": "9h", **extra}


def test_the_flop_board_is_the_three_cards_and_nothing_else():
    assert price.street_board(_body(), "flop") == tuple(parse_cards("AhKd7c"))


def test_a_street_is_solved_on_its_own_complete_board():
    """M173/M174: each street solves on the board it is actually on.

    Reading only `board` would value a river hand on the flop and still
    return a confident answer - M165's failure shape.
    """
    assert price.street_board(_body(), "turn") == tuple(parse_cards("AhKd7c2s"))
    assert price.street_board(_body(), "river") == tuple(parse_cards("AhKd7c2s9h"))


def test_each_street_prices_with_its_own_shipped_tree():
    cfg = _Cfg()
    assert price.street_tree_config(cfg, "flop") == (cfg.FLOP_RAISE_SIZES, cfg.FLOP_MAX_RAISES)
    assert price.street_tree_config(cfg, "turn") == (
        cfg.TURN_STANDALONE_RAISE_SIZES, cfg.TURN_STANDALONE_MAX_RAISES)
    assert price.street_tree_config(cfg, "river") == (
        cfg.RIVER_STANDALONE_RAISE_SIZES, cfg.RIVER_STANDALONE_MAX_RAISES)


def test_the_tree_config_is_read_off_the_config_not_copied_from_it():
    """The defect this instrument exists to re-measure is a figure that
    outlived its configuration, so the instrument must not pin one."""
    cfg = _Cfg()
    cfg.TURN_STANDALONE_MAX_RAISES = 99
    assert price.street_tree_config(cfg, "turn")[1] == 99


def test_preflop_is_not_a_street_this_prices():
    with pytest.raises(ValueError, match="postflop"):
        price.street_tree_config(_Cfg(), "preflop")


def _combo(text):
    return HandCombo(*parse_cards(text))


def test_the_opponent_cannot_hold_a_card_hero_holds():
    """M256: hero's own cards left in the runout was worth 8.83% -> 5.31%
    on a realisation figure, and no averaged control could see it."""
    hands = [_combo("AhKh"), _combo("QsQd"), _combo("7c6c")]
    ranges = {"IP": {hands[0]: 1.0, hands[1]: 0.5, hands[2]: 0.25}}
    weights = price.opponent_weights(ranges, "IP", hands, hero_combo=_combo("AhTd"))
    assert list(weights) == [0.0, 0.5, 0.25]


def test_a_hand_outside_the_opponents_range_weighs_nothing():
    hands = [_combo("AhKh"), _combo("QsQd")]
    weights = price.opponent_weights({"IP": {hands[0]: 1.0}}, "IP", hands)
    assert list(weights) == [1.0, 0.0]


class _Node:
    """A two-action toy node; `children` is filled by the builder below."""

    def __init__(self, player, actions):
        self.player_to_act = player
        self.legal_actions = actions
        self.children = {}


def _two_level_tree():
    """OOP acts, then IP acts. Labels are plain strings."""
    leaf_a, leaf_b = _Node("IP", []), _Node("IP", [])
    ip = _Node("IP", ["fold", "call"])
    ip.children = {"fold": leaf_a, "call": leaf_b}
    root = _Node("OOP", ["check", "bet"])
    root.children = {"check": _Node("IP", []), "bet": ip}
    return root, ip


def test_the_opponents_action_probability_multiplies_their_reach_per_hand():
    """M161's step: an opponent's mix is per-HAND, so it scales the reach
    vector rather than weighting the branch."""
    root, ip = _two_level_tree()
    tables = {id(ip): np.array([[0.25, 0.75], [1.0, 0.0]])}
    node, reach = price.reach_to(
        root, ["bet", "call"], opp_position="IP",
        opp_reach=np.array([1.0, 0.4]),
        strategy_fn=lambda n: tables.get(id(n)))
    assert node is ip.children["call"]
    assert reach == pytest.approx([0.75, 0.0])


def test_heros_own_actions_leave_the_opponents_reach_alone():
    """Hero taking an action says nothing about which hand the opponent
    holds, so conditioning on it must not reweight their range."""
    root, _ip = _two_level_tree()
    node, reach = price.reach_to(
        root, ["bet"], opp_position="IP", opp_reach=np.array([1.0, 0.4]),
        strategy_fn=lambda n: np.array([[0.1, 0.9], [0.2, 0.8]]))
    assert reach == pytest.approx([1.0, 0.4])
    assert node.player_to_act == "IP"


def test_an_untrained_opponent_node_splits_their_reach_evenly():
    """`ev.py` reads a missing table as the uniform prior; the reach walk
    has to agree with it or the two halves price different strategies."""
    root, ip = _two_level_tree()
    _node, reach = price.reach_to(
        root, ["bet", "fold"], opp_position="IP", opp_reach=np.array([1.0, 0.4]),
        strategy_fn=lambda n: None)
    assert reach == pytest.approx([0.5, 0.2])


def test_an_unvisited_node_has_no_strategy_rather_than_a_zero_one():
    """A zero table would price as "takes no action at all"; None is what
    `ev.py` reads as "has formed no preference"."""
    class _Result:
        node_data = {}
    assert price.strategy_fn_for(_Result())(_Node("OOP", [])) is None


def test_a_visited_node_prices_with_its_average_strategy():
    class _Table:
        def average_strategy(self):
            return np.array([[0.3, 0.7]])

    node = _Node("OOP", ["check", "bet"])

    class _Result:
        node_data = {id(node): _Table()}

    assert price.strategy_fn_for(_Result())(node)[0] == pytest.approx([0.3, 0.7])


def test_mass_the_reference_node_cannot_express_is_reported_not_absorbed():
    """M202/M241: comparing two different action menus is comparing two
    games, so the study has to be able to see it and refuse the row."""
    row, remapped = price._row_over(
        ["fold", "call_or_check"], {"fold": 0.2, "call_or_check": 0.5, "raise:9.90": 0.3})
    assert list(row) == [0.2, 0.5]
    assert remapped == pytest.approx(0.3)


def test_a_matching_menu_remaps_nothing():
    _row, remapped = price._row_over(["fold", "call_or_check"],
                                     {"fold": 0.2, "call_or_check": 0.8})
    assert remapped == pytest.approx(0.0)
