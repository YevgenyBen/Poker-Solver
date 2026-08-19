import pytest

from poker_solver.game_tree import (
    ALL_IN,
    BB,
    BTN,
    CALL_OR_CHECK,
    FOLD,
    RAISE,
    Action,
    DecisionNode,
    GameConfig,
    StreetConfig,
    TerminalNode,
    build_game_tree,
    build_street_tree,
    count_terminal_nodes,
    tree_depth,
    walk,
)


# ---------------------------------------------------------------------------
# GameConfig validation
# ---------------------------------------------------------------------------


def test_default_config_is_valid():
    GameConfig()  # should not raise


def test_default_config_is_heads_up():
    assert GameConfig().positions == (BTN, BB)
    assert GameConfig().num_players == 2


def test_config_rejects_wrong_raise_sizes_length():
    with pytest.raises(ValueError):
        GameConfig(max_raises=4, raise_sizes=(2.5, 3.0))  # needs 3 entries


def test_config_rejects_max_raises_below_one():
    with pytest.raises(ValueError):
        GameConfig(max_raises=0, raise_sizes=())


def test_config_rejects_stack_not_greater_than_small_blind():
    with pytest.raises(ValueError):
        GameConfig(stack_bb=0.5, small_blind=0.5, raise_sizes=())


def test_config_rejects_nonpositive_blinds():
    with pytest.raises(ValueError):
        GameConfig(small_blind=0, raise_sizes=())


def test_config_rejects_fewer_than_two_positions():
    with pytest.raises(ValueError):
        GameConfig(positions=("BTN",), raise_sizes=())


def test_config_rejects_duplicate_positions():
    with pytest.raises(ValueError):
        GameConfig(positions=("BTN", "BTN", "BB"), raise_sizes=())


# ---------------------------------------------------------------------------
# TerminalNode.payoff
# ---------------------------------------------------------------------------


def test_payoff_btn_folds():
    node = TerminalNode(pot=1.5, invested={"BTN": 0.5, "BB": 1.0}, folded=frozenset({"BTN"}))
    assert node.payoff({}) == {"BTN": -0.5, "BB": 0.5}


def test_payoff_bb_folds():
    node = TerminalNode(pot=1.5, invested={"BTN": 0.5, "BB": 1.0}, folded=frozenset({"BB"}))
    # BTN wins the whole pot: net = pot - btn_invested = 1.0.
    assert node.payoff({}) == {"BTN": 1.0, "BB": -1.0}


def test_payoff_fold_is_zero_sum_multiway():
    node = TerminalNode(
        pot=15.0,
        invested={"BTN": 5.0, "SB": 5.0, "BB": 5.0},
        folded=frozenset({"BTN", "SB"}),
    )
    payoffs = node.payoff({})
    assert sum(payoffs.values()) == pytest.approx(0.0)
    assert payoffs["BB"] == pytest.approx(10.0)  # wins BTN's and SB's chips
    assert payoffs["BTN"] == pytest.approx(-5.0)
    assert payoffs["SB"] == pytest.approx(-5.0)


def test_payoff_showdown_uses_payoff_fn():
    node = TerminalNode(pot=10.0, invested={"BTN": 5.0, "BB": 5.0}, folded=frozenset())
    # Stub: the first live hand gets 70% of the pot, the second 30%.
    payoff_fn = lambda hands, pot: [0.7 * pot, 0.3 * pot]
    payoffs = node.payoff({"BTN": "AA", "BB": "72o"}, payoff_fn)
    assert payoffs["BTN"] == pytest.approx(7.0 - 5.0)
    assert payoffs["BB"] == pytest.approx(3.0 - 5.0)
    assert sum(payoffs.values()) == pytest.approx(0.0)


def test_payoff_showdown_multiway_with_a_folder():
    # BTN folded earlier; SB and BB go to showdown.
    node = TerminalNode(
        pot=12.0,
        invested={"BTN": 2.0, "SB": 5.0, "BB": 5.0},
        folded=frozenset({"BTN"}),
    )
    payoff_fn = lambda hands, pot: [0.4 * pot, 0.6 * pot]  # [SB share, BB share]
    payoffs = node.payoff({"SB": "KK", "BB": "QQ"}, payoff_fn)
    assert payoffs["BTN"] == pytest.approx(-2.0)
    assert payoffs["SB"] == pytest.approx(0.4 * 12.0 - 5.0)
    assert payoffs["BB"] == pytest.approx(0.6 * 12.0 - 5.0)
    assert sum(payoffs.values()) == pytest.approx(0.0)


def test_payoff_showdown_without_payoff_fn_raises():
    node = TerminalNode(pot=10.0, invested={"BTN": 5.0, "BB": 5.0}, folded=frozenset())
    with pytest.raises(ValueError):
        node.payoff({"BTN": "AA", "BB": "72o"})


def test_terminal_is_showdown_property():
    fold_node = TerminalNode(pot=1, invested={"BTN": 1, "BB": 1}, folded=frozenset({"BTN"}))
    showdown_node = TerminalNode(pot=1, invested={"BTN": 1, "BB": 1}, folded=frozenset())
    assert fold_node.is_showdown is False
    assert showdown_node.is_showdown is True


def test_terminal_is_showdown_with_multiple_folds_still_multiway():
    node = TerminalNode(
        pot=1, invested={"BTN": 1, "SB": 1, "BB": 1, "CO": 1}, folded=frozenset({"BTN"})
    )
    assert node.is_showdown is True  # 3 live players remain


# ---------------------------------------------------------------------------
# Tree structure (heads-up — confirms the general builder still produces
# the exact same behavior as the pre-generalization HU-specific one)
# ---------------------------------------------------------------------------


def test_root_player_to_act_is_btn():
    root = build_game_tree(GameConfig())
    assert root.player_to_act == BTN


def test_root_offers_fold_call_raise_and_allin():
    root = build_game_tree(GameConfig())
    kinds = {action.kind for action in root.legal_actions}
    assert kinds == {FOLD, CALL_OR_CHECK, RAISE, ALL_IN}


def test_root_fold_terminal_matches_blinds():
    root = build_game_tree(GameConfig(small_blind=0.5, big_blind=1.0))
    fold_terminal = root.children[Action(FOLD)]
    assert fold_terminal.folded == frozenset({"BTN"})
    assert fold_terminal.invested == {"BTN": 0.5, "BB": 1.0}
    assert fold_terminal.pot == 1.5


def test_btn_limp_passes_action_to_bb():
    root = build_game_tree(GameConfig())
    after_limp = root.children[Action(CALL_OR_CHECK)]
    assert isinstance(after_limp, DecisionNode)
    assert after_limp.player_to_act == BB
    assert after_limp.invested["BTN"] == after_limp.invested["BB"]


def test_bb_facing_limp_has_no_fold_option():
    root = build_game_tree(GameConfig())
    after_limp = root.children[Action(CALL_OR_CHECK)]
    kinds = {action.kind for action in after_limp.legal_actions}
    assert FOLD not in kinds


def test_bb_check_after_limp_is_terminal_showdown():
    root = build_game_tree(GameConfig())
    after_limp = root.children[Action(CALL_OR_CHECK)]
    checked = after_limp.children[Action(CALL_OR_CHECK)]
    assert isinstance(checked, TerminalNode)
    assert checked.is_showdown


def test_call_after_a_raise_is_terminal():
    root = build_game_tree(GameConfig())
    raise_action = next(a for a in root.legal_actions if a.kind == RAISE)
    after_raise = root.children[raise_action]
    called = after_raise.children[Action(CALL_OR_CHECK)]
    assert isinstance(called, TerminalNode)
    assert called.is_showdown


def test_raise_sizing_open_is_multiplier_times_big_blind():
    config = GameConfig(big_blind=1.0, raise_sizes=(2.5, 3.0, 2.2), max_raises=4)
    root = build_game_tree(config)
    raise_action = next(a for a in root.legal_actions if a.kind == RAISE)
    assert raise_action.size == pytest.approx(2.5)


def test_raise_sizing_3bet_is_multiplier_times_open():
    config = GameConfig(big_blind=1.0, raise_sizes=(2.5, 3.0, 2.2), max_raises=4)
    root = build_game_tree(config)
    open_action = next(a for a in root.legal_actions if a.kind == RAISE)
    after_open = root.children[open_action]
    threebet_action = next(a for a in after_open.legal_actions if a.kind == RAISE)
    assert threebet_action.size == pytest.approx(3.0 * open_action.size)


def test_fourth_raise_has_no_sized_tier_only_allin():
    config = GameConfig(big_blind=1.0, raise_sizes=(2.5, 3.0, 2.2), max_raises=4)
    node = build_game_tree(config)
    for _ in range(3):
        raise_action = next(a for a in node.legal_actions if a.kind == RAISE)
        node = node.children[raise_action]
    kinds = {action.kind for action in node.legal_actions}
    assert RAISE not in kinds
    assert ALL_IN in kinds


# ---------------------------------------------------------------------------
# Terminal-node counts, cross-checked against an independently-coded
# reference formula that works for any number of players (a fresh
# implementation of the same round-closing rules, not a call into
# game_tree.py), for a generic deep-stack config where only the raise
# cap ever limits action.
#
# Key structural fact that generalizes cleanly from HU to N players:
# once ANY player shoves all-in, every subsequent responder's remaining
# stack always exactly equals what they owe (current_bet == stack_bb
# from then on, unaffected by other players' choices), so nobody after
# a jam is ever offered a further raise — the whole "who still needs to
# respond to this jam" tail is a pure fold/call binary chain of length k
# (k = how many live players still need to act), giving exactly 2**k
# terminal leaves for that tail, regardless of N.
# ---------------------------------------------------------------------------


def _reference_terminal_count(num_players: int, max_raises: int) -> int:
    positions = tuple(range(num_players))
    sb_index, bb_index = positions[-2], positions[-1]
    invested0 = {p: 0.0 for p in positions}
    invested0[sb_index] = 0.5
    invested0[bb_index] = 1.0

    def post_jam_count(k: int) -> int:
        return 2**k

    def count(invested: dict, to_act: tuple, live: frozenset, raises_so_far: int) -> int:
        if len(live) == 1 or not to_act:
            return 1
        player, rest = to_act[0], to_act[1:]
        current_bet = max(invested[p] for p in live)
        to_call = current_bet - invested[player]

        total = 0
        if to_call > 0:
            total += count(invested, rest, live - {player}, raises_so_far)  # fold

        call_invested = dict(invested)
        call_invested[player] = current_bet
        total += count(call_invested, rest, live, raises_so_far)  # call/check

        next_raise_number = raises_so_far + 1
        if next_raise_number <= max_raises:
            reopened = tuple(p for p in range(num_players) if p != player and p in live)
            if next_raise_number < max_raises:
                raise_invested = dict(invested)
                raise_invested[player] = current_bet + 1.0  # any distinct higher value
                total += count(raise_invested, reopened, live, next_raise_number)
            total += post_jam_count(len(reopened))
        return total

    return count(invested0, positions, frozenset(positions), 0)


@pytest.mark.parametrize("max_raises", [1, 2, 3, 4])
def test_terminal_count_matches_reference_formula_heads_up(max_raises):
    raise_sizes = tuple([2.0] * (max_raises - 1))
    config = GameConfig(
        positions=(BTN, BB), stack_bb=1000.0, raise_sizes=raise_sizes, max_raises=max_raises
    )
    root = build_game_tree(config)
    assert count_terminal_nodes(root) == _reference_terminal_count(2, max_raises)


def test_reference_formula_matches_known_hu_values():
    # Hand-verified (by exhaustive enumeration) in the original HU test
    # suite — cross-checking the general formula reduces to these.
    assert [_reference_terminal_count(2, m) for m in (1, 2, 3, 4)] == [6, 14, 22, 30]


@pytest.mark.parametrize("max_raises", [1, 2, 3, 4])
def test_terminal_count_matches_reference_formula_three_max(max_raises):
    raise_sizes = tuple([2.0] * (max_raises - 1))
    config = GameConfig(
        positions=("BTN", "SB", "BB"), stack_bb=1000.0, raise_sizes=raise_sizes, max_raises=max_raises
    )
    root = build_game_tree(config)
    assert count_terminal_nodes(root) == _reference_terminal_count(3, max_raises)


def test_default_config_terminal_count():
    root = build_game_tree(GameConfig())
    assert count_terminal_nodes(root) == _reference_terminal_count(2, 4)


def test_three_max_default_raises_terminal_count():
    config = GameConfig(positions=("BTN", "SB", "BB"))
    root = build_game_tree(config)
    assert count_terminal_nodes(root) == _reference_terminal_count(3, 4)


# ---------------------------------------------------------------------------
# Backward-compatibility regression: the generalized builder must behave
# identically to the pre-generalization heads-up-only implementation.
# ---------------------------------------------------------------------------


def test_heads_up_backward_compatibility():
    config = GameConfig(positions=(BTN, BB))
    root = build_game_tree(config)
    assert root.player_to_act == BTN
    assert set(root.invested) == {BTN, BB}
    assert count_terminal_nodes(root) == 30  # unchanged from the original HU implementation


# ---------------------------------------------------------------------------
# Multiway (3-max) structural checks
# ---------------------------------------------------------------------------


def test_three_max_root_is_first_position():
    config = GameConfig(positions=("BTN", "SB", "BB"))
    root = build_game_tree(config)
    assert root.player_to_act == "BTN"
    assert root.invested == {"BTN": 0.0, "SB": 0.5, "BB": 1.0}


def test_three_max_btn_fold_passes_action_to_sb():
    # BTN folding doesn't end the hand — SB and BB still need to settle
    # up on the blinds (SB owes another 0.5 to match BB).
    config = GameConfig(positions=("BTN", "SB", "BB"))
    root = build_game_tree(config)
    after_btn_fold = root.children[Action(FOLD)]
    assert isinstance(after_btn_fold, DecisionNode)
    assert after_btn_fold.player_to_act == "SB"
    assert after_btn_fold.folded == frozenset({"BTN"})


def test_three_max_sb_still_has_fold_option_after_btn_limps():
    config = GameConfig(positions=("BTN", "SB", "BB"))
    root = build_game_tree(config)
    after_btn_limp = root.children[Action(CALL_OR_CHECK)]
    assert after_btn_limp.player_to_act == "SB"
    kinds = {action.kind for action in after_btn_limp.legal_actions}
    assert FOLD in kinds  # SB only posted half the big blind, still owes the rest


def test_three_max_bb_has_no_fold_option_after_everyone_limps():
    config = GameConfig(positions=("BTN", "SB", "BB"))
    root = build_game_tree(config)
    after_btn_limp = root.children[Action(CALL_OR_CHECK)]
    after_sb_limp = after_btn_limp.children[Action(CALL_OR_CHECK)]
    assert after_sb_limp.player_to_act == "BB"
    kinds = {action.kind for action in after_sb_limp.legal_actions}
    assert FOLD not in kinds


def test_three_max_all_limp_is_terminal_showdown():
    config = GameConfig(positions=("BTN", "SB", "BB"))
    root = build_game_tree(config)
    after_btn_limp = root.children[Action(CALL_OR_CHECK)]
    after_sb_limp = after_btn_limp.children[Action(CALL_OR_CHECK)]
    all_checked = after_sb_limp.children[Action(CALL_OR_CHECK)]
    assert isinstance(all_checked, TerminalNode)
    assert all_checked.is_showdown
    assert all_checked.invested == {"BTN": 1.0, "SB": 1.0, "BB": 1.0}


def test_three_max_raise_reopens_for_every_other_live_player():
    config = GameConfig(positions=("BTN", "SB", "BB"))
    root = build_game_tree(config)
    raise_action = next(a for a in root.legal_actions if a.kind == RAISE)
    after_btn_raise = root.children[raise_action]
    assert after_btn_raise.player_to_act == "SB"
    # SB folds; BB hasn't matched BTN's raise yet, so the round can't be
    # closed by SB's fold — BB must still get a decision.
    after_sb_fold = after_btn_raise.children[Action(FOLD)]
    assert isinstance(after_sb_fold, DecisionNode)
    assert after_sb_fold.player_to_act == "BB"
    assert after_sb_fold.folded == frozenset({"SB"})
    sb_call = after_btn_raise.children[Action(CALL_OR_CHECK)]
    assert isinstance(sb_call, DecisionNode)
    assert sb_call.player_to_act == "BB"


def test_three_max_fold_removes_player_from_future_reopening():
    # BTN raises; SB folds; BB 3-bets — the reopened queue for BB's
    # 3-bet must be [BTN] only, never SB (already folded).
    config = GameConfig(positions=("BTN", "SB", "BB"))
    root = build_game_tree(config)
    raise_action = next(a for a in root.legal_actions if a.kind == RAISE)
    after_btn_raise = root.children[raise_action]
    assert after_btn_raise.player_to_act == "SB"
    after_sb_fold = after_btn_raise.children[Action(FOLD)]
    assert after_sb_fold.player_to_act == "BB"
    threebet_action = next(a for a in after_sb_fold.legal_actions if a.kind == RAISE)
    after_bb_threebet = after_sb_fold.children[threebet_action]
    assert after_bb_threebet.player_to_act == "BTN"


def test_no_side_pots_multiway_all_actions_cap_at_stack():
    config = GameConfig(positions=("BTN", "SB", "BB"), stack_bb=5.0, raise_sizes=(2.5, 3.0, 2.2))
    root = build_game_tree(config)
    for node in walk(root):
        if isinstance(node, (DecisionNode, TerminalNode)):
            for amount in node.invested.values():
                assert amount <= config.stack_bb


# ---------------------------------------------------------------------------
# Termination / bounded depth / structural invariants
# ---------------------------------------------------------------------------


def test_tree_build_terminates_for_larger_raise_caps():
    config = GameConfig(stack_bb=1000.0, raise_sizes=tuple([2.0] * 7), max_raises=8)
    root = build_game_tree(config)
    assert tree_depth(root) <= 2 * config.max_raises + 1


def test_tree_build_terminates_multiway():
    config = GameConfig(positions=("BTN", "SB", "BB"), stack_bb=1000.0, raise_sizes=(2.0, 2.0, 2.0), max_raises=4)
    root = build_game_tree(config)
    assert tree_depth(root) > 0  # just needs to complete without recursing forever


def test_no_decision_node_exceeds_max_raises():
    config = GameConfig()
    root = build_game_tree(config)
    for node in walk(root):
        if isinstance(node, DecisionNode):
            assert node.raises_so_far <= config.max_raises


def test_allin_present_exactly_when_raise_room_and_stack_remain():
    config = GameConfig()
    root = build_game_tree(config)
    for node in walk(root):
        if not isinstance(node, DecisionNode):
            continue
        live = [p for p in node.invested if p not in node.folded]
        current_bet = max(node.invested[p] for p in live)
        to_call = current_bet - node.invested[node.player_to_act]
        remaining_stack = config.stack_bb - node.invested[node.player_to_act]
        should_have_allin = node.raises_so_far < config.max_raises and remaining_stack > to_call
        kinds = {action.kind for action in node.legal_actions}
        assert (ALL_IN in kinds) == should_have_allin


def test_no_raise_offered_once_cap_reached():
    config = GameConfig()
    root = build_game_tree(config)
    for node in walk(root):
        if isinstance(node, DecisionNode) and node.raises_so_far >= config.max_raises:
            kinds = {action.kind for action in node.legal_actions}
            assert RAISE not in kinds
            assert ALL_IN not in kinds


def test_no_action_commits_more_than_the_stack():
    config = GameConfig()
    root = build_game_tree(config)
    for node in walk(root):
        if isinstance(node, DecisionNode):
            for action in node.legal_actions:
                if action.size is not None:
                    assert action.size <= config.stack_bb


def test_short_stack_collapses_sized_raise_to_allin_only():
    config = GameConfig(stack_bb=5.0, raise_sizes=(2.5, 3.0, 2.2), max_raises=4)
    root = build_game_tree(config)
    open_action = next(a for a in root.legal_actions if a.kind == RAISE)
    after_open = root.children[open_action]
    kinds = {action.kind for action in after_open.legal_actions}
    assert RAISE not in kinds
    assert ALL_IN in kinds


# ---------------------------------------------------------------------------
# walk / count_terminal_nodes consistency
# ---------------------------------------------------------------------------


def test_walk_terminal_count_matches_count_terminal_nodes():
    root = build_game_tree(GameConfig())
    terminals_via_walk = sum(1 for node in walk(root) if isinstance(node, TerminalNode))
    assert terminals_via_walk == count_terminal_nodes(root)


def test_action_equality_and_hash():
    assert Action(FOLD) == Action(FOLD)
    assert Action(RAISE, 2.5) == Action(RAISE, 2.5)
    assert Action(RAISE, 2.5) != Action(RAISE, 3.0)
    assert len({Action(FOLD), Action(FOLD), Action(CALL_OR_CHECK)}) == 2


def test_action_str():
    assert str(Action(FOLD)) == "fold"
    assert str(Action(RAISE, 2.5)) == "raise:2.50"


# ---------------------------------------------------------------------------
# StreetConfig / build_street_tree — M11: a single postflop betting round.
# Reuses _build/_reopened_order/LazyChildren unchanged (see game_tree.py's
# module docstring), so these tests focus on what's actually different —
# no blinds posted, pot-relative (not big-blind-relative) opening sizing —
# not on re-proving round-closing logic test_game_tree.py already covers.
# ---------------------------------------------------------------------------


def test_street_config_is_valid():
    StreetConfig(positions=("OOP", "IP"), pot=10.0, stack_bb=97.0)  # should not raise


def test_street_config_rejects_wrong_raise_sizes_length():
    with pytest.raises(ValueError):
        StreetConfig(positions=("OOP", "IP"), pot=10.0, stack_bb=97.0, max_raises=4, raise_sizes=(2.5, 3.0))


def test_street_config_rejects_max_raises_below_one():
    with pytest.raises(ValueError):
        StreetConfig(positions=("OOP", "IP"), pot=10.0, stack_bb=97.0, max_raises=0, raise_sizes=())


def test_street_config_rejects_nonpositive_pot():
    with pytest.raises(ValueError):
        StreetConfig(positions=("OOP", "IP"), pot=0.0, stack_bb=97.0, max_raises=1, raise_sizes=())


def test_street_config_rejects_nonpositive_stack():
    with pytest.raises(ValueError):
        StreetConfig(positions=("OOP", "IP"), pot=10.0, stack_bb=0.0, max_raises=1, raise_sizes=())


def test_street_config_rejects_fewer_than_two_positions():
    with pytest.raises(ValueError):
        StreetConfig(positions=("OOP",), pot=10.0, stack_bb=97.0, max_raises=1, raise_sizes=())


def test_street_config_rejects_duplicate_positions():
    with pytest.raises(ValueError):
        StreetConfig(positions=("OOP", "OOP"), pot=10.0, stack_bb=97.0, max_raises=1, raise_sizes=())


def test_street_tree_root_is_first_position_with_nothing_invested():
    config = StreetConfig(positions=("OOP", "IP"), pot=10.0, stack_bb=97.0, max_raises=1, raise_sizes=())
    root = build_street_tree(config)
    assert root.player_to_act == "OOP"
    assert root.invested == {"OOP": 0.0, "IP": 0.0}
    assert root.pot == 10.0


def test_street_tree_root_has_no_fold_option():
    # Nobody has bet anything yet this street — checking is always free,
    # so folding isn't a legal action at the very first decision (mirrors
    # BB-facing-no-raise's existing preflop behavior, not new logic).
    config = StreetConfig(positions=("OOP", "IP"), pot=10.0, stack_bb=97.0, max_raises=1, raise_sizes=())
    root = build_street_tree(config)
    assert FOLD not in {action.kind for action in root.legal_actions}
    assert CALL_OR_CHECK in {action.kind for action in root.legal_actions}


def test_street_tree_open_raise_is_sized_off_the_pot_not_a_blind():
    config = StreetConfig(
        positions=("OOP", "IP"), pot=10.0, stack_bb=97.0, max_raises=2, raise_sizes=(0.75,)
    )
    root = build_street_tree(config)
    open_action = next(a for a in root.legal_actions if a.kind == RAISE)
    assert open_action.size == pytest.approx(0.75 * 10.0)  # 0.75x pot, not 0.75x a big blind


def test_street_tree_second_raise_is_sized_off_the_previous_bet():
    config = StreetConfig(
        positions=("OOP", "IP"), pot=10.0, stack_bb=97.0, max_raises=3, raise_sizes=(0.75, 2.0)
    )
    root = build_street_tree(config)
    open_action = next(a for a in root.legal_actions if a.kind == RAISE)
    open_size = open_action.size
    after_open = root.children[open_action]
    reraise_action = next(a for a in after_open.legal_actions if a.kind == RAISE)
    assert reraise_action.size == pytest.approx(2.0 * open_size)


def test_street_tree_facing_a_raise_has_a_fold_option():
    config = StreetConfig(positions=("OOP", "IP"), pot=10.0, stack_bb=97.0, max_raises=2, raise_sizes=(0.75,))
    root = build_street_tree(config)
    open_action = next(a for a in root.legal_actions if a.kind == RAISE)
    after_open = root.children[open_action]
    assert after_open.player_to_act == "IP"
    assert FOLD in {action.kind for action in after_open.legal_actions}


def test_street_tree_all_in_never_exceeds_the_streets_remaining_stack():
    config = StreetConfig(positions=("OOP", "IP"), pot=10.0, stack_bb=15.0, max_raises=4)
    root = build_street_tree(config)
    for node in walk(root):
        if isinstance(node, DecisionNode):
            for action in node.legal_actions:
                if action.size is not None:
                    assert action.size <= config.stack_bb


def test_street_tree_terminal_pot_includes_the_entering_pot():
    # Both players check through — 0 this-street invested from either —
    # the resulting showdown terminal's pot must still reflect the
    # entering pot (StreetConfig.pot_offset), not just this street's own
    # action, or every downstream equity*pot payoff would be wrong.
    config = StreetConfig(positions=("OOP", "IP"), pot=10.0, stack_bb=97.0, max_raises=1, raise_sizes=())
    root = build_street_tree(config)
    check_action = next(a for a in root.legal_actions if a.kind == CALL_OR_CHECK)
    after_oop_checks = root.children[check_action]
    ip_check = next(a for a in after_oop_checks.legal_actions if a.kind == CALL_OR_CHECK)
    showdown = after_oop_checks.children[ip_check]
    assert isinstance(showdown, TerminalNode)
    assert showdown.pot == pytest.approx(10.0)


def test_street_tree_payoff_sums_to_the_entering_pot_not_zero():
    # See TerminalNode.payoff's docstring: with a nonzero pot_offset
    # (postflop), payoffs sum to that offset, not 0 — the entering pot
    # is already at stake, not contributed by anyone this street. This
    # is expected, documented behavior, not a bug — pinned here so a
    # future change can't silently break the (different) preflop
    # zero-sum invariant without this test catching it.
    node = TerminalNode(pot=10.0, invested={"OOP": 0.0, "IP": 0.0}, folded=frozenset({"IP"}))
    payoffs = node.payoff({})
    assert sum(payoffs.values()) == pytest.approx(10.0)
    assert payoffs["OOP"] == pytest.approx(10.0)
    assert payoffs["IP"] == pytest.approx(0.0)


def test_game_tree_terminal_payoff_still_sums_to_zero():
    # The original preflop invariant, unaffected by pot_offset (which is
    # 0 for GameConfig) — a regression guard for the docstring's claim.
    node = TerminalNode(pot=1.5, invested={"BTN": 0.5, "BB": 1.0}, folded=frozenset({"BTN"}))
    assert sum(node.payoff({}).values()) == pytest.approx(0.0)


def test_street_tree_reopens_action_after_a_raise_multiway():
    config = StreetConfig(positions=("OOP", "MID", "IP"), pot=15.0, stack_bb=97.0, max_raises=2, raise_sizes=(0.75,))
    root = build_street_tree(config)
    check_action = next(a for a in root.legal_actions if a.kind == CALL_OR_CHECK)
    after_oop_checks = root.children[check_action]
    assert after_oop_checks.player_to_act == "MID"
    raise_action = next(a for a in after_oop_checks.legal_actions if a.kind == RAISE)
    after_mid_raises = after_oop_checks.children[raise_action]
    assert after_mid_raises.player_to_act == "IP"
    # OOP already acted (checked) but must get a chance to respond to
    # MID's raise — the same "reopen for every other live player" logic
    # build_game_tree already relies on, exercised here for a street tree.
    ip_call = next(a for a in after_mid_raises.legal_actions if a.kind == CALL_OR_CHECK)
    after_ip_calls = after_mid_raises.children[ip_call]
    assert after_ip_calls.player_to_act == "OOP"
    assert FOLD in {action.kind for action in after_ip_calls.legal_actions}
