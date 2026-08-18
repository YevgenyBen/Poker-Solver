"""Heads-up preflop betting-action tree.

This module models only the sequence of preflop actions (fold / call or
check / raise / all-in) between the two heads-up positions, BTN (button
and small blind, acts first) and BB (big blind, acts second and closes
the action when there's been no raise).

It deliberately does not branch on hole cards or hand classes anywhere —
the same tree is reused for every (BTN hand, BB hand) pair during CFR
solving (added in a later milestone). Terminal nodes instead expose a
generic `payoff_fn` seam: whoever solves the tree supplies a function
mapping (btn_hand, bb_hand, pot) to BTN's raw share of the pot, so this
module has no dependency on cards/equity concepts at all. Today that
payoff_fn is backed by the preflop equity table; if postflop streets are
added later, it could be backed by a full postflop subgame value instead,
without changing anything here.

This is a preflop-only model: any action sequence that isn't a fold (a
limp-and-check, a raise that gets called, a jam that gets called) ends
immediately at a "showdown" terminal valued via the injected payoff
function, standing in for "then the rest of the hand gets played out."
"""

from dataclasses import dataclass
from typing import Callable, Optional

BTN = "BTN"
BB = "BB"

FOLD = "fold"
CALL_OR_CHECK = "call_or_check"
RAISE = "raise"
ALL_IN = "all_in"


@dataclass(frozen=True)
class GameConfig:
    """Parameters defining one heads-up preflop game to build a tree for.

    Both players are assumed to start with the same effective stack
    (`stack_bb`) — standard for heads-up preflop solving.

    `raise_sizes` has exactly `max_raises - 1` entries: the first raise
    (the "open") is sized as `raise_sizes[0] * big_blind`; every raise
    after that is sized as `raise_sizes[i] * <the previous bet's total
    size>`. The max_raises-th raise has no sized tier at all — it's
    always forced to be an all-in shove. This mirrors real heads-up play,
    where a 5th preflop raise is essentially always a jam.
    """

    stack_bb: float = 100.0
    small_blind: float = 0.5
    big_blind: float = 1.0
    raise_sizes: tuple = (2.5, 3.0, 2.2)
    max_raises: int = 4

    def __post_init__(self):
        if self.max_raises < 1:
            raise ValueError("max_raises must be at least 1")
        if len(self.raise_sizes) != self.max_raises - 1:
            raise ValueError(
                "raise_sizes must have exactly max_raises - 1 "
                f"({self.max_raises - 1}) entries, got {len(self.raise_sizes)}"
            )
        if self.stack_bb <= self.small_blind:
            raise ValueError("stack_bb must be greater than small_blind")
        if self.small_blind <= 0 or self.big_blind <= 0:
            raise ValueError("small_blind and big_blind must be positive")


@dataclass(frozen=True)
class Action:
    """One legal action at a DecisionNode.

    `size` is the *total* commitment size for a raise/all-in (not the
    incremental amount added), and is None for fold/call_or_check.
    """

    kind: str
    size: Optional[float] = None

    def __str__(self) -> str:
        if self.size is not None:
            return f"{self.kind}:{self.size:.2f}"
        return self.kind


@dataclass(frozen=True)
class TerminalNode:
    """A leaf of the betting tree: either a fold or a (preflop) showdown."""

    pot: float
    btn_invested: float
    bb_invested: float
    folded_player: Optional[str]  # BTN, BB, or None for a showdown

    @property
    def is_showdown(self) -> bool:
        return self.folded_player is None

    def payoff(self, btn_hand, bb_hand, payoff_fn: Optional[Callable] = None) -> float:
        """BTN's net payoff at this terminal; BB's is always its negation.

        `payoff_fn(btn_hand, bb_hand, pot)` must return BTN's *raw* share
        of the pot (e.g. `equity(btn_hand, bb_hand) * pot`) — this method
        subtracts BTN's investment for you. It's only called (and only
        needs to be supplied) for showdown terminals; fold terminals are
        resolved from the pot/investment bookkeeping alone.
        """
        if self.folded_player == BTN:
            return -self.btn_invested
        if self.folded_player == BB:
            return self.bb_invested
        if payoff_fn is None:
            raise ValueError("payoff_fn is required to score a showdown terminal")
        return payoff_fn(btn_hand, bb_hand, self.pot) - self.btn_invested


@dataclass(frozen=True)
class DecisionNode:
    """A node where `player_to_act` chooses among `children`."""

    player_to_act: str
    pot: float
    btn_invested: float
    bb_invested: float
    raises_so_far: int
    children: dict  # Action -> DecisionNode | TerminalNode

    @property
    def legal_actions(self) -> list:
        return list(self.children.keys())


Node = "DecisionNode | TerminalNode"


def _raise_total_size(raise_number: int, big_blind: float, previous_bet: float, raise_sizes: tuple) -> float:
    """Total committed size for raise number `raise_number` (1-indexed)."""
    multiplier = raise_sizes[raise_number - 1]
    if raise_number == 1:
        return multiplier * big_blind
    return multiplier * previous_bet


def _build_decision_node(
    config: GameConfig,
    player_to_act: str,
    invested: dict,
    raises_so_far: int,
    previous_bet: float,
) -> DecisionNode:
    opponent = BB if player_to_act == BTN else BTN
    to_call = invested[opponent] - invested[player_to_act]
    pot = invested[BTN] + invested[BB]
    remaining_stack = config.stack_bb - invested[player_to_act]

    children = {}

    if to_call > 0:
        children[Action(FOLD)] = TerminalNode(
            pot=pot,
            btn_invested=invested[BTN],
            bb_invested=invested[BB],
            folded_player=player_to_act,
        )

    call_invested = dict(invested)
    call_invested[player_to_act] = invested[opponent]
    call_pot = call_invested[BTN] + call_invested[BB]

    # BTN's very first action is the one case where a call (a limp)
    # doesn't close the betting round: BB still gets the "option" to
    # check or raise, since BB hasn't acted yet. Every other call closes
    # the action immediately.
    is_opening_limp = raises_so_far == 0 and player_to_act == BTN
    if is_opening_limp:
        children[Action(CALL_OR_CHECK)] = _build_decision_node(
            config, opponent, call_invested, raises_so_far, previous_bet
        )
    else:
        children[Action(CALL_OR_CHECK)] = TerminalNode(
            pot=call_pot,
            btn_invested=call_invested[BTN],
            bb_invested=call_invested[BB],
            folded_player=None,
        )

    next_raise_number = raises_so_far + 1
    if next_raise_number <= config.max_raises and remaining_stack > to_call:
        if next_raise_number < config.max_raises:
            size = _raise_total_size(next_raise_number, config.big_blind, previous_bet, config.raise_sizes)
            if size < config.stack_bb:
                raise_invested = dict(invested)
                raise_invested[player_to_act] = size
                children[Action(RAISE, size)] = _build_decision_node(
                    config, opponent, raise_invested, next_raise_number, size
                )
        jam_invested = dict(invested)
        jam_invested[player_to_act] = config.stack_bb
        children[Action(ALL_IN, config.stack_bb)] = _build_decision_node(
            config, opponent, jam_invested, next_raise_number, config.stack_bb
        )

    return DecisionNode(
        player_to_act=player_to_act,
        pot=pot,
        btn_invested=invested[BTN],
        bb_invested=invested[BB],
        raises_so_far=raises_so_far,
        children=children,
    )


def build_game_tree(config: GameConfig) -> DecisionNode:
    """Build the full heads-up preflop betting tree for `config`."""
    return _build_decision_node(
        config,
        player_to_act=BTN,
        invested={BTN: config.small_blind, BB: config.big_blind},
        raises_so_far=0,
        previous_bet=config.big_blind,
    )


def walk(node):
    """Yield every node in the tree (DecisionNode and TerminalNode), DFS."""
    yield node
    if isinstance(node, DecisionNode):
        for child in node.children.values():
            yield from walk(child)


def count_terminal_nodes(node) -> int:
    if isinstance(node, TerminalNode):
        return 1
    return sum(count_terminal_nodes(child) for child in node.children.values())


def tree_depth(node) -> int:
    """Number of decision points on the longest path from `node` down."""
    if isinstance(node, TerminalNode) or not node.children:
        return 0
    return 1 + max(tree_depth(child) for child in node.children.values())
