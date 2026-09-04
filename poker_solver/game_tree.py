"""N-player betting-action tree, for one betting round at a time.

This module models the sequence of actions (fold / call or check / raise
/ all-in) around a table of N players for a single betting round, in a
fixed table order. Two configs seed that same underlying algorithm
differently: `GameConfig` for preflop (`positions`' last two entries post
blinds, the first raise is sized off the big blind) and `StreetConfig`
for postflop (nobody posts anything — the pot already exists from prior
streets — and the first raise is sized off the pot). Heads-up (2
players) is just the N=2 special case of either for tree *construction*
— true without exception. `button_position`/`postflop_action_order`
below are the one place this module does carry a genuine, real-poker-
rule heads-up exception (the button and small blind are the same seat
at N=2) — see their own docstrings for why that's a fact about seating,
not something either config's tree-building logic needs to know about.

It deliberately does not branch on hole cards or hand classes anywhere —
the same tree is reused for every combination of hands across all N
players during CFR solving. Terminal nodes instead expose a generic
`payoff_fn` seam: whoever solves the tree supplies a function mapping
(a list of the live players' hands, pot) to their raw shares of the pot,
so this module has no dependency on cards/equity concepts at all.
Preflop's payoff_fn is backed by the preflop equity table
(poker_solver/equity.py); a flop-only tree's is backed by
poker_solver/board_equity.py's board-aware combo equity instead — same
seam, different value source, no changes needed here either way.

This is a single-street model: any action sequence that isn't a fold (a
limp-and-check, a raise that gets called, a jam that gets called) ends
immediately at a "showdown" terminal valued via the injected payoff
function, standing in for "then the rest of the hand gets played out" —
for a flop-only tree (M11), that means averaging over the remaining
turn+river runouts, not modeling them as explicit further action; a
later milestone can chain multiple street-trees together through chance
nodes for that, without needing to change this module.

No side pots, at any N: both configs require one shared `stack_bb` for
every player (GameConfig: the whole hand's effective stack; StreetConfig:
whatever's left behind entering this street — see StreetConfig's
docstring), and the raise-sizing logic below guarantees no one is ever
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
        _validate_raise_sizes(self.raise_sizes)
        if self.stack_bb < self.big_blind:
            # M117. This used to compare against small_blind, which let a
            # stack shorter than the BIG blind through — and the big blind
            # is posted unconditionally, so the tree then started with
            # invested["BB"] > stack_bb and every pot below it counted
            # chips nobody had. Measured across whole trees, the overstatement
            # is exactly 2 * (big_blind - stack_bb): 96% of the real pot at
            # 0.51bb, 67% at 0.6bb, 0 at and above 1bb. A stack under one
            # big blind is a forced all-in with no decision in it, so
            # refusing is both correct and the only honest answer.
            raise ValueError(
                f"stack_bb ({self.stack_bb}) must be at least big_blind "
                f"({self.big_blind}) — a shorter stack cannot post the blind"
            )
        if self.small_blind <= 0 or self.big_blind <= 0:
            raise ValueError("small_blind and big_blind must be positive")

    @property
    def num_players(self) -> int:
        return len(self.positions)

    @property
    def open_size_reference(self) -> float:
        """What the very first raise of the tree is sized relative to —
        the big blind, preflop's standard reference point. StreetConfig
        (postflop) supplies the same property differently (the pot),
        so `_build`/`_raise_total_size` stay one shared implementation
        rather than needing a preflop-vs-postflop branch."""
        return self.big_blind

    @property
    def pot_offset(self) -> float:
        """Chips already in the pot before this tree's own action, not
        attributable to any tracked `invested` entry. Zero for preflop —
        the whole pot is built from blinds/action all tracked via
        `invested` itself, so `_build` doesn't need anything added on
        top. StreetConfig (postflop) supplies this differently (the pot
        already built on earlier streets, which *isn't* attributable to
        any position's *this-street* `invested`)."""
        return 0.0


def button_position(positions: tuple) -> str:
    """The seat holding the dealer button, given a preflop acting order
    (GameConfig.positions — see its own docstring: the *last two*
    entries post the small/big blind, in that order).

    Real poker rule, not a convention this project invented: the button
    is the seat immediately before the small blind, at any table size —
    `positions[-3]`. Heads-up is a single, genuine exception, not a
    degenerate case of that rule: with only two players, the button
    *is* the small blind (Robert's Rules of Poker, "Button and Blind
    Use"), so `positions[-2] == positions[0]` there and the button is
    `positions[0]`, not `positions[-3]` (which doesn't even exist at
    N=2). See postflop_action_order's own docstring for why this
    exception matters beyond just naming the button correctly.
    """
    if len(positions) < 2:
        raise ValueError("positions must have at least 2 entries")
    if len(positions) == 2:
        return positions[0]
    return positions[-3]


def postflop_action_order(positions: tuple, live_positions: tuple | None = None) -> tuple:
    """`positions` (a preflop acting order) reordered into postflop
    acting order: starting with the first live seat after the button,
    proceeding around the table, ending with the button itself (last,
    if still live) — the universal postflop rule ("Robert's Rules of
    Poker": action begins with the first active player to the left of
    the button, on every betting round after the first), with no
    table-size exception. `positions` is treated as the same circular
    seating ring `_reopened_order` already relies on for reopening
    logic — a genuinely new fact about *this* module, not assumed.

    Optionally filtered to `live_positions` (any subset, in any order —
    the *output* order is what carries the postflop-acting-order
    information, not the input order), for turning a real hand's
    surviving 2+ players into a real postflop acting order. Full,
    unfiltered output is still N-general on purpose, at zero extra
    cost, for whenever true multiway postflop solving (this project's
    own named, still-unscoped next structural gap) needs it — a 2-player
    caller just unpacks the first two entries.

    The common, seductive-but-wrong shortcut is "the small blind acts
    first postflop" — true for 3+ players (SB is the seat immediately
    left of the button there), false at heads-up, where the button
    *is* the small blind and therefore acts LAST postflop, not first.
    Applying a "rotate blinds to the front" formula uniformly across
    all N gets heads-up backwards for exactly this reason; anchoring
    on the button instead (which has no exception) is what makes one
    formula correct at every N.
    """
    button = button_position(positions)
    start = positions.index(button) + 1
    order = positions[start:] + positions[:start]
    if live_positions is None:
        return order
    live = set(live_positions)
    return tuple(p for p in order if p in live)


@dataclass(frozen=True)
class StreetConfig:
    """Parameters for one postflop betting round.

    Unlike GameConfig, nobody posts blinds — the pot already exists from
    prior streets — and the *first* raise is conventionally sized off
    the pot, not a blind (postflop's standard reference point, where
    preflop's is the big blind). `positions` is this street's acting
    order (earliest position first — postflop that's determined by
    table position, not blinds, so there's no "last two post something"
    convention the way GameConfig has). Every position starts the
    street with 0 already invested — `pot` is what's already there from
    earlier streets, not something any one position contributed *this*
    street.

    `stack_bb` is the *remaining* effective stack entering this street
    (each player's original stack minus whatever they already committed
    on earlier streets) — not the hand's original starting stack. `_build`
    reuses exactly the same accounting either way: it only ever compares
    a position's *this-street* `invested` against `stack_bb`, so as long
    as both `invested` (seeded at 0 below) and `stack_bb` consistently
    mean "for this street," GameConfig's and StreetConfig's math is
    identical without either needing to know about the other.
    """

    positions: tuple
    pot: float
    stack_bb: float
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
        _validate_raise_sizes(self.raise_sizes)
        if self.pot <= 0:
            raise ValueError("pot must be positive")
        if self.stack_bb <= 0:
            raise ValueError("stack_bb must be positive")

    @property
    def num_players(self) -> int:
        return len(self.positions)

    @property
    def open_size_reference(self) -> float:
        return self.pot

    @property
    def pot_offset(self) -> float:
        return self.pot


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
    """A leaf of the betting tree: either a fold-out or a showdown."""

    pot: float
    invested: dict  # position -> chips committed *in this tree* (see payoff's note on pot_offset)
    folded: frozenset  # positions that folded

    @property
    def is_showdown(self) -> bool:
        return len(self.invested) - len(self.folded) > 1

    def payoff(self, hands: dict, payoff_fn: Optional[Callable] = None) -> dict:
        """Net payoff per position.

        Zero-sum (sums to 0) for a tree built from GameConfig (preflop)
        — the whole pot is accounted for via `invested`, blinds
        included. For a tree built from StreetConfig (postflop), `pot`
        also includes `config.pot_offset` (the pot already built on
        earlier streets), which isn't attributable to any position's
        `invested` here — so payoffs sum to `pot_offset` instead of 0 in
        that case, not a bug: this method returns each player's net
        result *from this street's own action*, treating the entering
        pot as already at stake rather than freshly contributed by
        anyone this street. `node.pot` itself (used by the actual
        solving code in cfr.py, which doesn't call this method) is
        unaffected either way — it's always the true total pot.

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


class LazyChildren:
    """Dict-like Action -> DecisionNode|TerminalNode mapping that builds
    (and memoizes) each child only the first time it's actually accessed,
    instead of eagerly building the whole subtree upfront.

    Why: the tree is built by re-opening the betting round for every
    remaining live player on every raise, so its size grows combinatorially
    with both player count and max_raises — measured during M9 at
    ~333K terminal nodes for 6-max (still fine eagerly) but ~8.7M for
    9-max at just 3 raises, tens of millions at the default 4 raises.
    Eagerly materializing that whole structure before solving even starts
    is not viable at that scale. But no real traversal (CFR's own
    recursion, MCCFR's sampling, or a human clicking through a handful of
    tree nodes) ever needs more than a tiny fraction of the tree
    materialized at once — so children are built on demand instead,
    keeping memory/time proportional to what's actually visited.
    `legal_actions` (the *set* of actions) is still known upfront without
    building anything, since which actions exist is purely a function of
    this node's own state (pot, stack, raises_so_far), not of what lies
    beneath them.
    """

    def __init__(self, builders: dict):
        # Action -> zero-arg callable that lazily builds that one child
        # (which is itself lazy, if it's a DecisionNode).
        self._builders = builders
        self._built: dict = {}

    def __getitem__(self, action):
        if action not in self._built:
            self._built[action] = self._builders[action]()
        return self._built[action]

    def __iter__(self):
        return iter(self._builders)

    def __len__(self) -> int:
        return len(self._builders)

    def __contains__(self, action) -> bool:
        return action in self._builders

    def keys(self):
        return self._builders.keys()

    def values(self):
        """Builds (and memoizes) *every* child — only use this when you
        actually mean to materialize the whole subtree (tests walking
        the full tree), never on a hot solving path."""
        return (self[action] for action in self._builders)

    def items(self):
        return ((action, self[action]) for action in self._builders)


@dataclass(frozen=True)
class DecisionNode:
    """A node where `player_to_act` chooses among `children`."""

    player_to_act: str
    pot: float
    invested: dict
    folded: frozenset
    raises_so_far: int
    children: "LazyChildren"  # Action -> DecisionNode | TerminalNode, built lazily

    @property
    def legal_actions(self) -> list:
        return list(self.children.keys())


# M206. A client echoes a size back as text it read from a response
# ("raise:12.50"), so an exact float comparison would reject the API's
# own output. Half a cent is far below any real bet increment.
_SIZE_TOLERANCE = 0.005


def resolve_action(node: "DecisionNode", kind: str, size: float | None = None) -> Action:
    """The one real `Action` of kind `kind` legal at `node`, without the
    caller needing to know its exact `size`.

    Every caller that needs a specific `Action` off a node's own
    `legal_actions` (M15/M16's `derive_flop_scenario`/`derive_ranges_
    from_path`, ~30 call sites across the test suite) has always
    inlined the identical `next(a for a in node.legal_actions if a.kind
    == X)` pattern — this is the first shared version of it, added for
    a *live, untrusted* caller (M24's action-path endpoint) that only
    has a bare kind string, not a pre-known exact size.

    Safe by construction, not just in practice: at most one sized RAISE
    action can ever exist at a single node (`_raise_total_size` computes
    one scalar, assigned into the node's action dict exactly once inside
    one `if`, never a loop — see `_build`), and ALL_IN is a disjoint
    kind that never collides with it. So a bare kind never has more than
    one matching `Action` here; no ambiguity case exists to resolve.

    **M205 CORRECTION: M203 broke that invariant.** A raise level may now
    offer a MENU of sizes, so several sized RAISE actions can be legal at
    one node. The old loop returned the FIRST — measured on a
    `(0.33, 0.75, 2.5)` menu it silently returned `raise:3.30`, so a
    client saying "villain raised" would be modelled as facing the
    SMALLEST bet with nothing saying so. That is a confident answer to a
    different question, and this project has shipped enough of those.

    Ambiguity is therefore an ERROR, not a silent choice. A bare kind
    stays valid wherever it is unambiguous, which is every configuration
    shipped today; enabling a menu on a street whose action paths are
    walked requires the caller to name the size, and this is what forces
    that to be a deliberate change rather than a silent one.

    `size` (M206) names WHICH sized action is meant, which is how a
    caller resolves the ambiguity above. Matched within a tolerance
    rather than exactly, because a client echoes back a size it read from
    a response as decimal text and float equality would reject its own
    output.

    Raises `ValueError` (not `StopIteration`) for an unknown kind, a kind
    not currently legal at this node, or a size that is not on offer — a
    clearer error for an untrusted caller than a bare generator
    exhaustion.
    """
    matches = [action for action in node.legal_actions if action.kind == kind]
    if not matches:
        raise ValueError(f"{kind!r} is not legal at this node (legal actions: {node.legal_actions!r})")
    if size is not None:
        for action in matches:
            if action.size is not None and abs(action.size - size) <= _SIZE_TOLERANCE:
                return action
        raise ValueError(
            f"{kind!r} at size {size} is not legal at this node "
            f"(legal actions: {node.legal_actions!r})"
        )
    if len(matches) > 1:
        raise ValueError(
            f"{kind!r} is ambiguous at this node — it matches "
            f"{[str(a) for a in matches]}. Name the size instead of the "
            f"bare kind (M205)."
        )
    return matches[0]


def _validate_raise_sizes(raise_sizes: tuple) -> None:
    """Each entry is a positive multiplier, or a non-empty tuple of them.

    Checked rather than trusted because a malformed menu does not fail —
    an empty tuple silently removes every sized raise at that level, and
    a non-positive multiplier builds a bet nobody made. Both produce a
    tree that passes every legality invariant while modelling the wrong
    game, which is the failure mode this project keeps meeting.
    """
    for level, entry in enumerate(raise_sizes, start=1):
        multipliers = entry if isinstance(entry, (tuple, list)) else (entry,)
        if not multipliers:
            raise ValueError(
                f"raise_sizes[{level - 1}] is an empty menu; a level must "
                "offer at least one sized raise"
            )
        for multiplier in multipliers:
            if not isinstance(multiplier, (int, float)) or multiplier <= 0:
                raise ValueError(
                    f"raise_sizes[{level - 1}] contains {multiplier!r}; "
                    "every multiplier must be a positive number"
                )


def _raise_total_sizes(raise_number: int, open_size_reference: float,
                       previous_bet: float, raise_sizes: tuple) -> tuple:
    """Every total size offered at raise number `raise_number` (1-indexed).

    An entry of `raise_sizes` is either a single multiplier — one sized
    raise at that level, the original behaviour — or a TUPLE of them, a
    **menu** the solver picks from as part of its strategy (M203).

    `open_size_reference` is what the *first* raise is sized relative to
    — GameConfig.open_size_reference (the big blind) for preflop,
    StreetConfig.open_size_reference (the pot) for postflop; every raise
    after the first is always sized relative to the previous bet,
    regardless of which kind of config this is.

    Sizes are deduplicated, in order: two multipliers can land on the
    same total, and `Action(RAISE, size)` values are dict keys during
    tree construction, so a duplicate would silently drop a branch
    rather than raise.
    """
    entry = raise_sizes[raise_number - 1]
    multipliers = entry if isinstance(entry, (tuple, list)) else (entry,)
    reference = open_size_reference if raise_number == 1 else previous_bet
    seen, out = set(), []
    for multiplier in multipliers:
        size = multiplier * reference
        if size not in seen:
            seen.add(size)
            out.append(size)
    return tuple(out)


def _raise_total_size(raise_number: int, open_size_reference: float, previous_bet: float, raise_sizes: tuple) -> float:
    """The FIRST total size at `raise_number` — the single-size case.

    Kept because callers and tests predating the menu (M203) expect one
    float back; `_raise_total_sizes` is what the tree builder uses.
    """
    return _raise_total_sizes(raise_number, open_size_reference, previous_bet, raise_sizes)[0]


def _reopened_order(config: GameConfig, raiser: str, invested: dict, folded: frozenset) -> list:
    """Table order starting right after `raiser`, excluding `raiser`
    itself and anyone folded or already all-in (they have no decision
    left to make).

    The all-in half of that exclusion is UNREACHABLE under this tree's
    equal-stacks model, and M117 confirmed it: instrumented over 1,880
    calls across twelve configs it never once excluded anybody. The
    reason is worth keeping, because it is not obvious. Once any player
    is all-in, `current_bet` equals `stack_bb`, so for every remaining
    player `to_call` and `remaining_stack` are the same number — and
    `_build` offers a raise only when `remaining_stack > to_call`,
    strictly. So no raise can follow an all-in, and `_reopened_order` is
    only ever called from a raise. **Do not delete the clause as dead
    code**: it is the guard that would matter the moment stacks stopped
    being equal, which is exactly the assumption M23's no-side-pots
    proof rests on. `test_no_raise_is_offered_once_anyone_is_all_in`
    pins the property that makes it dead.
    """
    idx = config.positions.index(raiser)
    order_after = config.positions[idx + 1 :] + config.positions[:idx]
    return [p for p in order_after if p not in folded and invested[p] < config.stack_bb]


def _build(
    config,
    invested: dict,
    folded: frozenset,
    raises_so_far: int,
    previous_bet: float,
    to_act: list,
):
    """Builds one node. `config` is either a GameConfig (preflop) or a
    StreetConfig (postflop) — this function reads only the properties
    they share (`positions`, `stack_bb`, `raise_sizes`, `max_raises`,
    `open_size_reference`, `pot_offset`), so the same tree-construction
    algorithm serves both without needing to know which kind of config
    it has.

    For a DecisionNode, `children` is populated with zero-arg *builders*
    (closures), not already-built child nodes — see LazyChildren. Each
    closure captures its own branch's state (the `dict(invested)` copies
    etc.) exactly as before; the only change from the old eager version
    is that the recursive `_build(...)` call is deferred into a lambda
    instead of being made immediately.
    """
    live = [p for p in config.positions if p not in folded]
    # `pot_offset` is 0 for preflop (the whole pot is built from
    # `invested` itself, blinds included) and the entering pot for a
    # postflop street (built on earlier streets, not attributable to any
    # one position's *this-street* `invested`, which starts at 0) — see
    # GameConfig.pot_offset / StreetConfig.pot_offset.
    pot = config.pot_offset + sum(invested.values())

    if len(live) == 1 or not to_act:
        return TerminalNode(pot=pot, invested=dict(invested), folded=folded)

    player, rest = to_act[0], to_act[1:]
    current_bet = max(invested[p] for p in live)
    to_call = current_bet - invested[player]
    remaining_stack = config.stack_bb - invested[player]

    builders = {}

    if to_call > 0:
        folded_after = folded | {player}
        builders[Action(FOLD)] = lambda: _build(config, invested, folded_after, raises_so_far, previous_bet, rest)

    call_invested = dict(invested)
    call_invested[player] = current_bet
    builders[Action(CALL_OR_CHECK)] = lambda: _build(config, call_invested, folded, raises_so_far, previous_bet, rest)

    next_raise_number = raises_so_far + 1
    if next_raise_number <= config.max_raises and remaining_stack > to_call:
        reopened = _reopened_order(config, player, invested, folded)
        if next_raise_number < config.max_raises:
            for size in _raise_total_sizes(next_raise_number, config.open_size_reference,
                                           previous_bet, config.raise_sizes):
                if size >= config.stack_bb:
                    # At or beyond the stack this is the all-in below, and
                    # a sized action equal to it would collide with that
                    # action's own dict key.
                    continue
                raise_invested = dict(invested)
                raise_invested[player] = size
                # Bind per iteration. With one size a bare closure was
                # safe; with a menu, late binding would give every branch
                # the LAST size's subtree - the whole tree silently wrong
                # while every node still looked legal.
                builders[Action(RAISE, size)] = (
                    lambda _ri=raise_invested, _s=size: _build(
                        config, _ri, folded, next_raise_number, _s, reopened
                    )
                )
        jam_invested = dict(invested)
        jam_invested[player] = config.stack_bb
        builders[Action(ALL_IN, config.stack_bb)] = lambda: _build(
            config, jam_invested, folded, next_raise_number, config.stack_bb, reopened
        )

    return DecisionNode(
        player_to_act=player,
        pot=pot,
        invested=dict(invested),
        folded=folded,
        raises_so_far=raises_so_far,
        children=LazyChildren(builders),
    )


def build_game_tree(config: GameConfig) -> DecisionNode:
    """Build the root of the N-player preflop betting tree for `config`.

    Only the root itself is built eagerly — every DecisionNode's
    `children` is a LazyChildren mapping that builds (and memoizes) each
    child only when it's actually accessed. This is load-bearing, not
    just an optimization: the full tree's size grows combinatorially
    with both player count and max_raises (every raise re-opens the
    round for every remaining live player), reaching tens of millions of
    nodes at 9-max with the default raise cap — not viable to
    materialize upfront before solving even starts. `walk`/
    `count_terminal_nodes`/`tree_depth` below still fully materialize
    the tree (that's their point), just pay that cost when actually
    called rather than here.
    """
    invested = {position: 0.0 for position in config.positions}
    invested[config.positions[-2]] = config.small_blind
    invested[config.positions[-1]] = config.big_blind
    # M117: a player who is already all-in from posting a blind has no
    # decision to make, so they must not open the round in `to_act`.
    # This only ever bites at exactly `stack_bb == big_blind` (shorter is
    # refused above), and it did: the big blind was handed a decision node
    # whose one action was a call it had no chips left to make. The
    # analogous filter for all-in-by-action lives in `_reopened_order`,
    # which is reached only from a raise — and no raise can follow an
    # all-in, so it could never cover this case.
    to_act = [p for p in config.positions if invested[p] < config.stack_bb]
    return _build(config, invested, frozenset(), 0, config.open_size_reference, to_act)


def build_street_tree(config: StreetConfig) -> DecisionNode:
    """Build the root of a single postflop betting round for `config` —
    reuses the exact same `_build` algorithm as build_game_tree (see
    StreetConfig's docstring for why no changes to `_build` itself were
    needed), just seeded differently: every position starts this street
    with 0 invested (StreetConfig.pot already accounts for everything
    committed on earlier streets — nobody posts anything fresh here the
    way preflop's blinds do), acting in `config.positions`' order
    starting from its first entry (postflop's action order is
    positional, not blind-determined).
    """
    invested = {position: 0.0 for position in config.positions}
    return _build(config, invested, frozenset(), 0, config.open_size_reference, list(config.positions))


def walk(node):
    """Yield every node in the tree (DecisionNode and TerminalNode), DFS.

    Fully materializes the tree as it goes (see build_game_tree) — fine
    for tests/introspection on small-to-medium trees, not meant to be
    called on a hot solving path or on a huge (9-max, high max_raises)
    tree.
    """
    yield node
    if isinstance(node, DecisionNode):
        for child in node.children.values():
            yield from walk(child)


def count_terminal_nodes(node) -> int:
    """Fully materializes the subtree under `node` (see walk's docstring
    for the same caveat)."""
    if isinstance(node, TerminalNode):
        return 1
    return sum(count_terminal_nodes(child) for child in node.children.values())


def tree_depth(node) -> int:
    """Number of decision points on the longest path from `node` down.

    Fully materializes the subtree under `node` (see walk's docstring
    for the same caveat) — `not node.children` below is safe (checks
    LazyChildren's __len__, doesn't build anything) but the recursive
    call into `.values()` does.
    """
    if isinstance(node, TerminalNode) or not node.children:
        return 0
    return 1 + max(tree_depth(child) for child in node.children.values())
