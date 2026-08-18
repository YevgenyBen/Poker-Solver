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
    TerminalNode,
    build_game_tree,
    count_terminal_nodes,
    tree_depth,
    walk,
)


# ---------------------------------------------------------------------------
# GameConfig validation
# ---------------------------------------------------------------------------


def test_default_config_is_valid():
    GameConfig()  # should not raise


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


# ---------------------------------------------------------------------------
# TerminalNode.payoff
# ---------------------------------------------------------------------------


def test_payoff_btn_folds():
    node = TerminalNode(pot=1.5, btn_invested=0.5, bb_invested=1.0, folded_player=BTN)
    assert node.payoff(None, None) == -0.5


def test_payoff_bb_folds():
    node = TerminalNode(pot=1.5, btn_invested=0.5, bb_invested=1.0, folded_player=BB)
    # BTN wins the whole pot: net = pot - btn_invested = 1.0.
    assert node.payoff(None, None) == 1.0


def test_payoff_fold_is_zero_sum():
    node = TerminalNode(pot=5.0, btn_invested=2.0, bb_invested=3.0, folded_player=BTN)
    btn_net = node.payoff(None, None)
    # BB's net when BTN folds = pot - bb_invested, which must equal
    # -btn_net for the outcome to be zero-sum.
    bb_net = node.pot - node.bb_invested
    assert btn_net == -bb_net


def test_payoff_showdown_uses_payoff_fn():
    node = TerminalNode(pot=10.0, btn_invested=5.0, bb_invested=5.0, folded_player=None)
    # Stub: BTN gets 70% of the pot.
    payoff_fn = lambda btn_hand, bb_hand, pot: 0.7 * pot
    assert node.payoff("AA", "72o", payoff_fn) == pytest.approx(7.0 - 5.0)


def test_payoff_showdown_without_payoff_fn_raises():
    node = TerminalNode(pot=10.0, btn_invested=5.0, bb_invested=5.0, folded_player=None)
    with pytest.raises(ValueError):
        node.payoff("AA", "72o")


def test_terminal_is_showdown_property():
    fold_node = TerminalNode(pot=1, btn_invested=1, bb_invested=1, folded_player=BTN)
    showdown_node = TerminalNode(pot=1, btn_invested=1, bb_invested=1, folded_player=None)
    assert fold_node.is_showdown is False
    assert showdown_node.is_showdown is True


# ---------------------------------------------------------------------------
# Tree structure
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
    assert fold_terminal.folded_player == BTN
    assert fold_terminal.btn_invested == 0.5
    assert fold_terminal.bb_invested == 1.0
    assert fold_terminal.pot == 1.5


def test_btn_limp_passes_action_to_bb():
    root = build_game_tree(GameConfig())
    after_limp = root.children[Action(CALL_OR_CHECK)]
    assert isinstance(after_limp, DecisionNode)
    assert after_limp.player_to_act == BB
    assert after_limp.btn_invested == after_limp.bb_invested


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
    # Walk down the "always raise" line three times (raises 1, 2, 3).
    for _ in range(3):
        raise_action = next(a for a in node.legal_actions if a.kind == RAISE)
        node = node.children[raise_action]
    # Now raises_so_far == 3 == max_raises - 1: no sized raise offered.
    kinds = {action.kind for action in node.legal_actions}
    assert RAISE not in kinds
    assert ALL_IN in kinds


# ---------------------------------------------------------------------------
# Terminal-node counts (hand-verified for max_raises=1 by exhaustive
# enumeration; cross-checked against an independent recursive formula for
# max_raises 1-4).
#
# Key structural fact the formula has to respect: once a player shoves
# all-in, the responder's remaining stack exactly equals their call
# amount (both players share the same starting stack), so a node reached
# via an all-in never offers a further raise — it's always just
# fold/call (2 terminals), regardless of how many raises came before it.
# A node reached via a *sized* raise still has room behind, so it keeps
# the full fold/call/raise/all-in shape.
# ---------------------------------------------------------------------------


def test_terminal_count_max_raises_1():
    config = GameConfig(raise_sizes=(), max_raises=1)
    root = build_game_tree(config)
    assert count_terminal_nodes(root) == 6


def _reference_terminal_count(max_raises: int) -> int:
    """Independent recursive re-derivation of the terminal-node count,
    for a "generic" (deep-stack) config where only the raise cap ever
    limits action, never remaining stack.
    """
    POST_JAM = 2  # fold/call only, forever, once someone has shoved

    def generic(r: int) -> int:
        """A node reached via a sized raise, with `r` raises so far."""
        total = 2  # fold + call
        if r < max_raises - 1:
            total += generic(r + 1)  # sized raise child
        if r < max_raises:
            total += POST_JAM  # all-in child
        return total

    def bb_facing_limp() -> int:
        """BB's node after BTN limps: same shape as `generic` but with
        no fold option (nothing to call yet)."""
        total = 1  # call/check
        if 0 < max_raises - 1:
            total += generic(1)
        if 0 < max_raises:
            total += POST_JAM
        return total

    # Root: BTN's first action. fold + (limp -> bb_facing_limp) +
    # (sized raise -> generic(1), if a sized tier exists) + all-in.
    total = 1 + bb_facing_limp()
    if 0 < max_raises - 1:
        total += generic(1)
    if 0 < max_raises:
        total += POST_JAM
    return total


@pytest.mark.parametrize("max_raises", [1, 2, 3, 4])
def test_terminal_count_matches_reference_formula(max_raises):
    raise_sizes = tuple([2.0] * (max_raises - 1))
    config = GameConfig(stack_bb=1000.0, raise_sizes=raise_sizes, max_raises=max_raises)
    root = build_game_tree(config)
    assert count_terminal_nodes(root) == _reference_terminal_count(max_raises)


def test_default_config_terminal_count():
    # max_raises=4 by default; matches _reference_terminal_count(4).
    root = build_game_tree(GameConfig())
    assert count_terminal_nodes(root) == _reference_terminal_count(4)


# ---------------------------------------------------------------------------
# Termination / bounded depth / structural invariants
# ---------------------------------------------------------------------------


def test_tree_build_terminates_for_larger_raise_caps():
    config = GameConfig(stack_bb=1000.0, raise_sizes=tuple([2.0] * 7), max_raises=8)
    root = build_game_tree(config)
    assert tree_depth(root) <= 2 * config.max_raises + 1


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
        opponent_invested = node.bb_invested if node.player_to_act == BTN else node.btn_invested
        own_invested = node.btn_invested if node.player_to_act == BTN else node.bb_invested
        to_call = opponent_invested - own_invested
        remaining_stack = config.stack_bb - own_invested
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
    # BTN opens to 2.5; BB's 3-bet would be 3.0 * 2.5 = 7.5, which
    # exceeds a 5bb stack — BB should only see all_in as a raise option.
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
