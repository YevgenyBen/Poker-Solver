from itertools import combinations

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
    button_position,
    count_terminal_nodes,
    postflop_action_order,
    resolve_action,
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


def test_config_rejects_a_stack_shorter_than_the_big_blind():
    """M117. The bound used to be the SMALL blind, which let a stack
    between the two blinds through — and the big blind is posted
    unconditionally, so the tree started with invested["BB"] > stack_bb
    and every pot below it counted chips nobody had. Measured over whole
    trees the overstatement is exactly 2 * (big_blind - stack_bb): 96%
    of the real pot at 0.51bb, 67% at 0.6bb. /advise answered a 0.6bb
    request with a confident 200 and a full strategy.
    """
    for stack in (0.5, 0.51, 0.6, 0.9, 0.99):
        with pytest.raises(ValueError, match="big_blind"):
            GameConfig(stack_bb=stack, small_blind=0.5, big_blind=1.0,
                       raise_sizes=(), max_raises=1)
    # exactly one big blind is the boundary, and it is legal: the BB is
    # all-in for their whole stack, which is a real (if trivial) spot.
    config = GameConfig(stack_bb=1.0, small_blind=0.5, big_blind=1.0,
                        raise_sizes=(), max_raises=1)
    root = build_game_tree(config)
    assert all(v <= config.stack_bb for v in root.invested.values())
    # ...and the BB, all-in from the blind, is never asked to decide
    # anything. Raising the bound alone left this broken.
    assert all(node.player_to_act != BB for node in walk(root)
               if isinstance(node, DecisionNode))


def test_config_rejects_nonpositive_blinds():
    # M117: `raise_sizes=()` without `max_raises=1` is itself invalid, so
    # this used to pass on the raise_sizes guard and never reach the one
    # it names. Both blinds are checked now, and the match= pins which
    # guard actually fired.
    for kwargs in ({"small_blind": 0}, {"big_blind": 0}, {"small_blind": -1}):
        with pytest.raises(ValueError, match="positive"):
            GameConfig(raise_sizes=(), max_raises=1, **kwargs)


def test_config_rejects_fewer_than_two_positions():
    with pytest.raises(ValueError):
        GameConfig(positions=("BTN",), raise_sizes=())


def test_config_rejects_duplicate_positions():
    with pytest.raises(ValueError):
        GameConfig(positions=("BTN", "BTN", "BB"), raise_sizes=())


# ---------------------------------------------------------------------------
# button_position / postflop_action_order (M29)
#
# Real poker rule (Robert's Rules of Poker, "Button and Blind Use"):
# action begins with the first active player to the LEFT OF THE BUTTON on
# every betting round after the first — no table-size exception. What
# genuinely differs at heads-up is a seating fact, not a rule: the button
# IS the small blind there, so "the seat immediately before the small
# blind" (the general way to locate the button from a GameConfig.positions
# tuple, since the last two entries always post small/big blind) doesn't
# apply the same way at N=2 as it does at N>=3 — see button_position's
# own docstring. The tempting shortcut "the small blind acts first
# postflop" is only a derived consequence of the real rule at N>=3 (SB is
# the seat immediately left of the button there); at N=2 the shortcut
# inverts, since the button/SB is the same seat, and that seat acts LAST
# postflop, not first. Every test below is designed to actually catch
# that shortcut (and its mirror-image bugs), not just confirm the
# formula agrees with itself.
# ---------------------------------------------------------------------------

_HU_POSITIONS = ("BTN", "BB")
_THREE_MAX_POSITIONS = ("BTN", "SB", "BB")
_SIX_MAX_POSITIONS = ("UTG", "MP", "CO", "BTN", "SB", "BB")
_NINE_MAX_POSITIONS = ("UTG", "UTG1", "MP1", "MP2", "MP3", "CO", "BTN", "SB", "BB")
_ALL_REAL_CONFIGS = (_HU_POSITIONS, _THREE_MAX_POSITIONS, _SIX_MAX_POSITIONS, _NINE_MAX_POSITIONS)


def test_button_position_heads_up_is_the_special_case():
    # THE regression test that matters most here: heads-up is a genuine
    # exception (the button posts the small blind, so it's positions[0],
    # not positions[-3] — which doesn't even exist at N=2), not a
    # degenerate case any general formula happens to also cover.
    assert button_position(("BTN", "BB")) == "BTN"


@pytest.mark.parametrize(
    "positions",
    [_THREE_MAX_POSITIONS, _SIX_MAX_POSITIONS, _NINE_MAX_POSITIONS],
)
def test_button_position_at_three_plus_players_is_the_seat_before_small_blind(positions):
    assert button_position(positions) == "BTN"
    assert positions[positions.index(button_position(positions)) + 1] == "SB"


def test_button_position_rejects_fewer_than_two_positions():
    with pytest.raises(ValueError):
        button_position(("BTN",))


def test_postflop_action_order_heads_up_reverses_preflop_order():
    # The single most important case: applying the N>=3 rotation formula
    # here would give (BTN, BB) unchanged — the OPPOSITE of reality, and
    # would silently invert every live heads-up-origin flop query.
    assert postflop_action_order(("BTN", "BB")) == ("BB", "BTN")


@pytest.mark.parametrize(
    "positions,expected",
    [
        (_THREE_MAX_POSITIONS, ("SB", "BB", "BTN")),
        (_SIX_MAX_POSITIONS, ("SB", "BB", "UTG", "MP", "CO", "BTN")),
        (
            _NINE_MAX_POSITIONS,
            ("SB", "BB", "UTG", "UTG1", "MP1", "MP2", "MP3", "CO", "BTN"),
        ),
    ],
)
def test_postflop_action_order_full_table_rotates_blinds_to_the_front(positions, expected):
    assert postflop_action_order(positions) == expected


def test_postflop_action_order_button_always_acts_last_when_live():
    # The one property that's genuinely universal across every N,
    # including heads-up — unlike "blinds act first," which isn't.
    for positions in _ALL_REAL_CONFIGS:
        for live in combinations(positions, 2):
            if button_position(positions) in live:
                assert postflop_action_order(positions, live)[-1] == button_position(positions)


def test_postflop_action_order_blind_vs_non_blind_survivors_match_the_modal_spot():
    # The most common real spot (someone opens, a blind defends) — high
    # blast radius, but NOT discriminating on its own: a buggy
    # "treat any 2 survivors as heads-up" implementation gives the same
    # answer here, since it happens to coincide with the real one.
    assert postflop_action_order(_SIX_MAX_POSITIONS, ("BTN", "BB")) == ("BB", "BTN")
    assert postflop_action_order(_SIX_MAX_POSITIONS, ("UTG", "BB")) == ("BB", "UTG")
    assert postflop_action_order(_SIX_MAX_POSITIONS, ("CO", "SB")) == ("SB", "CO")


def test_postflop_action_order_blind_vs_blind_survivors_catches_the_headsup_shortcut():
    # Folds to SB, SB raises, BB calls: neither survivor is the button,
    # so there's no button/SB collapse and therefore no inversion — the
    # "treat 2 survivors as heads-up" bug gives IP=SB here; correct is
    # OOP=SB (still left of the button), IP=BB.
    assert postflop_action_order(_SIX_MAX_POSITIONS, ("SB", "BB")) == ("SB", "BB")
    assert postflop_action_order(_THREE_MAX_POSITIONS, ("SB", "BB")) == ("SB", "BB")


def test_postflop_action_order_non_blind_vs_non_blind_survivors_preserves_preflop_order():
    # UTG opens, MP calls, everyone else folds: postflop order matches
    # preflop's own relative order here (neither seat is a blind) — a
    # "reverse the survivors" bug gives IP=UTG; correct is IP=MP.
    assert postflop_action_order(_SIX_MAX_POSITIONS, ("UTG", "MP")) == ("UTG", "MP")
    assert postflop_action_order(_SIX_MAX_POSITIONS, ("CO", "BTN")) == ("CO", "BTN")


def _reference_postflop_order(positions: tuple, live_positions: tuple) -> tuple:
    """Independent reference implementation, deliberately written a
    different way (index-stepping through the ring one seat at a time,
    not slicing) than postflop_action_order itself — so agreement
    between the two is real cross-validation, not self-consistency.
    Mirrors M19's own brute-force-vs-naive-walk validation technique."""
    n = len(positions)
    button = positions[0] if n == 2 else positions[-3]
    button_idx = positions.index(button)
    live = set(live_positions)
    order = []
    for step in range(1, n + 1):
        seat = positions[(button_idx + step) % n]
        if seat in live:
            order.append(seat)
    return tuple(order)


def test_postflop_action_order_matches_an_independent_reference_for_every_real_survivor_pair():
    # Exhaustive: every 2-survivor subset of every real config this
    # project ships (55 cases total), each checked against a
    # separately-implemented reference, not just internal consistency.
    checked = 0
    for positions in _ALL_REAL_CONFIGS:
        for live in combinations(positions, 2):
            assert postflop_action_order(positions, live) == _reference_postflop_order(positions, live)
            checked += 1
    assert checked == 55


def test_multiway_table_configs_end_in_small_blind_then_big_blind():
    # postflop_action_order's formula is index-based (button_position ==
    # positions[-3] for N>=3), which depends on this project's own
    # multiway position tuples actually ending in (..., "SB", "BB") —
    # true today by convention, not enforced by GameConfig itself
    # (positions is documented as arbitrary unique labels), so this
    # guards against a future config silently breaking that assumption.
    for positions in (_THREE_MAX_POSITIONS, _SIX_MAX_POSITIONS, _NINE_MAX_POSITIONS):
        assert positions[-2:] == ("SB", "BB")
        assert "BTN" in positions[:-2]


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


# ---------------------------------------------------------------------------
# M24: resolve_action — the first shared "kind string -> real Action"
# helper, for a live caller (an action-path endpoint) that only has a
# bare kind, not a pre-known exact size.
# ---------------------------------------------------------------------------


def test_resolve_action_matches_an_existing_kind():
    root = build_game_tree(GameConfig())
    assert resolve_action(root, RAISE) == Action(RAISE, 2.5)
    assert resolve_action(root, CALL_OR_CHECK) == Action(CALL_OR_CHECK)
    assert resolve_action(root, ALL_IN).kind == ALL_IN


def test_resolve_action_raises_for_an_unknown_kind_string():
    root = build_game_tree(GameConfig())
    with pytest.raises(ValueError):
        resolve_action(root, "not_a_real_kind")


def test_resolve_action_raises_for_a_kind_not_legal_at_this_node():
    # BB facing a limp has no fold option (test_bb_facing_limp_has_no_
    # fold_option above) — a real, not contrived, illegal-here case.
    root = build_game_tree(GameConfig())
    after_limp = root.children[Action(CALL_OR_CHECK)]
    with pytest.raises(ValueError):
        resolve_action(after_limp, FOLD)


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


def test_no_raise_is_offered_once_anyone_is_all_in():
    """M117. The property that makes `_reopened_order`'s all-in
    exclusion unreachable, pinned so the reasoning in its docstring
    stays true.

    Under equal stacks, an all-in sets `current_bet` to `stack_bb`, so
    every remaining player's `to_call` equals their `remaining_stack`
    exactly — and `_build` offers a raise only when `remaining_stack >
    to_call`, strictly. So no raise can follow an all-in anywhere in the
    tree. If equal stacks ever stop holding, this test is the one that
    should fail first.
    """
    for n in (2, 3, 4):
        for depth in (5.0, 25.0, 100.0):
            config = GameConfig(positions=tuple("P%d" % i for i in range(n)),
                                stack_bb=depth, raise_sizes=(2.5, 3.0, 2.2), max_raises=4)
            for node in walk(build_game_tree(config)):
                if isinstance(node, TerminalNode):
                    continue
                someone_all_in = any(v >= config.stack_bb - 1e-9
                                     for p, v in node.invested.items()
                                     if p not in node.folded)
                if someone_all_in:
                    kinds = {action.kind for action in node.children.keys()}
                    assert kinds <= {FOLD, CALL_OR_CHECK}, (
                        f"a raise is offered after an all-in at {node.invested}: {kinds}"
                    )


@pytest.mark.parametrize("config,builder", [
    (GameConfig(positions=("P0", "P1"), stack_bb=100.0,
                raise_sizes=(2.5, 3.0, 2.2), max_raises=4), build_game_tree),
    (GameConfig(positions=("P0", "P1", "P2"), stack_bb=100.0,
                raise_sizes=(2.5, 3.0, 2.2), max_raises=4), build_game_tree),
    (GameConfig(positions=("BTN", "SB", "BB"), stack_bb=8.0,
                raise_sizes=(2.5, 3.0), max_raises=3), build_game_tree),
    # M117's boundary: at exactly one big blind the BB is all-in from
    # posting, so it must never appear as a player_to_act. Raising the
    # bound alone did not achieve that — `_build`'s opening `to_act` had
    # to stop including players who are already all-in.
    (GameConfig(positions=("BTN", "BB"), stack_bb=1.0,
                raise_sizes=(2.5, 3.0, 2.2), max_raises=4), build_game_tree),
    (GameConfig(positions=("BTN", "SB", "BB"), stack_bb=1.0,
                raise_sizes=(2.5, 3.0, 2.2), max_raises=4), build_game_tree),
    (GameConfig(positions=("P0", "P1", "P2", "P3"), stack_bb=100.0,
                raise_sizes=(2.5, 3.0), max_raises=3), build_game_tree),
    (StreetConfig(positions=("P0", "P1"), pot=6.0, stack_bb=97.0,
                  raise_sizes=(0.75, 2.5, 2.2), max_raises=4), build_street_tree),
    (StreetConfig(positions=("P0", "P1", "P2"), pot=6.0, stack_bb=97.0,
                  raise_sizes=(0.75, 2.5), max_raises=3), build_street_tree),
    (StreetConfig(positions=("SB", "BB"), pot=10.0, stack_bb=2.0,
                  raise_sizes=(0.75, 2.5), max_raises=3), build_street_tree),
    # M203: bet-size MENUS get the same exhaustive treatment. The
    # "raise label != money" check is what would catch a late-binding
    # closure handing every branch the last size's subtree - a tree that
    # is wholly wrong while every node still looks legal.
    (StreetConfig(positions=("OOP", "IP"), pot=6.0, stack_bb=97.0,
                  raise_sizes=((0.33, 0.75, 2.5), (2.0, 3.0)), max_raises=3),
     build_street_tree),
    (StreetConfig(positions=("P0", "P1", "P2"), pot=6.0, stack_bb=40.0,
                  raise_sizes=((0.5, 1.0), 2.5), max_raises=3),
     build_street_tree),
    # A menu at a SHORT stack, where some sizes are filtered out for
    # reaching the stack and the rest must still be coherent.
    (StreetConfig(positions=("OOP", "IP"), pot=10.0, stack_bb=12.0,
                  raise_sizes=((0.25, 0.75, 2.5),), max_raises=2),
     build_street_tree),
])
def test_every_tree_obeys_the_rules_of_poker(config, builder):
    """M117 (audit round 10). Eight legality invariants checked over
    every node of whole trees — the layer nine earlier audit rounds
    verified other things while assuming.

    The load-bearing one is NO SIDE POTS: at every showdown terminal
    each live player has committed exactly the same amount. M23 proved
    that from construction and built `query_strategy_from_path` on it;
    nothing had ever checked it. It holds over 26,354 nodes and 11,784
    showdowns across 38 configs, of which this parametrization is a
    representative, fast subset.

    Three of four injected mutations were caught by these checks (a
    short call, a re-acting raiser, a dropped entering pot). The fourth
    — deleting `_reopened_order`'s all-in exclusion — was not, because
    that clause is unreachable; see
    `test_no_raise_is_offered_once_anyone_is_all_in`.
    """
    eps = 1e-9
    stack, offset = config.stack_bb, config.pot_offset

    def check(node, acted):
        assert abs(node.pot - (offset + sum(node.invested.values()))) < eps, "pot not conserved"
        assert all(-eps <= v <= stack + eps for v in node.invested.values()), "invested out of range"
        live = [p for p in config.positions if p not in node.folded]
        assert live, "every terminal has a live player"
        if isinstance(node, TerminalNode):
            if len(live) >= 2:
                committed = {round(node.invested[p], 9) for p in live}
                assert len(committed) == 1, f"side pot at showdown: {node.invested}"
            return
        actor = node.player_to_act
        assert actor not in node.folded, "a folded player was asked to act"
        assert node.invested[actor] < stack - eps, "an all-in player was asked to act"
        current_bet = max(node.invested[p] for p in live)
        to_call = current_bet - node.invested[actor]
        actions = list(node.children.keys())
        assert (to_call > eps) == any(a.kind == FOLD for a in actions), "fold offered iff facing a bet"
        assert any(a.kind == CALL_OR_CHECK for a in actions), "call/check is always available"
        assert not (actor in acted and to_call <= eps), "the betting round should have closed"
        for action in actions:
            child = node.children[action]
            for p in config.positions:
                delta = child.invested[p] - node.invested[p]
                assert delta > -eps, "money came back out of the pot"
                assert p == actor or abs(delta) < eps, "a non-actor's money moved"
            if action.kind == CALL_OR_CHECK:
                assert abs(child.invested[actor] - current_bet) < eps, "call did not match the bet"
            elif action.kind == RAISE:
                assert abs(child.invested[actor] - action.size) < eps, "raise label != money"
                assert child.invested[actor] > current_bet + eps, "raise did not raise"
                assert child.invested[actor] < stack - eps, "a raise that is secretly an all-in"
            elif action.kind == ALL_IN:
                assert abs(child.invested[actor] - stack) < eps, "all-in is not the whole stack"
                assert child.invested[actor] > current_bet + eps, "all-in did not raise"
            assert node.folded <= child.folded, "a folded player un-folded"
            reopens = action.kind in (RAISE, ALL_IN)
            check(child, frozenset([actor]) if reopens else frozenset(acted) | {actor})

    check(builder(config), frozenset())


# --- M203: a raise level can offer a MENU of sizes ---------------------
#
# `raise_sizes` held one multiplier per raise level, so the tree could
# offer exactly one sized bet and an all-in. At production settings the
# smallest flop bet was 2.5x the pot: the engine could not bet half the
# pot on any street, ever, while solved play is dominated by bets of
# 0.25-0.75x. An entry may now be a tuple, and the solver picks the size.

def _menu_tree(sizes, pot=10.0, stack=100.0, max_raises=2):
    return build_street_tree(StreetConfig(
        positions=("OOP", "IP"), pot=pot, stack_bb=stack,
        raise_sizes=(sizes,), max_raises=max_raises))


def test_a_menu_offers_one_raise_action_per_size():
    root = _menu_tree((0.5, 1.0, 2.5))
    raises = sorted(a.size for a in root.legal_actions if a.kind == RAISE)
    assert raises == [5.0, 10.0, 25.0], raises


def test_a_single_multiplier_still_behaves_exactly_as_before():
    """Backward compatibility, asserted rather than assumed: every
    pre-M203 config passes a bare float and must be untouched."""
    menu = _menu_tree((2.5,))
    single = _menu_tree(2.5)
    assert ([str(a) for a in single.legal_actions]
            == [str(a) for a in menu.legal_actions])


def test_each_size_leads_to_its_OWN_subtree():
    """The late-binding trap, pinned directly.

    Building the branches in a loop makes the closure capture `size` by
    reference; without per-iteration binding every raise action would
    lead to the LAST size's subtree. Every node would still be legal —
    pots conserved, no side pots — while the tree modelled a game nobody
    plays. Here: after betting 5, the money in front of the bettor must
    be 5, not 25.
    """
    root = _menu_tree((0.5, 1.0, 2.5))
    for action in root.legal_actions:
        if action.kind != RAISE:
            continue
        child = root.children[action]
        assert abs(child.invested["OOP"] - action.size) < 1e-9, (
            f"{action} led to a subtree where OOP had committed "
            f"{child.invested['OOP']}")


def test_duplicate_sizes_are_collapsed_by_the_SIZE_HELPER_not_by_luck():
    """Asserted on `_raise_total_sizes`, not on the tree.

    Mutation testing caught the first version of this test: it built a
    tree from (1.0, 1.0, 2.5) and checked the action list, which passes
    with the dedupe REMOVED because two equal sizes make the identical
    `Action(RAISE, 10.0)` dict key and collapse anyway. The tree-level
    assertion could not fail. Testing the helper makes the guard real.
    """
    from poker_solver.game_tree import _raise_total_sizes

    assert _raise_total_sizes(1, 10.0, 0.0, ((1.0, 1.0, 2.5),)) == (10.0, 25.0)
    # Order is preserved — the first occurrence wins, not the last.
    assert _raise_total_sizes(1, 10.0, 0.0, ((2.5, 1.0, 2.5),)) == (25.0, 10.0)


def test_a_size_at_or_beyond_the_stack_is_dropped_not_disguised_as_a_raise():
    """A 2.5x-pot bet into a 12bb stack is a shove. It must appear as
    the all-in action, not as a `raise` labelled with the whole stack —
    which would also collide with the all-in's own key."""
    root = _menu_tree((0.25, 0.75, 2.5), pot=10.0, stack=12.0)
    raises = [a for a in root.legal_actions if a.kind == RAISE]
    assert all(a.size < 12.0 for a in raises), raises
    assert sum(1 for a in root.legal_actions if a.kind == ALL_IN) == 1

    # The BOUNDARY, which the case above never reaches: a size landing
    # EXACTLY on the stack. Mutation testing caught this - relaxing the
    # filter from >= to > left the first version passing, because 2.5x
    # of a 10 pot is 25 against a 12bb stack and is filtered either way.
    exact = _menu_tree((0.5, 2.5), pot=10.0, stack=25.0)
    assert [str(a) for a in exact.legal_actions] == [
        "call_or_check", "raise:5.00", "all_in:25.00"], (
        "a bet equal to the stack must be the all-in action, not a raise "
        "labelled with the whole stack")


@pytest.mark.parametrize("bad", [(), (0.0, 1.0), (-0.5,), ("half",)])
def test_a_malformed_menu_is_rejected_at_construction(bad):
    """A malformed menu does not fail on its own: an empty tuple removes
    every sized raise at that level and a non-positive multiplier builds
    a bet nobody made, both producing a tree that passes every legality
    invariant while modelling the wrong game."""
    with pytest.raises(ValueError):
        StreetConfig(positions=("OOP", "IP"), pot=10.0, stack_bb=100.0,
                     raise_sizes=(bad,), max_raises=2)


def test_the_menu_reaches_preflop_too():
    """`GameConfig` and `StreetConfig` share the builder, so the feature
    is not postflop-only — preflop sizes are multiples of the big blind
    rather than the pot, and nothing else changes."""
    root = build_game_tree(GameConfig(
        positions=("BTN", "BB"), stack_bb=100.0,
        raise_sizes=((2.0, 3.0), 2.2), max_raises=3))
    raises = sorted(a.size for a in root.legal_actions if a.kind == RAISE)
    assert raises == [2.0, 3.0], raises


def test_a_bare_kind_is_an_ERROR_when_a_menu_makes_it_ambiguous():
    """M205. `resolve_action`'s docstring claimed at most one sized RAISE
    could exist at a node, and M203's menu broke that.

    The old loop returned the first match: on a (0.33, 0.75, 2.5) menu it
    silently returned `raise:3.30`, so an action path saying "villain
    raised" would be modelled as facing the SMALLEST bet with nothing
    reporting the choice. Ambiguity must fail loudly instead — which is
    also what stops a menu being enabled on a street whose action paths
    are walked without the API being extended first.
    """
    root = build_street_tree(StreetConfig(
        positions=("OOP", "IP"), pot=10.0, stack_bb=100.0,
        raise_sizes=((0.33, 0.75, 2.5), 3.0, 2.2), max_raises=4))
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_action(root, RAISE)
    # Unambiguous kinds at the same node still resolve.
    assert resolve_action(root, CALL_OR_CHECK).kind == CALL_OR_CHECK
    assert resolve_action(root, ALL_IN).kind == ALL_IN


def test_a_bare_kind_still_resolves_for_every_shipped_configuration():
    """The guard must be inert where no menu is configured — which is
    every street today. A single sized raise is unambiguous."""
    root = build_street_tree(StreetConfig(
        positions=("OOP", "IP"), pot=10.0, stack_bb=100.0,
        raise_sizes=(2.5, 3.0, 2.2), max_raises=4))
    assert str(resolve_action(root, RAISE)) == "raise:25.00"
