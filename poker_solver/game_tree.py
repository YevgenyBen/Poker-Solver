"""N-player preflop betting-action tree.

This module models the sequence of preflop actions (fold / call or check
/ raise / all-in) around a table of N players, in a fixed table order
given by `GameConfig.positions`. Heads-up (2 players, e.g.
`positions=("BTN", "BB")`) is just the N=2 special case — this module
makes no HU-specific assumptions.

It deliberately does not branch on hole cards or hand classes anywhere —
the same tree is reused for every combination of hands across all N
players during CFR solving. Terminal nodes instead expose a generic
`payoff_fn` seam: whoever solves the tree supplies a function mapping
(a list of the live players' hands, pot) to their raw shares of the pot,
so this module has no dependency on cards/equity concepts at all. Today
that payoff_fn is backed by the preflop equity table; if postflop
streets are added later, it could be backed by a full postflop subgame
value instead, without changing anything here.

This is a preflop-only model: any action sequence that isn't a fold (a
limp-and-check, a raise that gets called, a jam that gets called) ends
immediately at a "showdown" terminal valued via the injected payoff
function, standing in for "then the rest of the hand gets played out."

No side pots, at any N: `GameConfig` requires one shared `stack_bb` for
every player, and the raise-sizing logic below guarantees no one is ever
asked to commit more than that — so calling an all-in always brings the
caller's own total investment up to exactly the same ceiling.
"""

from dataclasses import dataclass
from typing import Callable, Optional

# Defaults / conventional heads-up position labels — GameConfig.positions
# accepts any table of unique strings, this isn't a hardcoded constraint.
BTN = "BTN"
BB = "BB"

FOLD = "fold"
CALL_OR_CHECK = "call_or_check"
RAISE = "raise"
ALL_IN = "all_in"


@dataclass(frozen=True)
class GameConfig:
    """Parameters defining one N-player preflop game to build a tree for.

    All players are assumed to start with the same effective stack
    (`stack_bb`) — standard for preflop solving at any table size.

    `positions` is the acting order for the *first* betting round,
    starting from the first player to act. The *last two* entries always
    post the small and big blind respectively (and so act last) — e.g.
    `("BTN", "SB", "BB")` for a 3-max table (BTN opens with nothing
    posted), or the heads-up default `("BTN", "BB")` (BTN posts the
    small blind and also opens first, same as today).

    `raise_sizes` has exactly `max_raises - 1` entries: the first raise
    (the "open") is sized as `raise_sizes[0] * big_blind`; every raise
    after that is sized as `raise_sizes[i] * <the previous bet's total
    size>`. The max_raises-th raise has no sized tier at all — it's
    always forced to be an all-in shove.
    """

    positions: tuple = (BTN, BB)
    stack_bb: float = 100.0
    small_blind: float = 0.5
    big_blind: float = 1.0
    raise_sizes: tuple = (2.5, 3.0, 2.2)
    max_raises: int = 4

    def __post_init__(self):
        if len(self.positions) < 2:
            raise ValueError("positions must have at least 2 entries")
        if len(set(self.positions)) != len(self.positions):
            raise ValueError(f"positions must be unique, got {self.positions!r}")
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

    @property
    def num_players(self) -> int:
        return len(self.positions)


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
    """A leaf of the betting tree: either a fold-out or a (preflop) showdown."""

    pot: float
    invested: dict  # position -> total chips committed
    folded: frozenset  # positions that folded

    @property
    def is_showdown(self) -> bool:
        return len(self.invested) - len(self.folded) > 1

    def payoff(self, hands: dict, payoff_fn: Optional[Callable] = None) -> dict:
        """Net payoff per position; always zero-sum (sums to 0).

        `hands` maps position -> that position's hand. For a showdown,
        `payoff_fn(live_hands, pot)` must return a list of *raw* pot
        shares (e.g. equity * pot) in the same order as `live_hands`
        (the live, non-folded positions in table order) — this method
        subtracts each player's investment for you. It's only called
        (and only needs to be supplied) for showdown terminals; a
        fold-out is resolved from the pot/investment bookkeeping alone.
        """
        live = [p for p in self.invested if p not in self.folded]
        if len(live) == 1:
            winner = live[0]
            return {
                p: (self.pot - self.invested[p] if p == winner else -self.invested[p])
                for p in self.invested
            }
        if payoff_fn is None:
            raise ValueError("payoff_fn is required to score a showdown terminal")
        shares = payoff_fn([hands[p] for p in live], self.pot)
        result = {p: -self.invested[p] for p in self.folded}
        for position, share in zip(live, shares):
            result[position] = share - self.invested[position]
        return result


@dataclass(frozen=True)
class DecisionNode:
    """A node where `player_to_act` chooses among `children`."""

    player_to_act: str
    pot: float
    invested: dict
    folded: frozenset
    raises_so_far: int
    children: dict  # Action -> DecisionNode | TerminalNode

    @property
    def legal_actions(self) -> list:
        return list(self.children.keys())


def _raise_total_size(raise_number: int, big_blind: float, previous_bet: float, raise_sizes: tuple) -> float:
    """Total committed size for raise number `raise_number` (1-indexed)."""
    multiplier = raise_sizes[raise_number - 1]
    if raise_number == 1:
        return multiplier * big_blind
    return multiplier * previous_bet


def _reopened_order(config: GameConfig, raiser: str, invested: dict, folded: frozenset) -> list:
    """Table order starting right after `raiser`, excluding `raiser`
    itself and anyone folded or already all-in (they have no decision
    left to make)."""
    idx = config.positions.index(raiser)
    order_after = config.positions[idx + 1 :] + config.positions[:idx]
    return [p for p in order_after if p not in folded and invested[p] < config.stack_bb]


def _build(
    config: GameConfig,
    invested: dict,
    folded: frozenset,
    raises_so_far: int,
    previous_bet: float,
    to_act: list,
):
    live = [p for p in config.positions if p not in folded]
    pot = sum(invested.values())

    if len(live) == 1 or not to_act:
        return TerminalNode(pot=pot, invested=dict(invested), folded=folded)

    player, rest = to_act[0], to_act[1:]
    current_bet = max(invested[p] for p in live)
    to_call = current_bet - invested[player]
    remaining_stack = config.stack_bb - invested[player]

    children = {}

    if to_call > 0:
        children[Action(FOLD)] = _build(config, invested, folded | {player}, raises_so_far, previous_bet, rest)

    call_invested = dict(invested)
    call_invested[player] = current_bet
    children[Action(CALL_OR_CHECK)] = _build(config, call_invested, folded, raises_so_far, previous_bet, rest)

    next_raise_number = raises_so_far + 1
    if next_raise_number <= config.max_raises and remaining_stack > to_call:
        reopened = _reopened_order(config, player, invested, folded)
        if next_raise_number < config.max_raises:
            size = _raise_total_size(next_raise_number, config.big_blind, previous_bet, config.raise_sizes)
            if size < config.stack_bb:
                raise_invested = dict(invested)
                raise_invested[player] = size
                children[Action(RAISE, size)] = _build(
                    config, raise_invested, folded, next_raise_number, size, reopened
                )
        jam_invested = dict(invested)
        jam_invested[player] = config.stack_bb
        children[Action(ALL_IN, config.stack_bb)] = _build(
            config, jam_invested, folded, next_raise_number, config.stack_bb, reopened
        )

    return DecisionNode(
        player_to_act=player,
        pot=pot,
        invested=dict(invested),
        folded=folded,
        raises_so_far=raises_so_far,
        children=children,
    )


def build_game_tree(config: GameConfig) -> DecisionNode:
    """Build the full N-player preflop betting tree for `config`."""
    invested = {position: 0.0 for position in config.positions}
    invested[config.positions[-2]] = config.small_blind
    invested[config.positions[-1]] = config.big_blind
    return _build(config, invested, frozenset(), 0, config.big_blind, list(config.positions))


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
