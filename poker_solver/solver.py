"""Public entrypoint: solve a heads-up or multiway preflop spot end to end.

Wires together game_tree (the betting tree), equity (showdown values),
and cfr (the solving loop) behind one function, `solve_preflop`, and
formats the result into simple hand -> action -> frequency dictionaries.

Dispatches internally between cfr.py's two solving paths based on player
count: the exact, exhaustive `solve()` for heads-up (kept as a fast path,
unchanged since M4), and `mccfr_solve()` for 3+ players (exhaustive CFR
doesn't scale past 2 players — see cfr.py's module docstring and the
project plan). Both return the same {id(node): InfoSetTable} shape, so
StrategyResult doesn't need to know which path produced it.
"""

import random
import time
from dataclasses import dataclass, field

import numpy as np

from .abstraction import BucketedPool, bucket_reach_vector, build_bucket_equity_table, build_hand_buckets
from .board_equity import DEFAULT_SEED as DEFAULT_EQUITY_SEED
from .board_equity import build_board_equity_table
from .chance import build_chance_node
from .cfr import InfoSetTable, mccfr_solve, solve
from .equity import MultiwayEquityCache, get_equity_table
from .game_tree import (
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
)
from .starting_hands import all_starting_hands

# Exact path (heads-up): measured ~3-4ms/iteration at full 169-hand
# scale (M4) — 1000 iterations lands comfortably in low-single-digit
# seconds.
DEFAULT_ITERATIONS = 1000

# MCCFR path (3+ players): needs far more iterations than the exact
# path to control sampling variance — see the M8 benchmarks/PR for the
# concrete numbers this was tuned against.
DEFAULT_MCCFR_ITERATIONS = 50_000


@dataclass
class StrategyResult:
    """The outcome of solving one GameConfig (preflop) or StreetConfig
    (a single postflop street, e.g. M11's flop-only tree) — this class
    only ever reads `config.positions`, which both configs provide, so
    one result type serves either without needing to know which kind of
    config actually produced it."""

    config: object  # GameConfig | StreetConfig
    root: DecisionNode
    hands: list
    node_data: dict
    iterations: int
    elapsed_seconds: float
    # M12: {id(showdown terminal): chance.ChanceNode} — empty for every
    # pre-M12 result (solve_preflop/solve_flop never touch it), populated
    # by solve_flop_turn/solve_flop_to_river. Lets a caller walk into a
    # specific next-card branch's subtree, e.g.
    # `result.chance_data[id(some_terminal)].branches[some_card].root`.
    # M13: for a solve_flop_to_river result, this dict is flat but
    # two-level deep — it holds *both* flop-terminal -> turn-ChanceNode
    # entries *and* turn-terminal -> river-ChanceNode entries (every
    # chance dispatch, at any depth, memoizes into this same dict, keyed
    # by whichever terminal triggered it), so reaching the river level is
    # the identical pattern one hop further:
    # `result.chance_data[id(some_turn_terminal)].branches[some_river_card].root`.
    chance_data: dict = field(default_factory=dict)

    def strategy_at(self, node: DecisionNode) -> dict:
        """hand label -> {action label -> frequency} at any node in the
        tree this result was solved for.

        Falls back to the uniform strategy if `node` has no entry in
        `node_data` at all — MCCFR (unlike the exact HU solver) only
        visits nodes actually reached along a sampled/traversed path, so
        a node reachable only via a low-probability combination of
        earlier actions (e.g. "every one of 8 earlier players limps" at
        a 9-max table with a modest iteration budget) can genuinely go
        unvisited. This mirrors InfoSetTable.average_strategy()'s own
        fallback for a *visited* node with no accumulated strategy_sum
        yet, so an unvisited node behaves the same as a visited-but-
        untrained one — consistent, not a special case.
        """
        actions = node.legal_actions
        table = self.node_data.get(id(node))
        if table is None:
            table = InfoSetTable.zeros(len(self.hands), len(actions))
        avg = table.average_strategy()
        return {
            str(hand): {str(action): float(avg[hand_idx, a_idx]) for a_idx, action in enumerate(actions)}
            for hand_idx, hand in enumerate(self.hands)
        }

    def opening_range(self) -> dict:
        """The first-to-act position's strategy at their very first
        decision — preflop, that's BTN's RFI spot; for a flop-only
        result (M11), it's whichever position acts first on that
        street's `positions` (OOP, by convention)."""
        return self.strategy_at(self.root)

    def node_for_position(self, position: str) -> DecisionNode:
        """The decision node for `position`'s first turn to act, reached
        by everyone before them calling/checking (i.e. "the action folds
        around to you with no raise yet") — the natural generalization of
        `opening_range()`'s "BTN's very first decision" to a position that
        isn't first to act. Every position gets exactly one such node,
        found by walking call_or_check from the root: the first player to
        act is the root itself, and each subsequent position's turn
        arrives right after everyone before them has called/checked.
        """
        if position not in self.config.positions:
            raise ValueError(f"{position!r} is not one of {self.config.positions}")
        node = self.root
        while node.player_to_act != position:
            call_action = next(a for a in node.legal_actions if a.kind == CALL_OR_CHECK)
            node = node.children[call_action]
        return node

    def strategy_for_position(self, position: str) -> dict:
        """`position`'s strategy (hand -> action -> frequency) the first
        time it's their turn, assuming no raise has happened yet — see
        node_for_position. For the first-to-act position this is exactly
        opening_range()."""
        return self.strategy_at(self.node_for_position(position))

    def continuing_frequencies(self, node: DecisionNode, action_kind: str | None = None) -> dict:
        """hand -> frequency at `node`, keyed by the actual `StartingHand`/
        combo *objects* from `self.hands` — deliberately NOT the
        string-keyed shape `strategy_at` returns. This exists specifically
        to feed `combos.range_from_class_frequencies` (M10), which reads
        `hand.high_rank`/`.low_rank` off its keys and so needs the real
        objects, not display labels; `strategy_at`'s own output can't be
        repurposed for this since it discards the object (keeps only
        `str(hand)`), and the string->object parser was removed as dead
        code back at M10 (callers were expected to already have the
        objects a StrategyResult was solved with — this is that expected
        caller).

        `action_kind=None` (default) sums every non-fold action's
        frequency — the "1 - fold probability" continue-frequency
        `range_from_class_frequencies`'s own docstring already
        anticipated. `action_kind=<a game_tree.py kind constant>` (e.g.
        RAISE) instead isolates *one specific action's* frequency —
        needed because a single node can have both a sized RAISE and an
        ALL_IN simultaneously (`game_tree._build` adds both
        independently when applicable), each leading to a *different*
        pot; summing them together would misattribute a hand that
        prefers jamming into a range meant to represent a specific sized
        raise's own pot. Raises ValueError if `action_kind` isn't legal
        at this node at all (e.g. requesting RAISE where no sized raise
        exists).
        """
        actions = node.legal_actions
        table = self.node_data.get(id(node))
        if table is None:
            table = InfoSetTable.zeros(len(self.hands), len(actions))
        avg = table.average_strategy()
        if action_kind is None:
            keep = [a_idx for a_idx, action in enumerate(actions) if action.kind != FOLD]
        else:
            keep = [a_idx for a_idx, action in enumerate(actions) if action.kind == action_kind]
            if not keep:
                raise ValueError(f"no action of kind {action_kind!r} exists at this node")
        return {hand: float(avg[hand_idx, keep].sum()) for hand_idx, hand in enumerate(self.hands)}


def solve_preflop(
    stack_bb: float = 100.0,
    iterations: int = None,
    config: GameConfig = None,
    hands: list = None,
    equity_table=None,
    equity_cache: MultiwayEquityCache = None,
    seed: int = 0,
) -> StrategyResult:
    """Solve a preflop spot and return its strategy.

    Either pass `stack_bb` for the default blind/sizing/raise-cap
    settings (heads-up), or pass a fully custom `config` directly (e.g.
    with `positions=("BTN", "SB", "BB")` for 3-max).

    `hands` defaults to the full 169-class canonical set — override for
    smaller/faster test solves. `equity_table` (heads-up only) and
    `equity_cache` (3+ players only) default to the real disk-cached
    table / a fresh lazy cache respectively — override together with
    `hands` for fast, self-contained testing.

    `iterations` defaults to DEFAULT_ITERATIONS for heads-up or
    DEFAULT_MCCFR_ITERATIONS for 3+ players — the two paths need very
    different scales, so leave it unset unless you have a specific
    reason to override.
    """
    config = config if config is not None else GameConfig(stack_bb=stack_bb)
    root = build_game_tree(config)
    hands = hands if hands is not None else all_starting_hands()

    start = time.perf_counter()
    if config.num_players == 2:
        actual_iterations = iterations if iterations is not None else DEFAULT_ITERATIONS
        equity_table = equity_table if equity_table is not None else get_equity_table()
        node_data = solve(root, hands, equity_table, iterations=actual_iterations)
    else:
        actual_iterations = iterations if iterations is not None else DEFAULT_MCCFR_ITERATIONS
        equity_cache = equity_cache if equity_cache is not None else MultiwayEquityCache(hands=hands)
        node_data = mccfr_solve(
            root, hands, config.positions, equity_cache, iterations=actual_iterations, seed=seed
        )
    elapsed = time.perf_counter() - start

    return StrategyResult(
        config=config,
        root=root,
        hands=hands,
        node_data=node_data,
        iterations=actual_iterations,
        elapsed_seconds=elapsed,
    )


@dataclass(frozen=True)
class FlopScenario:
    """Ranges + pot/stack state for a flop, derived from a preflop
    StrategyResult for one specific line: `raiser_position` opens,
    `caller_position` calls, everyone else (if any) already folded
    before either of them acted — the single most common way a real pot
    actually reaches a flop. Not multiway, not a limped pot, not a
    3-bet pot; see derive_flop_scenario for why each of those is
    explicitly out of scope here (M15).

    `raiser_range`/`caller_range` map StartingHand -> that hand's
    frequency of reaching *this exact* line (not each position's overall
    continue frequency — see derive_flop_scenario for why that
    distinction matters). Feed each through
    combos.range_from_class_frequencies(..., exclude=frozenset(board))
    to get a solve_flop/solve_flop_turn/solve_flop_to_river-ready combo
    range; `pot`/`effective_stack_bb` are already exactly what those
    functions' own `pot`/`effective_stack_bb` parameters expect.
    """

    raiser_position: str
    caller_position: str
    raiser_range: dict
    caller_range: dict
    pot: float
    effective_stack_bb: float


@dataclass(frozen=True)
class PathScenario:
    """Ranges + pot/stack state after walking an arbitrary `action_path`
    from `result.root` (M16) — generalizes `FlopScenario` beyond its
    fixed 2-step "raiser opens, caller calls" shape to any legal
    sequence of actions, at any street's tree, with any position acting
    any number of times along the way. See `derive_ranges_from_path`.

    `node` is the DecisionNode or TerminalNode `action_path` leads to —
    exposed (unlike `FlopScenario`, which only ever exposes pot/stack)
    since a general-purpose caller may need to keep exploring from
    there (what actions remain legal, is it a showdown, etc.).

    `live_positions` is every position still in the hand at `node`, in
    table order. `ranges` maps each live position -> {hand: frequency}
    (StartingHand/HandCombo-keyed, like `continuing_frequencies`) — that
    position's per-hand probability of taking *this exact* path, not
    their overall continue frequency. A position that never acted along
    the path (still waiting their turn at `node`) gets frequency 1.0 for
    every hand — unconditioned, since nothing has happened yet to filter
    their range.

    `pot`/`stacks` are `node.pot` and, per live position, `result.
    config.stack_bb - node.invested[position]` — a dict, not a single
    float like `FlopScenario.effective_stack_bb`, since an arbitrary
    path can leave live positions with unequal investments (e.g. a
    3-way pot reached mid-round, action not yet closed).
    """

    node: object  # DecisionNode | TerminalNode
    live_positions: tuple
    ranges: dict  # position -> {hand: frequency}
    pot: float
    stacks: dict  # position -> remaining effective stack


def derive_ranges_from_path(result: StrategyResult, action_path: list) -> PathScenario:
    """Walk `action_path` (a list of `game_tree.Action`s) from `result.
    root`, deriving every still-live position's range at the resulting
    node plus pot/stack state (M16) — the general mechanism
    `derive_flop_scenario` is now a thin, backward-compatible wrapper
    around (see below).

    Each `Action` must be obtained from the node it applies to's own
    `legal_actions` (e.g. `next(a for a in node.legal_actions if a.kind
    == RAISE)`), not hand-constructed — `Action` equality is exact on
    both `kind` and `size`, so a hand-built `Action(RAISE, 17.3)` that's
    off by float precision from the tree's own sized raise would be
    (correctly) rejected as illegal rather than silently matched. This
    matters more here than it did for `derive_flop_scenario` (which
    always looked actions up this way already): a general-purpose
    caller assembling a path from scratch — e.g. a future natural-
    language-to-action-path translation layer — is more likely to
    construct one by hand.

    For a position that acts more than once along the path (e.g. opens,
    faces a 3-bet, calls), their derived range is the *product*, in
    order, of `continuing_frequencies(node, action_kind=<the kind of
    the action they took at that node>)` across every one of their own
    decision nodes along the way — not just a single node's reading.
    This mirrors exactly how `cfr.py`'s own `reach_a`/`reach_b` tensors
    accumulate reach probability during real CFR solving (multiplying
    in that position's `current_strategy()` column at each of their own
    nodes, chained down the tree) — `continuing_frequencies` at any one
    node is a *conditional* frequency (given the range already reached
    that node), so multiple such nodes compose multiplicatively, the
    same way conditional probabilities always do.

    A path doesn't need to fully close the betting round — ending at a
    DecisionNode (someone else's turn still to come) is a valid result,
    not an error; any position who hasn't yet acted along the path is
    included in `live_positions`/`ranges` with an unconditioned (1.0)
    range. This lets a caller ask "what does this look like right here,
    mid-round," not just at a fully-resolved endpoint.

    Raises ValueError if: an action in `action_path` isn't actually
    legal at the node it's applied to; `action_path` has steps left
    after the walk has already reached a TerminalNode (the hand ended
    before the path did); fewer than 2 positions are still live at the
    resulting node (a fold-out, or an N-1-folded multiway remainder —
    not a postflop scenario either way).
    """
    node = result.root
    reach = {position: {hand: 1.0 for hand in result.hands} for position in result.config.positions}

    for step_idx, action in enumerate(action_path):
        if not isinstance(node, DecisionNode):
            raise ValueError(
                f"action_path continues past a terminal node after step {step_idx} "
                f"({len(action_path) - step_idx} action(s) left, but the hand already ended)"
            )
        if action not in node.legal_actions:
            raise ValueError(
                f"step {step_idx}: {action} is not legal for {node.player_to_act!r} "
                f"at this node (legal actions: {node.legal_actions!r})"
            )
        actor = node.player_to_act
        freqs = result.continuing_frequencies(node, action_kind=action.kind)
        reach[actor] = {hand: reach[actor][hand] * freqs[hand] for hand in result.hands}
        node = node.children[action]

    live_positions = tuple(p for p in result.config.positions if p not in node.folded)
    if len(live_positions) < 2:
        raise ValueError(
            f"action_path ends with {len(live_positions)} live position(s) "
            "— not a postflop scenario (need at least 2)"
        )

    return PathScenario(
        node=node,
        live_positions=live_positions,
        ranges={p: reach[p] for p in live_positions},
        pot=node.pot,
        stacks={p: result.config.stack_bb - node.invested[p] for p in live_positions},
    )


def derive_flop_scenario(result: StrategyResult, raiser_position: str, caller_position: str) -> FlopScenario:
    """Derive a FlopScenario from a preflop StrategyResult's "raiser
    opens, caller calls" line.

    Requires `result` to have come from a heads-up (2-player) preflop
    solve — a real multiway pot (3+ players still live after the raise)
    isn't modeled by this milestone, so a 3+ player config is rejected
    outright rather than silently producing a scenario that ignores
    whoever else might still be live.

    `raiser_position` must be the *first* position to act (`result.
    root.player_to_act`), not just any position with a sized raise
    somewhere in the tree — deliberately NOT resolved via
    `node_for_position` (which walks call_or_check from root, i.e.
    "everyone before you limped"): for a non-first-to-act position that
    would silently derive a scenario for raising *over an earlier limp*,
    a materially different, out-of-scope line, not the immediate-open
    this function is documented to model.

    `raiser_range`/`caller_range` end up specific to the raise-then-call
    line (via `derive_ranges_from_path`'s reach-product mechanism, M16),
    not the simpler "1 - fold" default — load-bearing, not stylistic: a
    single node can have both a sized RAISE and an ALL_IN, each leading
    to a different pot, and `pot`/`effective_stack_bb` below are read
    off the *specific* raise-then-call child, not an average over every
    non-fold line. Using the "1-fold" default for either range would
    silently weight in hands that actually took a different (jam, or
    3-bet) line into a (pot, stack) pair that isn't theirs.

    Raises ValueError for: a 3+ player `result`; either position not in
    `result.config.positions`; `raiser_position` not first to act; no
    sized raise available at the raiser's node; `caller_position` not
    the position that actually acts right after the raise.

    M16: implemented as a thin wrapper around `derive_ranges_from_path`
    (the general path-walking mechanism) — everything above this point
    stays as `FlopScenario`-specific semantic validation (stricter than
    a general path needs, e.g. "caller must be the very next actor"),
    then the mechanical walk itself is delegated, not reimplemented.
    """
    if result.config.num_players != 2:
        raise ValueError("derive_flop_scenario only models a heads-up raiser/caller pair, not a multiway pot")
    if raiser_position not in result.config.positions:
        raise ValueError(f"{raiser_position!r} is not one of {result.config.positions}")
    if caller_position not in result.config.positions:
        raise ValueError(f"{caller_position!r} is not one of {result.config.positions}")
    if raiser_position != result.root.player_to_act:
        raise ValueError(
            f"{raiser_position!r} is not the first position to act "
            f"({result.root.player_to_act!r} is) — this only models an immediate "
            "open-raise, not a raise over an earlier limp"
        )

    raiser_node = result.root
    raise_action = next((a for a in raiser_node.legal_actions if a.kind == RAISE), None)
    if raise_action is None:
        raise ValueError(f"{raiser_position!r} has no sized-raise action at their first decision")

    caller_node = raiser_node.children[raise_action]
    if caller_node.player_to_act != caller_position:
        raise ValueError(f"{caller_position!r} doesn't act directly after {raiser_position!r}'s raise")

    call_action = next((a for a in caller_node.legal_actions if a.kind == CALL_OR_CHECK), None)
    if call_action is None:
        # Structurally always present (game_tree._build adds CALL_OR_CHECK
        # unconditionally at every DecisionNode) — guarded explicitly
        # anyway, matching the raise_action lookup right above, rather
        # than letting a future game_tree.py change surface as a bare
        # StopIteration here instead of a clear error.
        raise ValueError(f"no call action found at {caller_position!r}'s node")

    scenario = derive_ranges_from_path(result, [raise_action, call_action])
    return FlopScenario(
        raiser_position=raiser_position,
        caller_position=caller_position,
        raiser_range=scenario.ranges[raiser_position],
        caller_range=scenario.ranges[caller_position],
        pot=scenario.pot,
        effective_stack_bb=scenario.stacks[raiser_position],
    )


# Flop tree is heads-up (2 positions), same shape as preflop's exact
# fast path — same default as DEFAULT_ITERATIONS, tuned for M11's
# curated-range demo scale, not the full ~1176-combo case (see
# board_equity.py's module-level comment for the measured reason a wide
# range isn't there yet).
DEFAULT_FLOP_ITERATIONS = 1000


def solve_flop(
    board: tuple,
    hero_range: dict,
    villain_range: dict,
    pot: float,
    effective_stack_bb: float,
    positions: tuple = ("OOP", "IP"),
    raise_sizes: tuple = (2.5, 3.0, 2.2),
    max_raises: int = 4,
    iterations: int = None,
    equity_samples: int = None,
    equity_seed: int = DEFAULT_EQUITY_SEED,
) -> StrategyResult:
    """Solve a single flop betting round and return its strategy.

    `board` is the flop (3 Cards). `hero_range`/`villain_range` map
    combos.HandCombo -> weight — see combos.range_from_class_frequencies
    for the natural way to build one from a preflop solve's per-class
    continue-frequency output. They become `positions`' two combo pools
    *and* their starting reach weights, in `positions` order
    (`hero_range` for `positions[0]`, `villain_range` for `positions[1]`)
    — a combo missing from a range gets 0 weight for that position, not
    an error, exactly like a class the preflop solve folded 100% of the
    time contributes nothing to a range built via
    range_from_class_frequencies.

    `pot`/`effective_stack_bb` describe the state entering the flop —
    see StreetConfig for exactly what each means (`effective_stack_bb`
    is what's left *behind*, not the hand's original starting stack).

    Reuses cfr.solve()'s exact tensor CFR — this is a heads-up (2
    position), single-street tree, the same shape as preflop's exact
    fast path, not a new solving loop (see cfr.py's module docstring for
    the generalizations — custom position labels, custom starting reach
    — that make this direct reuse possible).

    Hero's and villain's ranges are combined into one shared combo pool
    (the union of both, matching cfr.solve()'s single `hands` list/NxN
    equity_table design) — some (hero combo, villain combo) pairs in
    that pool inevitably share a physical card (e.g. hero's AhKh and
    villain's AhQc both need the Ah), which board_equity.
    build_board_equity_table correctly reports as an undefined (NaN)
    matchup. Those NaN entries are replaced with a neutral 0.5 before
    solving — the same "true probability is ~0 anyway, so any neutral
    placeholder is fine, it just must not poison the computation"
    reasoning equity.MultiwayEquityCache already uses for the analogous
    N>=3 preflop case (see its docstring) — cfr.solve()'s reach vectors
    don't account for *cross-position* card removal either (each
    position's range is supplied independently), the same "ignore
    blockers between players' hands" approximation carried since M1,
    just now also visible at combo (not just class) granularity.
    """
    hero_position, villain_position = positions
    combos = sorted(set(hero_range) | set(villain_range), key=str)
    hero_reach = np.array([hero_range.get(combo, 0.0) for combo in combos])
    villain_reach = np.array([villain_range.get(combo, 0.0) for combo in combos])

    config = StreetConfig(
        positions=positions,
        pot=pot,
        stack_bb=effective_stack_bb,
        raise_sizes=raise_sizes,
        max_raises=max_raises,
    )
    root = build_street_tree(config)

    equity_kwargs = {"rng": random.Random(equity_seed)}
    if equity_samples is not None:
        equity_kwargs["samples"] = equity_samples
    equity_table = build_board_equity_table(board, combos, **equity_kwargs)
    equity_table = np.nan_to_num(equity_table, nan=0.5)

    actual_iterations = iterations if iterations is not None else DEFAULT_FLOP_ITERATIONS
    start = time.perf_counter()
    node_data = solve(
        root,
        combos,
        equity_table,
        iterations=actual_iterations,
        positions=positions,
        initial_reach={hero_position: hero_reach, villain_position: villain_reach},
    )
    elapsed = time.perf_counter() - start

    return StrategyResult(
        config=config,
        root=root,
        hands=combos,
        node_data=node_data,
        iterations=actual_iterations,
        elapsed_seconds=elapsed,
    )


def solve_flop_abstracted(
    board: tuple,
    hero_range: dict,
    villain_range: dict,
    pot: float,
    effective_stack_bb: float,
    num_buckets: int,
    positions: tuple = ("OOP", "IP"),
    raise_sizes: tuple = (2.5, 3.0, 2.2),
    max_raises: int = 4,
    iterations: int = None,
    equity_samples: int = None,
    equity_seed: int = DEFAULT_EQUITY_SEED,
) -> StrategyResult:
    """Solve a flop betting round over `num_buckets` hand-strength buckets
    instead of solve_flop's real combos — same betting tree/parameters,
    only hand granularity changes, trading exactness for a CFR solve
    running its tensor operations over B buckets instead of N combos.

    `hero_range`/`villain_range` are combined into one shared combo pool
    the same way solve_flop does, then that pool is bucketed via
    abstraction.build_hand_buckets, weighted by each combo's *combined*
    (hero + villain) weight — inert for build_hand_buckets itself (bucket
    membership only depends on the combo list, not weights), but
    load-bearing for build_bucket_equity_table, whose combo_weights
    argument weights the B x B aggregate toward whichever combos either
    range actually has real reach behind.

    A bucket built over that combined pool can end up containing combos
    that are "mostly hero's" and combos that are "mostly villain's" — so,
    unlike solve_flop's hero_reach/villain_reach (each a straight
    per-combo lookup), each side's own per-bucket reach vector is
    computed independently via abstraction.bucket_reach_vector, summing
    *that side's own* range_dict over each bucket's members. HandBucket's
    own aggregate `.weight` (built from the combined dict) is deliberately
    not reused here — it can't serve as either side's real reach.

    Returns a StrategyResult whose `hands` are abstraction.HandBucket
    instances, not combos.HandCombo — use expand_bucket_strategy to fan
    a bucket-level strategy dict back out to real combos.
    """
    hero_position, villain_position = positions
    combos = sorted(set(hero_range) | set(villain_range), key=str)
    bucket_weights = {c: hero_range.get(c, 0.0) + villain_range.get(c, 0.0) for c in combos}

    equity_kwargs = {"rng": random.Random(equity_seed)}
    if equity_samples is not None:
        equity_kwargs["samples"] = equity_samples
    bucketed_pool = build_hand_buckets(board, bucket_weights, num_buckets, **equity_kwargs)
    bucket_equity_table = np.nan_to_num(build_bucket_equity_table(bucketed_pool, bucket_weights), nan=0.5)

    hero_bucket_reach = bucket_reach_vector(bucketed_pool, hero_range)
    villain_bucket_reach = bucket_reach_vector(bucketed_pool, villain_range)

    config = StreetConfig(
        positions=positions,
        pot=pot,
        stack_bb=effective_stack_bb,
        raise_sizes=raise_sizes,
        max_raises=max_raises,
    )
    root = build_street_tree(config)

    actual_iterations = iterations if iterations is not None else DEFAULT_FLOP_ITERATIONS
    start = time.perf_counter()
    node_data = solve(
        root,
        bucketed_pool.buckets,
        bucket_equity_table,
        iterations=actual_iterations,
        positions=positions,
        initial_reach={hero_position: hero_bucket_reach, villain_position: villain_bucket_reach},
    )
    elapsed = time.perf_counter() - start

    return StrategyResult(
        config=config,
        root=root,
        hands=bucketed_pool.buckets,
        node_data=node_data,
        iterations=actual_iterations,
        elapsed_seconds=elapsed,
    )


def expand_bucket_strategy(bucket_strategy: dict, bucketed_pool: BucketedPool) -> dict:
    """Fans a bucket-level strategy dict (strategy_at/opening_range/
    strategy_for_position's output on a solve_flop_abstracted result,
    keyed by str(HandBucket)) out to a combo-level dict keyed by
    str(HandCombo) — every combo in a bucket inherits its bucket's
    converged strategy verbatim, since a real caller wants advice for
    their exact combo, not "bucket 5". Reuses strategy_at's own per-node
    lookup entirely unchanged (bucket_strategy is its output) — the only
    new logic is this bucket -> combo fan-out. Raises ValueError if a
    bucket's str() key is missing from bucket_strategy (a mismatched
    result/pool pairing).
    """
    expanded = {}
    for bucket in bucketed_pool.buckets:
        key = str(bucket)
        if key not in bucket_strategy:
            raise ValueError(f"bucket_strategy has no entry for {key!r} — mismatched BucketedPool?")
        for combo in bucket.members:
            expanded[str(combo)] = bucket_strategy[key]
    return expanded


# Flop->turn chaining (M12) walks a real exact-CFR tree ~47x wider at
# every showdown-eligible terminal (one branch per undealt card) than
# solve_flop's — see chance.py's module docstring. DEFAULT_FLOP_TURN_ITERATIONS
# is set from real measurement (see the M12 PR), not copied from
# DEFAULT_FLOP_ITERATIONS.
DEFAULT_FLOP_TURN_ITERATIONS = 200


def solve_flop_turn(
    board: tuple,
    hero_range: dict,
    villain_range: dict,
    pot: float,
    effective_stack_bb: float,
    positions: tuple = ("OOP", "IP"),
    raise_sizes: tuple = (2.5, 3.0, 2.2),
    max_raises: int = 4,
    iterations: int = None,
    equity_seed: int = DEFAULT_EQUITY_SEED,
) -> StrategyResult:
    """Solve a flop betting round that, whenever action is capped without
    a fold, chains into a real turn betting round (dealt via a real
    chance node) instead of solve_flop's "average every remaining runout
    immediately" shortcut — the river is still handled that way, one
    street further out, at each *turn* terminal. See chance.py's and
    cfr.py's module docstrings for the full chance-node design.

    Same parameters as `solve_flop`, minus `equity_samples` (board_equity
    tables built here are all turn-board tables — remaining_needed==1 —
    which are resolved exactly, not sampled, so there's nothing to tune).
    `raise_sizes`/`max_raises` apply to both the flop and turn streets —
    a deliberate M12 scope cut, not a separate turn-specific sizing menu.

    The returned StrategyResult's `chance_data` maps each showdown-
    eligible flop terminal actually reached during solving to its
    `chance.ChanceNode` — the only way to reach turn-street strategy,
    since `StrategyResult`'s own convenience walkers
    (`node_for_position`/`strategy_for_position`) only ever walk `root`'s
    own street.
    """
    hero_position, villain_position = positions
    combos = sorted(set(hero_range) | set(villain_range), key=str)
    hero_reach = np.array([hero_range.get(combo, 0.0) for combo in combos])
    villain_reach = np.array([villain_range.get(combo, 0.0) for combo in combos])

    config = StreetConfig(
        positions=positions,
        pot=pot,
        stack_bb=effective_stack_bb,
        raise_sizes=raise_sizes,
        max_raises=max_raises,
    )
    root = build_street_tree(config)

    equity_table = build_board_equity_table(board, combos, rng=random.Random(equity_seed))
    equity_table = np.nan_to_num(equity_table, nan=0.5)

    def chance_fn(terminal):
        return build_chance_node(
            terminal, board=board, combos=combos, positions=positions,
            effective_stack_bb=effective_stack_bb, raise_sizes=raise_sizes, max_raises=max_raises,
        )

    chance_data: dict = {}
    actual_iterations = iterations if iterations is not None else DEFAULT_FLOP_TURN_ITERATIONS
    start = time.perf_counter()
    node_data = solve(
        root,
        combos,
        equity_table,
        iterations=actual_iterations,
        positions=positions,
        initial_reach={hero_position: hero_reach, villain_position: villain_reach},
        chance_fn=chance_fn,
        chance_data=chance_data,
    )
    elapsed = time.perf_counter() - start

    return StrategyResult(
        config=config,
        root=root,
        hands=combos,
        node_data=node_data,
        iterations=actual_iterations,
        elapsed_seconds=elapsed,
        chance_data=chance_data,
    )


# Flop->turn->river chaining (M13) adds a *second* chance-node hop on top
# of solve_flop_turn's already-expensive first one — see solve_flop_to_
# river's own docstring and the M13 PR for the real measured numbers.
# Deliberately untuned against any live endpoint (none is planned — see
# the M13 PR's cost section), unlike DEFAULT_FLOP_TURN_ITERATIONS, which
# was at least in the same conversation as a (rejected) live-endpoint
# question. Kept equal to the test suite's own tiny fixture's iteration
# count (measured ~4.3s there) rather than DEFAULT_FLOP_TURN_ITERATIONS's
# 200 — measured directly: 100 iterations here cost ~55s on that same
# tiny fixture (chance-node construction cost doesn't scale linearly with
# iterations the way M12's own default assumed), which is unnecessarily
# slow for a value nothing is actually tuned against.
DEFAULT_FLOP_TO_RIVER_ITERATIONS = 20


def solve_flop_to_river(
    board: tuple,
    hero_range: dict,
    villain_range: dict,
    pot: float,
    effective_stack_bb: float,
    positions: tuple = ("OOP", "IP"),
    raise_sizes: tuple = (2.5, 3.0, 2.2),
    max_raises: int = 4,
    iterations: int = None,
    equity_seed: int = DEFAULT_EQUITY_SEED,
) -> StrategyResult:
    """Solve a flop betting round that chains all the way to a real river
    showdown — flop->turn via a real chance node (see solve_flop_turn),
    and now turn->river via a second one (see chance.build_chance_node's
    `chain_to_river`), instead of solve_flop_turn's own "average the
    river inside the turn branch's equity table" shortcut. This is the
    last chance-node hop possible starting from a 3-card flop board — a
    complete 5-card river board has no more cards left to deal.

    Identical parameters to `solve_flop_turn` — the only difference is
    the single `chain_to_river=True` passed to `build_chance_node`
    inside this function's own `chance_fn` closure; see chance.py's
    module docstring for how that one flag lets the *same*
    `build_chance_node` call recursively populate a second level of
    `ChanceBranch.chance_fn` (the river hop) instead of leaving every
    branch's `chance_fn` at `None` the way solve_flop_turn's call does.
    No `cfr.py` changes were needed for this — see `chance_data`'s
    resulting shape below.

    The returned StrategyResult's `chance_data` ends up flat but two
    levels deep: `chance_data[id(some_flop_terminal)]` still gives the
    turn-level ChanceNode exactly like solve_flop_turn (every chance
    dispatch, at any depth, memoizes into the same dict — see
    StrategyResult's own docstring) — and
    `chance_data[id(some_turn_terminal)]` (once that turn terminal has
    actually been reached during solving) gives the *river*-level
    ChanceNode one hop further.

    Not exposed via the API/frontend — see the M13 PR for the measured
    tiny-fixture cost and the reasoned (not separately re-measured)
    demo-scale cost estimate that ruled it out, same as
    solve_flop_turn's own finding one milestone earlier.
    """
    hero_position, villain_position = positions
    combos = sorted(set(hero_range) | set(villain_range), key=str)
    hero_reach = np.array([hero_range.get(combo, 0.0) for combo in combos])
    villain_reach = np.array([villain_range.get(combo, 0.0) for combo in combos])

    config = StreetConfig(
        positions=positions,
        pot=pot,
        stack_bb=effective_stack_bb,
        raise_sizes=raise_sizes,
        max_raises=max_raises,
    )
    root = build_street_tree(config)

    equity_table = build_board_equity_table(board, combos, rng=random.Random(equity_seed))
    equity_table = np.nan_to_num(equity_table, nan=0.5)

    def chance_fn(terminal):
        return build_chance_node(
            terminal, board=board, combos=combos, positions=positions,
            effective_stack_bb=effective_stack_bb, raise_sizes=raise_sizes, max_raises=max_raises,
            chain_to_river=True,
        )

    chance_data: dict = {}
    actual_iterations = iterations if iterations is not None else DEFAULT_FLOP_TO_RIVER_ITERATIONS
    start = time.perf_counter()
    node_data = solve(
        root,
        combos,
        equity_table,
        iterations=actual_iterations,
        positions=positions,
        initial_reach={hero_position: hero_reach, villain_position: villain_reach},
        chance_fn=chance_fn,
        chance_data=chance_data,
    )
    elapsed = time.perf_counter() - start

    return StrategyResult(
        config=config,
        root=root,
        hands=combos,
        node_data=node_data,
        iterations=actual_iterations,
        elapsed_seconds=elapsed,
        chance_data=chance_data,
    )


def format_opening_range_grid(result: StrategyResult) -> str:
    """A quick 13x13-ish text dump of the opening range, sorted by hand
    strength, for eyeballing sanity (used by the console script)."""
    opening = result.opening_range()
    non_fold_action_by_strength = []
    for hand in result.hands:
        freqs = opening[str(hand)]
        fold_freq = freqs.get("fold", 0.0)
        non_fold_action_by_strength.append((str(hand), 1.0 - fold_freq, freqs))

    lines = [
        f"{result.root.player_to_act} opening range @ {result.config.stack_bb:.0f}bb "
        f"({result.iterations} iterations, {result.elapsed_seconds:.2f}s):",
    ]
    for label, non_fold_freq, freqs in non_fold_action_by_strength:
        breakdown = ", ".join(f"{action}={freq:.2f}" for action, freq in freqs.items())
        lines.append(f"  {label:>4s}  non-fold={non_fold_freq:5.1%}   ({breakdown})")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    stack = float(sys.argv[1]) if len(sys.argv) > 1 else 100.0
    iters = int(sys.argv[2]) if len(sys.argv) > 2 else None
    result = solve_preflop(stack_bb=stack, iterations=iters)
    print(format_opening_range_grid(result))
