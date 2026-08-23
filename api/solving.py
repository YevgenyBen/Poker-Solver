"""Solve orchestration for the API layer (M62, audit recommendation #4).

The second and larger half of splitting `api/main.py`, which held
constants, caches, orchestrators, and routes in one 3,441-line module.
M61 carved out `config.py` and `caches.py`; this takes the ~1,700 lines
of orchestration, leaving `main.py` as the HTTP surface it should be.

    config  <-  caches  <-  solving  <-  main

Everything here answers "how do we produce an answer for this request",
never "what URL is it at" or "what shape does it serialize to". The
route functions in `main.py` do validation and response shaping and call
into these; nothing here imports FastAPI or knows about HTTP.

Constants are read as `config.X` at call time rather than imported by
value — see the note in `main.py` for why that indirection is
deliberate rather than incidental.
"""

import dataclasses
import threading

from starlette.concurrency import run_in_threadpool  # noqa: F401  (re-exported for callers)

from poker_solver.canonicalize import canonical_stack_depth, canonicalize_board
from poker_solver.cards import parse_cards
from poker_solver.combos import HandCombo, range_from_class_frequencies
from poker_solver.equity import MultiwayEquityCache
from poker_solver.game_tree import (
    CALL_OR_CHECK,
    DecisionNode,
    GameConfig,
    TerminalNode,
    postflop_action_order,
    resolve_action,
)
from poker_solver.library import query_strategy, query_strategy_from_path
from poker_solver.solver import (
    DEFAULT_FLOP_TO_RIVER_MULTIWAY_ITERATIONS,
    DEFAULT_FLOP_TURN_ITERATIONS,
    DEFAULT_ITERATIONS,
    StrategyResult,
    derive_ranges_from_path,
    ensure_mccfr_chance_branch,
    solve_flop,
    solve_flop_multiway,
    solve_flop_to_river,
    solve_flop_to_river_multiway,
    solve_flop_turn,
    solve_flop_turn_multiway,
    solve_preflop,
)
from poker_solver.starting_hands import StartingHand
from poker_solver.strategy_format import format_flop_response, format_solve_response

# Aliased to `cfg`: `config` is a very common LOCAL name in this
# codebase (`config = GameConfig(...)`, `config = StreetConfig(...)`),
# and a module-level `config` would be silently shadowed inside exactly
# those functions — it was, and surfaced as an UnboundLocalError.
from . import config as cfg
from .caches import (
    _flop_cache,
    _flop_multiway_cache,
    _flop_multiway_path_cache,
    _flop_query_library,
    _flop_to_river_cache,
    _flop_to_river_multiway_cache,
    _flop_turn_cache,
    _flop_turn_multiway_cache,
    _flop_node_cache,
    _multiway_cache,
    _multiway_equity_caches,
    _path_query_libraries,
    _preflop_raw_cache,
    _river_path_cache,
    _turn_multiway_path_cache,
    _turn_path_cache,
)


def _cache_key(stack_bb: float, iterations: int) -> tuple:
    return (round(stack_bb), iterations)


def _hero_cache_component(hero_combo, hero_in_range=None):
    """The hero part of a path-query cache key (M76).

    Every one of these caches keys on the action path, stack, board and
    iteration counts — and, before M76, on nothing about hero. That was
    wrong, because `_derive_path_situation` force-includes hero's own
    combo into every live position's derived range BEFORE the top-K cap.
    The SOLVE therefore depends on hero, while the KEY did not, so the
    first request for a spot fixed the pool and every later request for
    the same spot holding a different hand found its own combo missing
    and got no advice at all. Measured both directions: asking AsKd then
    9s9d gave advice then silence; reversing the order reversed which one
    was answered; clearing the cache between requests answered both.

    Keyed by hand CLASS, not concrete combo. The force-inclusion that
    makes the solve hero-dependent is class-shaped in every case that
    matters (a capped range is a set of classes expanded to combos), so
    two suit-isomorphic hero hands genuinely share a solve — 169 possible
    key values instead of 1,326, for the same correctness.

    The expensive preflop leg is cached separately (_preflop_raw_cache /
    _multiway_cache) and keyed without hero, so it is still shared across
    every hero hand; only the postflop solve is duplicated per class.

    **`hero_in_range` narrows that further (M77), and is the difference
    between a correct cache and a useless one.** Force-inclusion only
    changes the solved pool when hero's class was NOT already in the
    capped range. When it WAS — the common case, since the cap keeps the
    highest-frequency classes and most hands people hold are in them —
    the solve is genuinely hero-independent and every hero can share one
    cache entry. Returning None there restores that sharing.

    This matters because the first re-audit after M76 measured postflop
    latency roughly doubling (a heads-up turn 20.3s -> 44.5s, a 6-max
    flop 21.3s -> 45.2s): correctness had been bought by making every
    hero class miss the cache, including the majority that never needed
    their own solve. Pass `hero_in_range=True` and the entry is shared;
    pass False (or None, meaning "not known") and hero is keyed in.
    """
    if hero_combo is None:
        return None
    if hero_in_range:
        # Hero earned its place in the capped range on its own weight, so
        # no force-inclusion happened and the solve does not depend on
        # which hand was asked about.
        return None
    return str(_combo_to_class(hero_combo))


def _get_multiway_equity_cache(hands) -> MultiwayEquityCache:
    """The shared MultiwayEquityCache for `hands` (M67).

    Preflop equity depends only on the hands — not on stack depth, and
    not on table size beyond the opponent-tuple length MultiwayEquityCache
    already keys by. So every multiway solve over the same pool can and
    should share one, instead of each (stack, players) spot rebuilding
    identical simulations from scratch. See api/caches.py's own comment
    on _multiway_equity_caches for why this is keyed by the pool rather
    than being a single module-level instance.
    """
    key = (tuple(str(hand) for hand in hands), cfg.MULTIWAY_PREFLOP_SAMPLES)
    with _multiway_equity_caches.lock:
        cache = _multiway_equity_caches.entries.get(key)
        if cache is None:
            cache = MultiwayEquityCache(
                hands=list(hands), samples=cfg.MULTIWAY_PREFLOP_SAMPLES, seed=1
            )
            _multiway_equity_caches.entries[key] = cache
    return cache


def _get_or_solve_multiway(stack_bb: float, players: int) -> StrategyResult:
    """Solves (or returns the cached result of solving) the full
    `players`-max tree once for `stack_bb`, over
    cfg.MULTIWAY_PREFLOP_HANDS (all 169 classes as of M67) — every
    position's strategy is derived from this single cached
    StrategyResult, so switching `position` in the API/UI never triggers
    a re-solve.

    That single cached solve is why the full pool is affordable at all:
    it costs ~170s at 6-max / ~215s at 9-max, paid once per (stack,
    players), and _prewarm_common_depths already warms stack_bb=100 for
    every table size on startup. See cfg.MULTIWAY_PREFLOP_HANDS for the
    measurements and for why the previous 8-class pool was replaced."""
    key = (round(stack_bb), players)
    with _multiway_cache.lock:
        cached = _multiway_cache.entries.get(key)
    if cached is not None:
        return cached

    table = cfg.MULTIWAY_TABLE_CONFIGS[players]
    config = GameConfig(positions=table["positions"], stack_bb=stack_bb)
    equity_cache = _get_multiway_equity_cache(cfg.MULTIWAY_PREFLOP_HANDS)
    result = solve_preflop(
        config=config,
        hands=cfg.MULTIWAY_PREFLOP_HANDS,
        equity_cache=equity_cache,
        iterations=table["iterations"],
        seed=1,
        floor_regret=table.get("floor_regret"),
    )

    with _multiway_cache.lock:
        _multiway_cache.entries[key] = result
    return result


def _get_or_solve_flop(board_cards: tuple, pot: float, stack_bb: float, iterations: int) -> StrategyResult:
    """Solves (or returns the cached result of solving) DEMO_FLOP_HERO/
    VILLAIN_CLASSES' board-legal expansion for one (board, pot, stack_bb,
    iterations) request — cached the same way multiway solves are, so
    switching `position` in the API/UI never triggers a re-solve."""
    key = (board_cards, round(pot, 2), round(stack_bb), iterations)
    with _flop_cache.lock:
        cached = _flop_cache.entries.get(key)
    if cached is not None:
        return cached

    exclude = frozenset(board_cards)
    hero_range = range_from_class_frequencies(cfg.DEMO_FLOP_HERO_CLASSES, exclude=exclude)
    villain_range = range_from_class_frequencies(cfg.DEMO_FLOP_VILLAIN_CLASSES, exclude=exclude)
    if not hero_range or not villain_range:
        # Only possible with a contrived board (e.g. 3 cards of the same
        # rank blocking a pair class down to nothing) — a real error for
        # the caller, not a crash.
        raise ValueError(f"board {''.join(str(c) for c in board_cards)!r} blocks every demo-range combo")

    result = solve_flop(
        board=board_cards,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=pot,
        effective_stack_bb=stack_bb,
        iterations=iterations,
    )

    with _flop_cache.lock:
        _flop_cache.entries[key] = result
    return result


def _get_or_solve_flop_turn(board_cards: tuple, pot: float, stack_bb: float, iterations: int) -> StrategyResult:
    """Solves (or returns the cached result of solving) DEMO_CHAINED_FLOP_
    HERO/VILLAIN_CLASSES' board-legal expansion via solve_flop_turn — same
    shape as _get_or_solve_flop, own cache dict (see its module-level
    comment for why a shared one would be unsafe)."""
    key = (board_cards, round(pot, 2), round(stack_bb), iterations)
    with _flop_turn_cache.lock:
        cached = _flop_turn_cache.entries.get(key)
    if cached is not None:
        return cached

    exclude = frozenset(board_cards)
    hero_range = range_from_class_frequencies(cfg.DEMO_CHAINED_FLOP_HERO_CLASSES, exclude=exclude)
    villain_range = range_from_class_frequencies(cfg.DEMO_CHAINED_FLOP_VILLAIN_CLASSES, exclude=exclude)
    if not hero_range or not villain_range:
        raise ValueError(f"board {''.join(str(c) for c in board_cards)!r} blocks every demo-range combo")

    result = solve_flop_turn(
        board=board_cards,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=pot,
        effective_stack_bb=stack_bb,
        raise_sizes=cfg.FLOP_TURN_RAISE_SIZES,
        max_raises=cfg.FLOP_TURN_MAX_RAISES,
        iterations=iterations,
    )

    with _flop_turn_cache.lock:
        _flop_turn_cache.entries[key] = result
    return result


def _get_or_solve_flop_to_river(board_cards: tuple, pot: float, stack_bb: float, iterations: int) -> StrategyResult:
    """Same idea as _get_or_solve_flop_turn, via solve_flop_to_river and
    its own (much tighter — see cfg.MAX_FLOP_TO_RIVER_ITERATIONS) cache."""
    key = (board_cards, round(pot, 2), round(stack_bb), iterations)
    with _flop_to_river_cache.lock:
        cached = _flop_to_river_cache.entries.get(key)
    if cached is not None:
        return cached

    exclude = frozenset(board_cards)
    hero_range = range_from_class_frequencies(cfg.DEMO_CHAINED_FLOP_HERO_CLASSES, exclude=exclude)
    villain_range = range_from_class_frequencies(cfg.DEMO_CHAINED_FLOP_VILLAIN_CLASSES, exclude=exclude)
    if not hero_range or not villain_range:
        raise ValueError(f"board {''.join(str(c) for c in board_cards)!r} blocks every demo-range combo")

    result = solve_flop_to_river(
        board=board_cards,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=pot,
        effective_stack_bb=stack_bb,
        raise_sizes=cfg.FLOP_TO_RIVER_RAISE_SIZES,
        max_raises=cfg.FLOP_TO_RIVER_MAX_RAISES,
        iterations=iterations,
    )

    with _flop_to_river_cache.lock:
        _flop_to_river_cache.entries[key] = result
    return result


def _get_or_solve_flop_multiway(board_cards: tuple, pot: float, stack_bb: float, iterations: int) -> StrategyResult:
    """Solves (or returns the cached result of solving) DEMO_MULTIWAY_
    FLOP_CLASSES' board-legal expansion via solve_flop_multiway (M35) —
    same shape as _get_or_solve_flop/_get_or_solve_flop_turn, own cache
    dict (see the module-level comment by _flop_multiway_cache for why a
    shared one would be unsafe). Unlike those two-position helpers,
    cfg.DEMO_MULTIWAY_FLOP_CLASSES is itself a {position: {StartingHand:
    weight}} dict (one entry per cfg.DEMO_MULTIWAY_FLOP_POSITIONS), not two
    separate hero_/villain_range parameters — expanded per position here
    via the same range_from_class_frequencies call the two-position
    helpers already use, just looped."""
    key = (board_cards, round(pot, 2), round(stack_bb), iterations)
    with _flop_multiway_cache.lock:
        cached = _flop_multiway_cache.entries.get(key)
    if cached is not None:
        return cached

    exclude = frozenset(board_cards)
    position_ranges = {
        position: range_from_class_frequencies(classes, exclude=exclude)
        for position, classes in cfg.DEMO_MULTIWAY_FLOP_CLASSES.items()
    }
    if any(not r for r in position_ranges.values()):
        raise ValueError(f"board {''.join(str(c) for c in board_cards)!r} blocks every demo-range combo for at least one position")

    result = solve_flop_multiway(
        board=board_cards,
        position_ranges=position_ranges,
        pot=pot,
        effective_stack_bb=stack_bb,
        positions=cfg.DEMO_MULTIWAY_FLOP_POSITIONS,
        raise_sizes=cfg.MULTIWAY_FLOP_RAISE_SIZES,
        max_raises=cfg.MULTIWAY_FLOP_MAX_RAISES,
        iterations=iterations,
    )

    with _flop_multiway_cache.lock:
        _flop_multiway_cache.entries[key] = result
    return result


def _get_or_solve_flop_turn_multiway(board_cards: tuple, pot: float, stack_bb: float, iterations: int) -> StrategyResult:
    """Same idea as _get_or_solve_flop_multiway, via solve_flop_turn_
    multiway (M36) and its own (more conservative — see MAX_FLOP_TURN_
    MULTIWAY_ITERATIONS) cache."""
    key = (board_cards, round(pot, 2), round(stack_bb), iterations)
    with _flop_turn_multiway_cache.lock:
        cached = _flop_turn_multiway_cache.entries.get(key)
    if cached is not None:
        return cached

    exclude = frozenset(board_cards)
    position_ranges = {
        position: range_from_class_frequencies(classes, exclude=exclude)
        for position, classes in cfg.DEMO_MULTIWAY_FLOP_CLASSES.items()
    }
    if any(not r for r in position_ranges.values()):
        raise ValueError(f"board {''.join(str(c) for c in board_cards)!r} blocks every demo-range combo for at least one position")

    result = solve_flop_turn_multiway(
        board=board_cards,
        position_ranges=position_ranges,
        pot=pot,
        effective_stack_bb=stack_bb,
        positions=cfg.DEMO_MULTIWAY_FLOP_POSITIONS,
        raise_sizes=cfg.MULTIWAY_FLOP_RAISE_SIZES,
        max_raises=cfg.MULTIWAY_FLOP_MAX_RAISES,
        iterations=iterations,
    )

    with _flop_turn_multiway_cache.lock:
        _flop_turn_multiway_cache.entries[key] = result
    return result


def _get_or_solve_flop_to_river_multiway(board_cards: tuple, pot: float, stack_bb: float, iterations: int) -> StrategyResult:
    """Same idea as _get_or_solve_flop_turn_multiway, via solve_flop_to_
    river_multiway (M39) and its own cache — see MAX_FLOP_TO_RIVER_
    MULTIWAY_ITERATIONS' own comment for why this endpoint's cap matches
    solve_flop_turn_multiway's rather than solve_flop_to_river's tiny
    2-position ones."""
    key = (board_cards, round(pot, 2), round(stack_bb), iterations)
    with _flop_to_river_multiway_cache.lock:
        cached = _flop_to_river_multiway_cache.entries.get(key)
    if cached is not None:
        return cached

    exclude = frozenset(board_cards)
    position_ranges = {
        position: range_from_class_frequencies(classes, exclude=exclude)
        for position, classes in cfg.DEMO_MULTIWAY_FLOP_CLASSES.items()
    }
    if any(not r for r in position_ranges.values()):
        raise ValueError(f"board {''.join(str(c) for c in board_cards)!r} blocks every demo-range combo for at least one position")

    result = solve_flop_to_river_multiway(
        board=board_cards,
        position_ranges=position_ranges,
        pot=pot,
        effective_stack_bb=stack_bb,
        positions=cfg.DEMO_MULTIWAY_FLOP_POSITIONS,
        raise_sizes=cfg.MULTIWAY_FLOP_RAISE_SIZES,
        max_raises=cfg.MULTIWAY_FLOP_MAX_RAISES,
        iterations=iterations,
    )

    with _flop_to_river_multiway_cache.lock:
        _flop_to_river_multiway_cache.entries[key] = result
    return result


def _query_flop(board_cards: tuple, stack_bb: float) -> dict:
    """Canonicalize-then-lookup-then-fallback-to-solve (M21's query_
    strategy) over FLOP_QUERY_HERO_/VILLAIN_CLASSES' board-legal
    expansion. Named _query_flop, not _get_or_solve_flop_X — the
    check-or-solve duality those helpers hand-roll around their own
    cache dict already lives inside query_strategy itself here.

    Holds _flop_query_lock for query_strategy's ENTIRE call, not just
    around a dict read/write the way every _get_or_solve_X helper above
    does — a deliberate, stricter departure. query_strategy is an
    atomic check-then-maybe-solve-then-insert primitive with no
    concurrency control of its own (see its own docstring's "Known,
    deliberate limitations"); unlike the hand-rolled helpers above, it
    can't be decomposed into a separate check step and solve step
    without reimplementing its internals here. This closes that
    documented gap for this one live entry point: no concurrent-miss
    double-solve, period. Real cost, not hidden: two unrelated
    concurrent misses (different, non-isomorphic boards) now queue
    behind each other rather than solving in parallel — the mirror-
    image tradeoff of every other endpoint's own looser locking (no
    serialization at all, so a concurrent identical-key miss there
    really can double-solve). _flop_query_lock is its own independent
    threading.Lock() object, and every request is dispatched via
    run_in_threadpool onto its own worker thread regardless of
    endpoint, so this only serializes requests to this one endpoint.
    """
    exclude = frozenset(board_cards)
    hero_range = range_from_class_frequencies(cfg.FLOP_QUERY_HERO_CLASSES, exclude=exclude)
    villain_range = range_from_class_frequencies(cfg.FLOP_QUERY_VILLAIN_CLASSES, exclude=exclude)
    if not hero_range or not villain_range:
        # query_strategy/build_library don't guard this themselves (see
        # poker_solver/library.py) — mirrors _get_or_solve_flop's own
        # guard, but is a genuine second computation, not a reused one:
        # build_library takes raw classes, not pre-computed ranges, so
        # it re-derives these internally regardless. Still cheap: no
        # solve, no equity table, just combo enumeration.
        raise ValueError(f"board {''.join(str(c) for c in board_cards)!r} blocks every demo-range combo")

    with _flop_query_library.lock:
        result = query_strategy(
            _flop_query_library.entries,
            board=board_cards,
            hero_classes=cfg.FLOP_QUERY_HERO_CLASSES,
            villain_classes=cfg.FLOP_QUERY_VILLAIN_CLASSES,
            pot=cfg.FLOP_QUERY_POT,
            effective_stack_bb=stack_bb,
            iterations=cfg.FLOP_QUERY_ITERATIONS,
        )

    # Pure functions of (board_cards, stack_bb) alone, safe to compute
    # outside the lock — guaranteed to agree with whatever key query_
    # strategy just looked up or inserted under (see its own docstring's
    # determinism argument for why).
    canonical_board, _ = canonicalize_board(board_cards)
    canonical_stack_bb = canonical_stack_depth(stack_bb)

    return {
        "board": "".join(str(c) for c in board_cards),
        "canonical_board": "".join(str(c) for c in canonical_board),
        "pot": cfg.FLOP_QUERY_POT,
        "stack_bb": stack_bb,
        "canonical_stack_bb": canonical_stack_bb,
        "hit": result.hit,
        "elapsed_seconds": result.elapsed_seconds,
        "strategy": result.strategy,
        "position": "OOP",
        "positions": ["OOP", "IP"],
    }


def _get_or_solve_preflop_raw(stack_bb: float, iterations: int, players: int = 2) -> StrategyResult:
    """Solves (or returns the cached result of solving) a real preflop
    spot, caching the RAW StrategyResult — unlike _get_or_solve above,
    which formats the result and discards it. M24 needs the real
    tree/node_data to walk with derive_ranges_from_path, the same
    reason _get_or_solve_multiway already caches its own raw result
    rather than a formatted one. Its own cache dict, not _cache — a
    formatted dict and a raw StrategyResult are different
    representations, never mixed in one dict (mirrors every other
    cache-dict boundary in this file).

    `players` (M29): 2 (the original, still-default behavior) solves
    heads-up with the CALLER's own `iterations` — unchanged. `players`
    in cfg.MULTIWAY_TABLE_CONFIGS instead delegates outright to
    _get_or_solve_multiway, ignoring `iterations` entirely — the same
    "fixed menu" discipline M9's own cfg.MULTIWAY_TABLE_CONFIGS budgets
    exist to enforce (a client-controllable iteration count at
    multiway scale reopens exactly the cost/safety question those
    budgets were tuned to close), and it reuses THE SAME cached
    StrategyResult `GET /solve/{stack_bb}?players=N` already solves and
    caches — a user who's already loaded that table size's range chart
    triggers no redundant second solve when they open the wizard next.
    """
    if players != 2:
        if players not in cfg.MULTIWAY_TABLE_CONFIGS:
            valid = ", ".join(str(p) for p in [2, *cfg.MULTIWAY_TABLE_CONFIGS])
            raise ValueError(f"players must be one of {valid}")
        return _get_or_solve_multiway(stack_bb, players)

    key = _cache_key(stack_bb, iterations)
    with _preflop_raw_cache.lock:
        cached = _preflop_raw_cache.entries.get(key)
    if cached is not None:
        return cached

    result = solve_preflop(stack_bb=stack_bb, iterations=iterations)

    with _preflop_raw_cache.lock:
        _preflop_raw_cache.entries[key] = result
    return result


def _resolve_action_path(root: DecisionNode, action_kinds: list) -> tuple:
    """Turns a client-supplied list of bare action *kind* strings (e.g.
    ["raise", "call_or_check"]) into the real Action objects derive_
    ranges_from_path needs, walking the tree one step at a time via
    game_tree.resolve_action. Raises ValueError prefixed with the
    (0-indexed) step number that failed — friendlier than a bare
    tree-level error for an untrusted caller who can't see the tree.

    Returns (actions, node) — the resolved Action list *and* the node
    the walk actually ends at. M24's original version returned only the
    action list (all it needed for derive_ranges_from_path); M25's
    _preflop_walk needs the node itself, to inspect what's legal *from
    here* without also requiring a resolved next step.

    Explicitly checks isinstance(node, DecisionNode) before resolving
    each step — a TerminalNode has no legal_actions at all (calling
    resolve_action on one would raise a raw AttributeError, not a clean
    ValueError), so an action_path that runs past a real terminal needs
    to be caught here, one step before derive_ranges_from_path's own
    "path continues past a TerminalNode" check would otherwise catch it
    on the already-fully-built Action list.
    """
    actions = []
    node = root
    for step, kind in enumerate(action_kinds):
        if not isinstance(node, DecisionNode):
            raise ValueError(f"step {step}: the hand is already over — no more actions are legal")
        try:
            action = resolve_action(node, kind)
        except ValueError as exc:
            raise ValueError(f"step {step}: {exc}") from exc
        actions.append(action)
        node = node.children[action]
    return actions, node


def _preflop_walk(stack_bb: float, action_kinds: list, iterations: int, players: int = 2) -> dict:
    """Orchestrates POST /preflop_walk: a real (cached, raw) preflop
    solve -> resolve the client's bare action kinds into real Actions,
    walking to the resulting node -> report what's legal from there.

    No range derivation, no board, no query_strategy — this is a pure
    tree-state query, so none of _query_flop_from_path's range-capping
    or partitioned-library machinery applies here.

    `players` (M29): defaults to 2 (heads-up, unchanged); any other
    supported table size walks that size's own real tree instead — see
    _get_or_solve_preflop_raw's own docstring for what that changes
    (a fixed iteration budget, not the caller's `iterations`).
    """
    preflop_result = _get_or_solve_preflop_raw(stack_bb, iterations, players=players)
    _actions, node = _resolve_action_path(preflop_result.root, action_kinds)
    live_positions = [p for p in preflop_result.config.positions if p not in node.folded]

    if isinstance(node, TerminalNode):
        return {
            "stack_bb": stack_bb,
            "action_path": list(action_kinds),
            "is_terminal": True,
            "player_to_act": None,
            "live_positions": live_positions,
            "positions": list(preflop_result.config.positions),
            "pot": node.pot,
            "legal_actions": [],
        }

    # Safe over ALL positions (folded included), not just live ones:
    # FOLD is only ever offered when to_call > 0 at that instant (see
    # game_tree._build), so the position actually holding the current
    # max can never fold; every other action only ever raises the
    # acting position's invested to >= the pre-action max. So the true
    # max across live positions is monotonically non-decreasing along
    # any path, and a folded position's frozen invested can never
    # exceed a later node's true max — max(node.invested.values()) over
    # every position, folded or not, always equals the live max.
    current_bet = max(node.invested.values())
    to_call = current_bet - node.invested[node.player_to_act]

    legal_actions = []
    for action in node.legal_actions:
        option = {"kind": action.kind, "size": None, "to_call": None}
        if action.kind == CALL_OR_CHECK:
            option["to_call"] = to_call
        elif action.size is not None:
            option["size"] = action.size
        legal_actions.append(option)

    return {
        "stack_bb": stack_bb,
        "action_path": list(action_kinds),
        "is_terminal": False,
        "player_to_act": node.player_to_act,
        "live_positions": live_positions,
        "positions": list(preflop_result.config.positions),
        "pot": node.pot,
        "legal_actions": legal_actions,
    }


def _cap_range(range_dict: dict, max_classes: int) -> dict:
    """Top cfg.MAX_PATH_QUERY_CLASSES_PER_SIDE classes by frequency, not
    alphabetical/random — a solved strategy has already ranked classes
    by relevance (see the module docstring's Finding 1: an uncapped
    real path left the entire 169-class pool nonzero, a ~1,176-combo
    union that would cost hours per request)."""
    if len(range_dict) <= max_classes:
        return range_dict
    top_items = sorted(range_dict.items(), key=lambda item: item[1], reverse=True)[:max_classes]
    return dict(top_items)


def _cap_range_to_combos(class_frequencies: dict, max_combos: int, exclude: frozenset) -> dict:
    """M46's own combo-level analog of _cap_range — expands a {Starting
    Hand: weight} dict to real board-legal combos first (via combos.
    range_from_class_frequencies), THEN caps to the top `max_combos`
    combos by weight, rather than capping classes before expansion. See
    cfg.RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE's own comment for why: a
    class-level cap is too coarse a lever for solve_flop_to_river's own
    cost curve (a single class can expand to up to 12 combos)."""
    expanded = range_from_class_frequencies(class_frequencies, exclude=exclude)
    if len(expanded) <= max_combos:
        return expanded
    top_items = sorted(expanded.items(), key=lambda item: item[1], reverse=True)[:max_combos]
    return dict(top_items)


def _range_confidence(path_scenario, position_ranges: dict) -> dict:
    """Per-position summary of `PathScenario.trained` (M29), restricted
    to the classes that ACTUALLY survived capping (M52).

    Deliberately computed over the surviving classes, not the full
    derived range: a caller's advice is only ever built from what got
    solved, so confidence over the 160-odd classes the cap discarded
    would be noise that dilutes the number that matters.

    Summarized per position rather than returned per hand — the shape
    M29/M42/M44 each deferred deciding. A full per-hand map for every
    live position is mostly noise for a caller asking "can I trust this
    advice"; counts plus a boolean answer that directly, and hero's own
    per-hand flag (see `hero_range_trained`) covers the one hand a
    caller actually holds.

    Why it matters, measured not assumed: M29 found a real 6-max path
    whose derived range came back *exactly* uniform — confident-looking,
    fabricated, and silently indistinguishable from a converged one.
    """
    confidence = {}
    for position, combo_dict in position_ranges.items():
        trained_map = path_scenario.trained.get(position, {})
        classes = {_combo_to_class(combo) for combo in combo_dict}
        # A class absent from `trained` never had solving applied to it
        # along this path (e.g. a force-included hero class) — treated as
        # untrained, the conservative reading, never silently as True.
        trained_classes = sum(1 for cls in classes if trained_map.get(cls, False))
        confidence[position] = {
            "trained_classes": trained_classes,
            "total_classes": len(classes),
            "fully_trained": trained_classes == len(classes),
        }
    return confidence


def _combo_to_class(combo) -> StartingHand:
    """The StartingHand class a concrete HandCombo belongs to (M51).

    Safe because HandCombo.__post_init__ (M10) normalizes card_a to the
    higher (value, suit) pair, so card_a.rank is always the high rank.
    No pair special-case is needed: two distinct cards of the same rank
    must differ in suit, so `suited` is already False for every pair.
    """
    return StartingHand(
        combo.card_a.rank,
        combo.card_b.rank,
        suited=combo.card_a.suit == combo.card_b.suit,
    )


@dataclasses.dataclass(frozen=True)
class _PathSituation:
    """Everything the five path-derived endpoints' shared front half
    produces (M50) — the real, solved-and-validated situation a client's
    action path describes, before any street-specific solving happens.

    `capped_scenario` is only populated for a CLASS-level cap (the
    canonical-library path in _query_flop_from_path needs a real
    PathScenario whose `ranges` are still StartingHand-keyed, per
    library.query_strategy_from_path's own documented class-dicts-only
    contract); a combo-level cap (M46's river endpoint) has no
    meaningful class-level equivalent and leaves this None.
    """

    preflop_result: StrategyResult
    path_scenario: object  # solver.PathScenario
    postflop_positions: tuple
    effective_stack_bb: float
    position_ranges: dict  # position -> {HandCombo: weight}
    capped_scenario: object | None
    # M51: True when `hero_combo` was supplied AND survived the cap on
    # its own derived weight; False when it had to be force-included.
    # None when no hero_combo was supplied at all.
    hero_in_range: bool | None = None
    # M52: per-position summary of PathScenario.trained, restricted to
    # the classes that actually survived capping — position -> {"trained
    # _classes", "total_classes", "fully_trained"}. See _range_confidence.
    range_confidence: dict | None = None
    # M52: whether hero's OWN class was trained along the derivation.
    hero_range_trained: bool | None = None


def _derive_path_situation(
    *,
    action_kinds: list,
    stack_bb: float,
    board_cards: tuple,
    iterations: int,
    players: int,
    multiway: bool,
    sibling_endpoint: str,
    max_classes_per_position: int | None = None,
    max_combos_per_position: int | None = None,
    path_field_name: str = "action_path",
    hero_combo=None,
) -> _PathSituation:
    """The shared front half of every path-derived endpoint (M50).

    Extracted from five near-identical orchestrators (_query_flop_from_
    path, _query_flop_multiway_from_path, _query_turn_from_path,
    _query_turn_multiway_from_path, _query_river_from_path) that each
    hand-rolled the same pipeline: a real (cached, raw) preflop solve ->
    resolve the client's bare action kinds into real Actions ->
    derive_ranges_from_path (M16) -> require a real terminal with the
    right live-position count -> cap every position's derived range ->
    expand to board-legal combos -> postflop_action_order (M29) ->
    derive the shared effective stack. Only the SOLVE stage and the
    response shape genuinely differ between those five; everything above
    was duplicated, which is why surfacing path_scenario.trained (a gap
    named and deferred in M29/M42/M44) kept needing a five-place change.

    Deliberately parameterized, not unified away, because these are real
    per-endpoint differences, not incidental drift:
      * `multiway` — exactly 2 live positions (the exact 2-position
        solvers) vs. 3+ (the MCCFR multiway solvers). Each rejects the
        other's case with a message naming `sibling_endpoint`.
      * class-level vs. combo-level capping — see _cap_range_to_combos
        and cfg.RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE for the measured reason
        the river endpoint needs the finer lever.
      * `path_field_name` — the flop endpoints call their own field
        `action_path`, the deeper ones `preflop_action_path`; error text
        names whichever the client actually sent.

    `hero_combo` (M51, None for every pre-M51 caller) is force-included
    in EVERY live position's capped range — deliberately not just the
    acting position's, since which seat hero occupies isn't knowable
    here for the deeper streets (the acting position isn't determined
    until after solving and walking chance_data). The real cost of that
    choice is honest and small: at most one extra combo per position.
    Without it, a hand outside the top-K would be silently absent from
    the very solve meant to advise it.
    """
    if (max_classes_per_position is None) == (max_combos_per_position is None):
        raise RuntimeError("exactly one of max_classes_per_position/max_combos_per_position must be set")

    preflop_result = _get_or_solve_preflop_raw(stack_bb, iterations, players=players)
    actions, _node = _resolve_action_path(preflop_result.root, action_kinds)
    path_scenario = derive_ranges_from_path(preflop_result, actions)

    # Known, deliberate gap (M29/M42/M44): path_scenario.trained — whether
    # each derived-range hand was genuinely backed by real solving along
    # the path, rather than the untrained uniform default — still isn't
    # surfaced in any endpoint's response. It now has exactly ONE place
    # that would need to change to fix it, which was much of the point of
    # this extraction; the response-shape decision it needs is still its
    # own separate work.
    if not isinstance(path_scenario.node, TerminalNode):
        raise ValueError(f"{path_field_name} does not reach a terminal — action isn't capped yet")

    live_count = len(path_scenario.live_positions)
    if multiway and live_count < 3:
        raise ValueError(
            f"{path_field_name} leaves only {live_count} live position(s) — "
            f"use {sibling_endpoint} for a 2-survivor situation"
        )
    if not multiway and live_count != 2:
        # Previously this case reached postflop_action_order's own 2-tuple
        # unpack and surfaced as a bare "too many values to unpack"
        # ValueError (still a 422, but an unhelpful one) in the two flop
        # endpoints; the deeper ones already checked explicitly. Unified
        # here so every endpoint gives the same real explanation.
        raise ValueError(
            f"{path_field_name} leaves {live_count} live positions, not 2 — "
            f"use {sibling_endpoint} for a 3+-survivor situation"
        )

    postflop_positions = postflop_action_order(preflop_result.config.positions, path_scenario.live_positions)
    effective_stack_bb = path_scenario.stacks[postflop_positions[0]]
    if any(path_scenario.stacks[p] != effective_stack_bb for p in postflop_positions):
        raise RuntimeError(
            "derive_ranges_from_path's own TerminalNode guarantee (equal remaining stacks "
            "across every live position) did not hold — this should be unreachable"
        )

    exclude = frozenset(board_cards)
    capped_scenario = None
    if max_classes_per_position is not None:
        capped_ranges = {
            position: _cap_range(range_dict, max_classes_per_position)
            for position, range_dict in path_scenario.ranges.items()
        }
        capped_scenario = dataclasses.replace(path_scenario, ranges=capped_ranges)
        position_ranges = {
            position: range_from_class_frequencies(range_dict, exclude=exclude)
            for position, range_dict in capped_ranges.items()
        }
    else:
        position_ranges = {
            position: _cap_range_to_combos(range_dict, max_combos_per_position, exclude)
            for position, range_dict in path_scenario.ranges.items()
        }

    for position, combo_dict in position_ranges.items():
        if not combo_dict:
            raise ValueError(
                f"board {''.join(str(c) for c in board_cards)!r} blocks every combo in "
                f"{position}'s derived (capped) range"
            )

    hero_in_range = None
    if hero_combo is not None:
        if hero_combo.blocks(frozenset(board_cards)):
            raise ValueError(f"hero_cards {hero_combo} shares a card with the board — impossible to hold")
        # "In range" means hero's own combo survived the cap on its own
        # derived weight, in EVERY live position — not "it's present
        # after we added it". Computed before any force-inclusion below.
        hero_in_range = all(hero_combo in combo_dict for combo_dict in position_ranges.values())
        for combo_dict in position_ranges.values():
            if hero_combo not in combo_dict:
                # Weight it at the range's own minimum rather than an
                # invented constant: present enough to be solved for,
                # never dominating a range it didn't earn a place in.
                combo_dict[hero_combo] = min(combo_dict.values())
        if capped_scenario is not None:
            # The canonical-library path (_query_flop_from_path) solves
            # from capped_scenario's CLASS-level ranges, not from
            # position_ranges — so hero has to be force-included there
            # too, or the force-inclusion above would silently have no
            # effect on exactly that one endpoint. Class-level, not
            # combo-level, because library.query_strategy_from_path's own
            # contract is class-dicts-only (M20's crux design finding:
            # a suit-asymmetric combo dict breaks canonical reuse).
            hero_class = _combo_to_class(hero_combo)
            hero_ranges = {
                position: (
                    range_dict
                    if hero_class in range_dict
                    else {**range_dict, hero_class: min(range_dict.values())}
                )
                for position, range_dict in capped_scenario.ranges.items()
            }
            capped_scenario = dataclasses.replace(capped_scenario, ranges=hero_ranges)

    hero_range_trained = None
    if hero_combo is not None:
        hero_class = _combo_to_class(hero_combo)
        # Trained only if EVERY live position's derivation had real
        # solving for hero's class — the same all-positions reading
        # hero_in_range uses, and conservative for a missing entry.
        hero_range_trained = all(
            path_scenario.trained.get(position, {}).get(hero_class, False)
            for position in position_ranges
        )

    return _PathSituation(
        preflop_result=preflop_result,
        path_scenario=path_scenario,
        postflop_positions=postflop_positions,
        effective_stack_bb=effective_stack_bb,
        position_ranges=position_ranges,
        capped_scenario=capped_scenario,
        hero_in_range=hero_in_range,
        range_confidence=_range_confidence(path_scenario, position_ranges),
        hero_range_trained=hero_range_trained,
    )


def _query_flop_from_path(
    action_kinds: list, stack_bb: float, board_cards: tuple, iterations: int, players: int = 2,
    hero_combo=None,
) -> dict:
    """Orchestrates POST /solve_flop_from_path end to end: a real
    (cached, raw) preflop solve -> resolve the client's bare action
    kinds into real Actions -> derive_ranges_from_path (M16) -> cap
    both sides to cfg.MAX_PATH_QUERY_CLASSES_PER_SIDE (Finding 1) -> a
    private, per-(action_path, stack_bb, iterations, players) library
    (Finding 2, not the shared _flop_query_library _query_flop above
    uses) -> query_strategy_from_path (M23).

    `players` (M29): part of the partition key, not just a solve
    parameter — two DIFFERENT origin table sizes can legitimately share
    the exact same literal action-kind path (e.g. ["raise",
    "call_or_check"] is valid at both heads-up and 6-max), so omitting
    it from the key would let one silently serve the other's cached
    answer. cfg.MULTIWAY_PREFLOP_HANDS' own 8-class pool is already far
    smaller than cfg.MAX_PATH_QUERY_CLASSES_PER_SIDE would even cap to, so
    Finding 1's own uncapped-169-class-pool cost blowup doesn't recur
    here — multiway's curated pool was already the safe side of that
    finding before this milestone existed.

    Holds _path_query_lock for the entire query_strategy_from_path
    call, mirroring _query_flop's own stricter-than-the-hand-rolled-
    helpers locking discipline, for the same reason (query_strategy is
    an atomic primitive with no concurrency control of its own). One
    single lock guards every partition, not one lock per partition —
    a deliberate simplicity choice: this endpoint is already expensive
    per miss (~21s, see the module docstring) and low-traffic by
    design (a demo, not a high-throughput service), so the extra
    complexity of per-partition locking isn't earning its keep yet.
    """
    situation = _derive_path_situation(
        action_kinds=action_kinds,
        stack_bb=stack_bb,
        board_cards=board_cards,
        iterations=iterations,
        players=players,
        multiway=False,
        sibling_endpoint="/solve_flop_multiway_from_path",
        max_classes_per_position=cfg.MAX_PATH_QUERY_CLASSES_PER_SIDE,
        hero_combo=hero_combo,
    )
    oop_position, ip_position = situation.postflop_positions
    effective_stack_bb = situation.effective_stack_bb

    partition_key = (tuple(action_kinds), round(stack_bb), iterations, players,
                     _hero_cache_component(hero_combo, situation.hero_in_range))
    with _path_query_libraries.lock:
        library = _path_query_libraries.entries.setdefault(partition_key, {})
        result = query_strategy_from_path(
            library,
            situation.preflop_result,
            situation.capped_scenario,
            board_cards,
            iterations=cfg.PATH_QUERY_ITERATIONS,
        )

    path_scenario = situation.path_scenario
    canonical_board, _ = canonicalize_board(board_cards)
    canonical_stack_bb = canonical_stack_depth(effective_stack_bb)

    return {
        "board": "".join(str(c) for c in board_cards),
        "canonical_board": "".join(str(c) for c in canonical_board),
        "action_path": list(action_kinds),
        "stack_bb": stack_bb,
        "effective_stack_bb": effective_stack_bb,
        "canonical_stack_bb": canonical_stack_bb,
        "pot": path_scenario.pot,
        "hit": result.hit,
        "elapsed_seconds": result.elapsed_seconds,
        "strategy": result.strategy,
        # M76: real per-combo confidence from the library, replacing the
        # hardcoded null this cell used to report.
        "trained": result.trained,
        "position": oop_position,
        "positions": [oop_position, ip_position],
        "players": players,
        "hero_in_range": situation.hero_in_range,
        "range_confidence": situation.range_confidence,
        "hero_range_trained": situation.hero_range_trained,
    }


def _query_flop_multiway_from_path(
    action_kinds: list, stack_bb: float, board_cards: tuple, iterations: int, flop_iterations: int,
    players: int = 3, hero_combo=None, flop_action_kinds: list | None = None,
) -> dict:
    """Orchestrates POST /solve_flop_multiway_from_path end to end: a
    real (cached, raw) preflop solve -> resolve the client's bare action
    kinds -> derive_ranges_from_path (M16, already N-position-general)
    -> require a genuine 3+-live-position terminal (a 2-survivor path
    stays /solve_flop_from_path's own job) -> cap every position's range
    to cfg.MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_POSITION -> postflop_action_
    order (M29, already N-general per its own docstring) for the correct
    real acting order -> solve_flop_multiway (M35) directly, behind a
    plain per-(action_path, players, stack_bb, board, iterations,
    flop_iterations) cache — not query_strategy_from_path, which is
    2-position machinery all the way down (query_strategy -> solve_flop
    -> build_board_equity_table, none of which accept a 3+-position
    range dict).

    `players` (M42, following M29's own precedent): part of the cache
    key, for the identical collision reason /solve_flop_from_path's
    partition key and /solve_turn_from_path's _turn_path_cache key both
    already include it — two different origin table sizes can share the
    same literal action-kind path.
    """
    situation = _derive_path_situation(
        action_kinds=action_kinds,
        stack_bb=stack_bb,
        board_cards=board_cards,
        iterations=iterations,
        players=players,
        multiway=True,
        sibling_endpoint="/solve_flop_from_path",
        max_classes_per_position=cfg.MAX_MULTIWAY_PATH_QUERY_CLASSES_PER_POSITION,
        hero_combo=hero_combo,
    )
    path_scenario = situation.path_scenario
    position_ranges = situation.position_ranges
    postflop_positions = situation.postflop_positions
    effective_stack_bb = situation.effective_stack_bb

    key = (tuple(action_kinds), players, round(stack_bb), iterations, board_cards,
           flop_iterations, _hero_cache_component(hero_combo, situation.hero_in_range))
    with _flop_multiway_path_cache.lock:
        cached = _flop_multiway_path_cache.entries.get(key)
    if cached is None:
        result = solve_flop_multiway(
            board=board_cards,
            position_ranges=position_ranges,
            pot=path_scenario.pot,
            effective_stack_bb=effective_stack_bb,
            positions=postflop_positions,
            raise_sizes=cfg.MULTIWAY_FLOP_RAISE_SIZES,
            max_raises=cfg.MULTIWAY_FLOP_MAX_RAISES,
            iterations=flop_iterations,
        )
        with _flop_multiway_path_cache.lock:
            _flop_multiway_path_cache.entries[key] = result
        cached = result

    formatted = format_flop_response(cached, board="".join(str(c) for c in board_cards))
    response = {
        "board": formatted["board"],
        "action_path": list(action_kinds),
        "stack_bb": stack_bb,
        "effective_stack_bb": effective_stack_bb,
        "pot": path_scenario.pot,
        "flop_iterations": formatted["iterations"],
        "elapsed_seconds": formatted["elapsed_seconds"],
        "strategy": formatted["strategy"],
        "trained": formatted["trained"],
        "position": formatted["position"],
        "positions": formatted["positions"],
        "players": players,
        "hero_in_range": situation.hero_in_range,
        "range_confidence": situation.range_confidence,
        "hero_range_trained": situation.hero_range_trained,
    }
    if not flop_action_kinds:
        return response

    # M87: a multiway flop decision that isn't the street's first, the
    # same gap M84 closed heads-up. This cell needed no new solve to do
    # it: `solve_flop_multiway` returns a StrategyResult over the whole
    # flop tree (flop-only — no chance dispatch), so the node is already
    # there. That is why the FLOP extends to multiway cleanly while the
    # turn and river do not: those read their node off a SAMPLED chance
    # branch, where the node a client asks about may never have been
    # built. See _advise's own refusal for those two.
    _actions, flop_node = _resolve_action_path(cached.root, flop_action_kinds)
    if isinstance(flop_node, TerminalNode):
        raise ValueError(
            "flop_action_path reaches a terminal — the flop's action has closed, so there is no "
            "flop decision left to advise. Supply a turn_card for turn advice."
        )
    return {
        **response,
        "flop_action_path": list(flop_action_kinds),
        "strategy": cached.strategy_at(flop_node),
        "trained": cached.trained_hands(flop_node),
        "position": flop_node.player_to_act,
        "player_to_act": flop_node.player_to_act,
        "pot": flop_node.pot,
        "effective_stack_bb": effective_stack_bb - max(flop_node.invested.values()),
    }


def _query_flop_node_from_path(
    preflop_action_kinds: list,
    flop_action_kinds: list,
    stack_bb: float,
    board_cards: tuple,
    iterations: int,
    turn_iterations: int,
    players: int = 2,
    hero_combo=None,
) -> dict:
    """Advice at a flop decision that is NOT the first one (M84).

    The gap this closes, found by the round-8 diagnostic: `/advise` could
    only answer the opening decision of each street. A player **facing a
    bet on the flop** — the most common and most consequential decision in
    poker — got a 422. `/advise` answered "what do I do first on this
    street" when the product's whole purpose is "what do I do now".

    It was never a solver limitation. `solve_flop_turn` already solves the
    entire flop subtree; the turn cell already walks an arbitrary path
    into it with `_resolve_action_path`. The data existed and was thrown
    away because nothing asked for it.

    So this shares the turn cell's solve and cache verbatim — same key,
    same `solve_flop_turn` call — and differs in exactly one respect:
    where `_query_turn_from_path` requires the resolved flop node to be a
    TerminalNode (the flop's action has closed, so a turn card can be
    dealt), this one requires the opposite. A DecisionNode means there is
    still a decision to advise, which is the whole point. A client asking
    about a closed line gets told to ask about the turn instead.

    Sharing the cache is not incidental: a player who asks about a flop
    decision and then about the turn pays for one solve, not two.
    """
    situation = _derive_path_situation(
        action_kinds=preflop_action_kinds,
        stack_bb=stack_bb,
        board_cards=board_cards,
        iterations=iterations,
        players=players,
        multiway=False,
        sibling_endpoint="/solve_flop_multiway_from_path",
        max_classes_per_position=cfg.MAX_PATH_QUERY_CLASSES_PER_SIDE,
        path_field_name="preflop_action_path",
        hero_combo=hero_combo,
    )
    oop_position, ip_position = situation.postflop_positions
    effective_stack_bb = situation.effective_stack_bb
    path_scenario = situation.path_scenario

    # M88 (R12): `solve_flop` at the SAME tree the canonical library uses
    # for this street's opening decision, rather than solve_flop_turn's
    # narrower one.
    #
    # M84 shared the turn cell's solve, which was cheap but meant the two
    # flop decisions modelled different games (F12): the opening one
    # offered three raise sizes and a 4-raise cap, one decision later only
    # a single size and a 2-raise cap. A user offered `raise:12.50` could
    # find that action simply gone. Neither answer was wrong; they were
    # answers to different questions, presented as one continuous street.
    #
    # The cost of sharing was also the wrong way round: `solve_flop` is
    # flop-only (runouts averaged at the terminal) where `solve_flop_turn`
    # chains a real turn, so the consistent option is also the cheaper
    # one. What it gives up is the shared cache — a user who asks about a
    # mid-flop decision AND the turn now pays for two solves instead of
    # one. That is the right trade: the doubled cost hits only users who
    # ask both questions, while the inconsistency hit everyone who asked
    # twice on the same street.
    flop_solve_key = (
        tuple(preflop_action_kinds),
        round(stack_bb),
        iterations,
        board_cards,
        players,
        _hero_cache_component(hero_combo, situation.hero_in_range),
    )
    with _flop_node_cache.lock:
        result = _flop_node_cache.entries.get(flop_solve_key)
    if result is None:
        result = solve_flop(
            board=board_cards,
            hero_range=situation.position_ranges[oop_position],
            villain_range=situation.position_ranges[ip_position],
            pot=path_scenario.pot,
            effective_stack_bb=effective_stack_bb,
            positions=(oop_position, ip_position),
            iterations=cfg.PATH_QUERY_ITERATIONS,
        )
        with _flop_node_cache.lock:
            _flop_node_cache.entries[flop_solve_key] = result

    _actions, flop_node = _resolve_action_path(result.root, flop_action_kinds)
    if isinstance(flop_node, TerminalNode):
        raise ValueError(
            "flop_action_path reaches a terminal — the flop's action has closed, so there is no "
            "flop decision left to advise. Supply a turn_card for turn advice."
        )

    return {
        "board": "".join(str(c) for c in board_cards),
        "action_path": list(preflop_action_kinds),
        "flop_action_path": list(flop_action_kinds),
        "stack_bb": stack_bb,
        "effective_stack_bb": effective_stack_bb - max(flop_node.invested.values()),
        "pot": flop_node.pot,
        "strategy": result.strategy_at(flop_node),
        "trained": result.trained_hands(flop_node),
        "position": flop_node.player_to_act,
        "player_to_act": flop_node.player_to_act,
        "positions": [oop_position, ip_position],
        "players": players,
        "is_terminal": False,
        "hero_in_range": situation.hero_in_range,
        "range_confidence": situation.range_confidence,
        "solve_iterations": result.iterations,
        "elapsed_seconds": result.elapsed_seconds,
    }


def _query_turn_from_path(
    preflop_action_kinds: list,
    flop_action_kinds: list,
    turn_card,
    stack_bb: float,
    board_cards: tuple,
    iterations: int,
    turn_iterations: int,
    players: int = 2,
    hero_combo=None,
    turn_action_kinds: list | None = None,
) -> dict:
    """Orchestrates POST /solve_turn_from_path end to end: a real
    (cached, raw) preflop solve -> resolve the client's preflop action
    kinds -> derive_ranges_from_path (M16) -> cap both sides (Finding 1,
    same as /solve_flop_from_path) -> solve_flop_turn (M12), behind its
    own narrowly-keyed cache -> resolve the client's flop action kinds
    against *that* result's own root -> deal the client's real turn
    card -> read whatever real strategy solve_flop_turn already computed
    there. See the module docstring for the full design writeup.

    `players` (M29): part of `_turn_path_cache`'s own key below, for the
    identical collision reason `_query_flop_from_path`'s partition key
    now includes it.
    """
    # cfg.MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE, not MAX_PATH_QUERY_CLASSES_
    # PER_SIDE — see that constant's own comment for the real measured
    # reason (solve_flop_turn's cost curve is fundamentally steeper than
    # solve_flop_from_path's own query_strategy-backed one).
    situation = _derive_path_situation(
        action_kinds=preflop_action_kinds,
        stack_bb=stack_bb,
        board_cards=board_cards,
        iterations=iterations,
        players=players,
        multiway=False,
        sibling_endpoint="/solve_turn_multiway_from_path",
        max_classes_per_position=cfg.MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE,
        path_field_name="preflop_action_path",
        hero_combo=hero_combo,
    )
    oop_position, ip_position = situation.postflop_positions
    effective_stack_bb = situation.effective_stack_bb
    path_scenario = situation.path_scenario
    hero_range = situation.position_ranges[oop_position]
    villain_range = situation.position_ranges[ip_position]

    turn_solve_key = (
        tuple(preflop_action_kinds),
        round(stack_bb),
        iterations,
        board_cards,
        turn_iterations,
        players,
        _hero_cache_component(hero_combo, situation.hero_in_range),
    )
    with _turn_path_cache.lock:
        result = _turn_path_cache.entries.get(turn_solve_key)
    if result is None:
        result = solve_flop_turn(
            board=board_cards,
            hero_range=hero_range,
            villain_range=villain_range,
            pot=path_scenario.pot,
            effective_stack_bb=effective_stack_bb,
            positions=(oop_position, ip_position),
            raise_sizes=cfg.FLOP_TURN_RAISE_SIZES,
            max_raises=cfg.FLOP_TURN_MAX_RAISES,
            iterations=turn_iterations,
        )
        with _turn_path_cache.lock:
            _turn_path_cache.entries[turn_solve_key] = result

    _flop_actions, flop_node = _resolve_action_path(result.root, flop_action_kinds)
    if not isinstance(flop_node, TerminalNode):
        raise ValueError("flop_action_path does not reach a terminal — action isn't capped yet")

    response = {
        "board": "".join(str(c) for c in board_cards),
        "turn_card": str(turn_card),
        "preflop_action_path": list(preflop_action_kinds),
        "flop_action_path": list(flop_action_kinds),
        "stack_bb": stack_bb,
        "position": oop_position,
        "positions": [oop_position, ip_position],
        "players": players,
        "elapsed_seconds": result.elapsed_seconds,
        "hero_in_range": situation.hero_in_range,
        "range_confidence": situation.range_confidence,
        "hero_range_trained": situation.hero_range_trained,
    }

    if id(flop_node) not in result.chance_data:
        # Heads-up only: proven airtight, not assumed (see the module
        # docstring) — TerminalNode.is_showdown is exactly `len(folded)
        # == 0` for a 2-position tree, and chance_data is only ever
        # populated for showdown-eligible terminals, so "not in
        # chance_data" and "folded" are exactly equivalent here, no
        # ambiguous third case.
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "trained": {},
            "pot": flop_node.pot,
            "effective_stack_bb": effective_stack_bb,
        }

    chance_node = result.chance_data[id(flop_node)]
    if turn_card not in chance_node.branches:
        raise ValueError(f"{turn_card} is not a legal turn card here (already on the board, or already dealt)")
    turn_node = chance_node.branches[turn_card].root

    # Recomputed identically to chance.py's build_chance_node's own
    # `remaining_stack` — there is no way to read it back off ChanceNode/
    # ChanceBranch directly, so this must stay hand-in-sync with that
    # formula if it ever changes. Safe over ALL positions' invested
    # (not just one), same max()-over-everyone reasoning _preflop_walk's
    # own to_call computation already relies on.
    remaining_stack = effective_stack_bb - max(chance_node.invested.values())

    if isinstance(turn_node, TerminalNode):
        # The flop action already put a player fully all-in — chance.py's
        # own design reuses the terminal itself as branch.root in that
        # case, never populating a real turn decision node.
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "trained": {},
            "pot": turn_node.pot,
            "effective_stack_bb": remaining_stack,
        }

    # M85: a deeper turn decision, not just branch.root. This used to be
    # "only the FIRST turn decision is ever exposed here... a deliberate
    # cut, not an oversight" — the same cut M84 removed on the flop, and
    # wrong for the same reason: a player FACING A BET on the turn could
    # not get advice, which is the decision they most need. The subtree is
    # already solved; resolving into it costs nothing.
    if turn_action_kinds:
        _turn_actions, turn_node = _resolve_action_path(turn_node, turn_action_kinds)
        if isinstance(turn_node, TerminalNode):
            raise ValueError(
                "turn_action_path reaches a terminal — the turn's action has closed, so there is "
                "no turn decision left to advise. Supply a river_card for river advice."
            )
        remaining_stack = effective_stack_bb - max(turn_node.invested.values())

    strategy = result.strategy_at(turn_node)
    trained = result.trained_hands(turn_node)
    return {
        **response,
        "turn_action_path": list(turn_action_kinds or []),
        "is_terminal": False,
        "player_to_act": turn_node.player_to_act,
        "position": turn_node.player_to_act,
        "strategy": strategy,
        "trained": trained,
        "pot": turn_node.pot,
        "effective_stack_bb": remaining_stack,
    }


def _query_turn_multiway_from_path(
    preflop_action_kinds: list,
    flop_action_kinds: list,
    turn_card,
    stack_bb: float,
    board_cards: tuple,
    iterations: int,
    flop_iterations: int,
    players: int = 3,
    hero_combo=None,
    turn_action_kinds: list | None = None,
    river_card=None,
) -> dict:
    """Orchestrates POST /solve_turn_multiway_from_path end to end — the
    multiway analog of _query_turn_from_path (M26): a real (cached, raw)
    preflop solve -> resolve the client's preflop action kinds ->
    derive_ranges_from_path (M16, already N-general) -> require a genuine
    3+-live-position terminal (mirrors _query_flop_multiway_from_path's
    own M42 scope boundary — a 2-survivor path stays /solve_turn_from_
    path's own job) -> cap every position's range -> solve_flop_turn_
    multiway (M36), behind its own plain cache -> resolve the client's
    flop action kinds against THAT result's own root -> deal the
    client's real turn card, via ensure_flop_turn_multiway_branch (M44)
    rather than a plain dict lookup — a real, structural difference from
    _query_turn_from_path's own chance_node.branches[turn_card] lookup:
    solve_flop_turn_multiway's chance_data only contains the (terminal,
    card) pairs MCCFR actually happened to sample, not every legal card
    the way the exact solver's chance_data does, so a legal card the
    solve never sampled is built on demand instead of rejected.
    """
    situation = _derive_path_situation(
        action_kinds=preflop_action_kinds,
        stack_bb=stack_bb,
        board_cards=board_cards,
        iterations=iterations,
        players=players,
        multiway=True,
        sibling_endpoint="/solve_turn_from_path",
        max_classes_per_position=cfg.MAX_MULTIWAY_TURN_PATH_QUERY_CLASSES_PER_POSITION,
        path_field_name="preflop_action_path",
        hero_combo=hero_combo,
    )
    path_scenario = situation.path_scenario
    position_ranges = situation.position_ranges
    postflop_positions = situation.postflop_positions
    effective_stack_bb = situation.effective_stack_bb

    # River depth is requested by supplying BOTH a turn action path and
    # a river card; either alone is a caller error rejected upstream.
    to_river = river_card is not None
    solver = solve_flop_to_river_multiway if to_river else solve_flop_turn_multiway

    # to_river is part of the key: the two solvers produce genuinely
    # different results (chain_to_river populates a second level of
    # chance_fn), so a turn-depth result must never be served to a
    # river-depth query or vice versa — the same collision reasoning
    # every other cache key in this file applies to `players`.
    turn_solve_key = (
        tuple(preflop_action_kinds),
        players,
        round(stack_bb),
        iterations,
        board_cards,
        flop_iterations,
        to_river,
        _hero_cache_component(hero_combo, situation.hero_in_range),
    )
    with _turn_multiway_path_cache.lock:
        result = _turn_multiway_path_cache.entries.get(turn_solve_key)
    if result is None:
        result = solver(
            board=board_cards,
            position_ranges=position_ranges,
            pot=path_scenario.pot,
            effective_stack_bb=effective_stack_bb,
            positions=postflop_positions,
            raise_sizes=cfg.MULTIWAY_FLOP_RAISE_SIZES,
            max_raises=cfg.MULTIWAY_FLOP_MAX_RAISES,
            iterations=flop_iterations,
        )
        with _turn_multiway_path_cache.lock:
            _turn_multiway_path_cache.entries[turn_solve_key] = result

    _flop_actions, flop_node = _resolve_action_path(result.root, flop_action_kinds)
    if not isinstance(flop_node, TerminalNode):
        raise ValueError("flop_action_path does not reach a terminal — action isn't capped yet")

    response = {
        "board": "".join(str(c) for c in board_cards),
        "turn_card": str(turn_card),
        "river_card": None if river_card is None else str(river_card),
        "preflop_action_path": list(preflop_action_kinds),
        "flop_action_path": list(flop_action_kinds),
        "turn_action_path": list(turn_action_kinds or []),
        "stack_bb": stack_bb,
        "flop_iterations": result.iterations,
        "position": postflop_positions[0],
        "positions": list(postflop_positions),
        "players": players,
        "elapsed_seconds": result.elapsed_seconds,
        "hero_in_range": situation.hero_in_range,
        "range_confidence": situation.range_confidence,
        "hero_range_trained": situation.hero_range_trained,
    }

    if not flop_node.is_showdown:
        # is_showdown is `len(invested) - len(folded) > 1` (game_tree.py),
        # already N-general — a fold-out down to exactly 1 live position
        # at ANY origin table size, not just heads-up. chance_data is
        # only ever populated for a showdown-eligible terminal (both here
        # and in the exact 2-position solver), so this check alone
        # (unlike _query_turn_from_path's own heads-up-only "folded"
        # framing) is both necessary and sufficient here too.
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "trained": {},
            "pot": flop_node.pot,
            "effective_stack_bb": effective_stack_bb,
        }

    ensure_kwargs = {
        "position_ranges": position_ranges,
        "positions": postflop_positions,
        "raise_sizes": cfg.MULTIWAY_FLOP_RAISE_SIZES,
        "max_raises": cfg.MULTIWAY_FLOP_MAX_RAISES,
        "chain_to_river": to_river,
        # M75: solve the branch rather than returning it untrained.
        "train_iterations": cfg.MULTIWAY_BRANCH_TRAIN_ITERATIONS,
    }
    with _turn_multiway_path_cache.lock:
        try:
            branch = ensure_mccfr_chance_branch(
                result, flop_node, turn_card, board=board_cards,
                effective_stack_bb=effective_stack_bb, **ensure_kwargs,
            )
        except ValueError as exc:
            raise ValueError(f"{turn_card} is not a legal turn card here (already on the board)") from exc
    turn_node = branch.root

    # Recomputed identically to _query_turn_from_path's own remaining_
    # stack formula — see that function's comment for why this must stay
    # hand-in-sync with chance.py's own construction if it ever changes.
    remaining_stack = effective_stack_bb - max(flop_node.invested.values())

    if isinstance(turn_node, TerminalNode):
        # The flop action already put a player fully all-in — chance.py's
        # own design reuses the terminal itself as branch.root there.
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "trained": {},
            "pot": turn_node.pot,
            "effective_stack_bb": remaining_stack,
        }

    if not to_river:
        strategy = result.strategy_at(turn_node)
        trained = result.trained_hands(turn_node)
        return {
            **response,
            "is_terminal": False,
            "player_to_act": turn_node.player_to_act,
            "strategy": strategy,
            "trained": trained,
            "pot": turn_node.pot,
            "effective_stack_bb": remaining_stack,
        }

    # --- One hop further: the river (M53) -------------------------------
    # Structurally identical to the turn hop above, which is exactly the
    # finding: M44 left open whether a SECOND chained hop needs different
    # treatment, and it does not — same ensure_mccfr_chance_branch, one
    # card-richer board, one street-deeper remaining stack.
    _turn_actions, turn_terminal = _resolve_action_path(turn_node, turn_action_kinds)
    if not isinstance(turn_terminal, TerminalNode):
        raise ValueError("turn_action_path does not reach a terminal — action isn't capped yet")

    # The TURN street's own fresh, 0-based investment tracking, not
    # cumulative with the flop's (game_tree.StreetConfig's per-street
    # reset) — the same subtlety _query_river_from_path documents.
    remaining_after_turn = remaining_stack - max(turn_terminal.invested.values())

    if not turn_terminal.is_showdown:
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "trained": {},
            "pot": turn_terminal.pot,
            "effective_stack_bb": remaining_after_turn,
        }

    with _turn_multiway_path_cache.lock:
        try:
            river_branch = ensure_mccfr_chance_branch(
                result, turn_terminal, river_card, board=board_cards + (turn_card,),
                effective_stack_bb=remaining_stack, **ensure_kwargs,
            )
        except ValueError as exc:
            raise ValueError(f"{river_card} is not a legal river card here (already on the board)") from exc
    river_node = river_branch.root

    if isinstance(river_node, TerminalNode):
        # Already all-in by the turn — nothing left to decide, just a
        # showdown once the river lands.
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "trained": {},
            "pot": river_node.pot,
            "effective_stack_bb": remaining_after_turn,
        }

    return {
        **response,
        "is_terminal": False,
        "player_to_act": river_node.player_to_act,
        "strategy": result.strategy_at(river_node),
        "trained": result.trained_hands(river_node),
        "pot": river_node.pot,
        "effective_stack_bb": remaining_after_turn,
    }


def _query_river_from_path(
    preflop_action_kinds: list,
    flop_action_kinds: list,
    turn_card,
    turn_action_kinds: list,
    river_card,
    stack_bb: float,
    board_cards: tuple,
    iterations: int,
    river_iterations: int,
    players: int = 2,
    hero_combo=None,
    river_action_kinds: list | None = None,
) -> dict:
    """Orchestrates POST /solve_river_from_path end to end — one street
    further than _query_turn_from_path, whose structure this mirrors
    exactly for the first hop: a real (cached, raw) preflop solve ->
    resolve the client's preflop action kinds -> derive_ranges_from_path
    (M16) -> require exactly 2 live positions -> cap both sides by COMBO
    count (cfg.RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE, not class — see that
    constant's own comment for the measured reason) -> solve_flop_to_
    river (M13), behind its own narrowly-keyed cache -> resolve the
    client's flop action kinds against that result's own root -> deal
    the client's real turn card -> resolve the client's real TURN action
    kinds against the resulting turn-street root (new relative to the
    turn endpoint, which only ever exposes the FIRST turn decision) ->
    deal the client's real river card -> read whatever real strategy
    solve_flop_to_river already computed there.

    `players` (mirrors M29's own precedent): part of `_river_path_
    cache`'s own key, for the identical collision reason `_turn_path_
    cache`'s key already includes it.
    """
    # Combo-level capping, not class-level like every sibling endpoint —
    # see cfg.RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE's own comment for the
    # measured reason solve_flop_to_river needs the finer lever.
    situation = _derive_path_situation(
        action_kinds=preflop_action_kinds,
        stack_bb=stack_bb,
        board_cards=board_cards,
        iterations=iterations,
        players=players,
        multiway=False,
        sibling_endpoint="/solve_turn_multiway_from_path",
        max_combos_per_position=cfg.RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE,
        path_field_name="preflop_action_path",
        hero_combo=hero_combo,
    )
    oop_position, ip_position = situation.postflop_positions
    effective_stack_bb = situation.effective_stack_bb
    path_scenario = situation.path_scenario
    hero_range = situation.position_ranges[oop_position]
    villain_range = situation.position_ranges[ip_position]

    river_solve_key = (
        tuple(preflop_action_kinds),
        round(stack_bb),
        iterations,
        board_cards,
        river_iterations,
        players,
        _hero_cache_component(hero_combo, situation.hero_in_range),
    )
    with _river_path_cache.lock:
        result = _river_path_cache.entries.get(river_solve_key)
    if result is None:
        result = solve_flop_to_river(
            board=board_cards,
            hero_range=hero_range,
            villain_range=villain_range,
            pot=path_scenario.pot,
            effective_stack_bb=effective_stack_bb,
            positions=(oop_position, ip_position),
            raise_sizes=cfg.FLOP_TO_RIVER_RAISE_SIZES,
            max_raises=cfg.FLOP_TO_RIVER_MAX_RAISES,
            iterations=river_iterations,
        )
        with _river_path_cache.lock:
            _river_path_cache.entries[river_solve_key] = result

    _flop_actions, flop_node = _resolve_action_path(result.root, flop_action_kinds)
    if not isinstance(flop_node, TerminalNode):
        raise ValueError("flop_action_path does not reach a terminal — action isn't capped yet")

    response = {
        "board": "".join(str(c) for c in board_cards),
        "turn_card": str(turn_card),
        "river_card": str(river_card),
        "preflop_action_path": list(preflop_action_kinds),
        "flop_action_path": list(flop_action_kinds),
        "turn_action_path": list(turn_action_kinds),
        "stack_bb": stack_bb,
        "position": oop_position,
        "positions": [oop_position, ip_position],
        "players": players,
        "river_iterations": river_iterations,
        "elapsed_seconds": result.elapsed_seconds,
        "hero_in_range": situation.hero_in_range,
        "range_confidence": situation.range_confidence,
        "hero_range_trained": situation.hero_range_trained,
    }

    if id(flop_node) not in result.chance_data:
        # Not showdown-eligible at the flop — someone folded there. No
        # turn or river decision to make. Same reasoning as _query_turn_
        # from_path's own identical check (is_showdown is exactly "2+
        # live positions", proven N-general, not a heads-up-only fact —
        # see game_tree.TerminalNode.is_showdown's own definition).
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "trained": {},
            "pot": flop_node.pot,
            "effective_stack_bb": effective_stack_bb,
        }

    turn_chance_node = result.chance_data[id(flop_node)]
    if turn_card not in turn_chance_node.branches:
        raise ValueError(f"{turn_card} is not a legal turn card here (already on the board, or already dealt)")
    turn_root = turn_chance_node.branches[turn_card].root

    # Same recomputation _query_turn_from_path's own comment already
    # explains (no way to read remaining_stack back off ChanceNode/
    # ChanceBranch directly) — the amount remaining entering the turn.
    remaining_stack_after_flop = effective_stack_bb - max(turn_chance_node.invested.values())

    if isinstance(turn_root, TerminalNode):
        # The flop action already put a player fully all-in — chance.py's
        # own design reuses the terminal itself as branch.root in that
        # case, never populating a real turn decision node, so there's
        # no turn action to resolve and no river decision either.
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "trained": {},
            "pot": turn_root.pot,
            "effective_stack_bb": remaining_stack_after_flop,
        }

    # New relative to _query_turn_from_path: the turn is a full betting
    # round, so a real river decision needs the client's own turn action
    # path resolved against turn_root, not turn_root itself.
    _turn_actions, turn_node = _resolve_action_path(turn_root, turn_action_kinds)
    if not isinstance(turn_node, TerminalNode):
        raise ValueError("turn_action_path does not reach a terminal — action isn't capped yet")

    # The amount remaining entering the river — turn_node.invested is
    # the TURN street's own fresh (0-based) investment tracking, not
    # cumulative with the flop's, per game_tree.StreetConfig's own
    # per-street reset (see game_tree.py's pot_offset docstring).
    remaining_stack_after_turn = remaining_stack_after_flop - max(turn_node.invested.values())

    if id(turn_node) not in result.chance_data:
        # Folded on the turn — no river decision to make.
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "trained": {},
            "pot": turn_node.pot,
            "effective_stack_bb": remaining_stack_after_turn,
        }

    river_chance_node = result.chance_data[id(turn_node)]
    if river_card not in river_chance_node.branches:
        raise ValueError(f"{river_card} is not a legal river card here (already on the board, or already dealt)")
    river_node = river_chance_node.branches[river_card].root

    if isinstance(river_node, TerminalNode):
        # Already all-in on the turn — no river decision, just a
        # showdown once the river card is revealed.
        return {
            **response,
            "is_terminal": True,
            "player_to_act": None,
            "strategy": {},
            "trained": {},
            "pot": river_node.pot,
            "effective_stack_bb": remaining_stack_after_turn,
        }

    # M86: a river decision deeper than the street's first. Completes the
    # arc M84 (flop) and M85 (turn) began — the river's later decisions
    # were the last unreachable ones in the heads-up tree, and facing a
    # river bet is the largest single decision in a hand.
    if river_action_kinds:
        _river_actions, river_node = _resolve_action_path(river_node, river_action_kinds)
        if isinstance(river_node, TerminalNode):
            raise ValueError(
                "river_action_path reaches a terminal — the hand is over, so there is no river "
                "decision left to advise."
            )
        remaining_stack_after_turn = remaining_stack_after_turn - max(river_node.invested.values())

    strategy = result.strategy_at(river_node)
    trained = result.trained_hands(river_node)
    return {
        **response,
        "river_action_path": list(river_action_kinds or []),
        "is_terminal": False,
        "player_to_act": river_node.player_to_act,
        "position": river_node.player_to_act,
        "strategy": strategy,
        "trained": trained,
        "pot": river_node.pot,
        "effective_stack_bb": remaining_stack_after_turn,
    }


_ADVISE_STREETS = ("preflop", "flop", "turn", "river")

# Per-(street, is-multiway) postflop iteration cap, reusing each sibling
# endpoint's own separately-measured constant rather than inventing one
# blended value — that per-cell measurement work (M24/M26/M42/M44/M46,
# re-tuned at M49) is real and cell-specific. Preflop has no postflop
# leg, so no entry.
_ADVISE_ITERATION_CAPS = {
    ("flop", False): (cfg.PATH_QUERY_ITERATIONS, cfg.PATH_QUERY_ITERATIONS),
    ("flop", True): (cfg.DEFAULT_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS, cfg.MAX_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS),
    ("turn", False): (DEFAULT_FLOP_TURN_ITERATIONS, cfg.MAX_FLOP_TURN_ITERATIONS),
    ("turn", True): (
        cfg.DEFAULT_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS,
        cfg.MAX_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS,
    ),
    ("river", False): (cfg.DEFAULT_RIVER_PATH_QUERY_ITERATIONS, cfg.MAX_RIVER_PATH_QUERY_ITERATIONS),
    ("river", True): (
        DEFAULT_FLOP_TO_RIVER_MULTIWAY_ITERATIONS,
        cfg.MAX_FLOP_TO_RIVER_MULTIWAY_ITERATIONS,
    ),
}

# The (street, is-multiway) cells /advise deliberately does NOT serve
# yet, each with the real reason — checked BEFORE the cap lookup above,
# so the caller gets this explanation rather than the bare KeyError that
# a missing cap entry would otherwise raise first (a real ordering bug
# caught by this milestone's own test, not by review).
_ADVISE_UNSUPPORTED_CELLS: dict = {
    # Empty as of M53, which filled the last cell — (river, multiway).
    # Kept (rather than deleted) as the declared place any future
    # unsupported cell states its real reason, and because the route's
    # own check reads it unconditionally.
}


def _infer_street(request) -> str:
    """Which street an AdviseRequest describes, from which fields it
    actually carries (M51) — plus rejection of every partial/skipped
    combination, so a client can't silently get advice for a shallower
    street than it thought it asked about.

    Deliberately inferred rather than client-declared: a `street` field
    the client sets independently of its own board/card fields is a
    second source of truth that can disagree with them.
    """
    if request.board is None:
        for field in (
            "flop_action_path", "turn_card", "turn_action_path", "river_card", "river_action_path",
        ):
            if getattr(request, field) is not None:
                raise ValueError(f"{field} was supplied without a board — a preflop query takes neither")
        return "preflop"

    if request.turn_card is None:
        for field in ("turn_action_path", "river_card", "river_action_path"):
            if getattr(request, field) is not None:
                raise ValueError(f"{field} was supplied without a turn_card")
        # M84: a flop query MAY now carry a flop_action_path. It used to
        # be rejected outright, which meant only the OPENING decision of
        # the street was reachable — a player facing a bet on the flop got
        # a 422. The path simply says which flop decision is being asked
        # about; an empty or absent one still means the first.
        return "flop"

    if request.flop_action_path is None:
        raise ValueError("turn_card requires flop_action_path — the flop's action has to close first")

    if request.river_card is None:
        # M85: a turn query MAY now carry a turn_action_path, for the same
        # reason M84 allowed one on the flop — otherwise only the street's
        # opening decision is reachable. A river_action_path without a
        # river card is still a contradiction, though.
        if request.river_action_path is not None:
            raise ValueError("river_action_path was supplied without a river_card")
        return "turn"

    if request.turn_action_path is None:
        raise ValueError("river_card requires turn_action_path — the turn's action has to close first")
    return "river"


def _live_position_count(request, iterations: int) -> int:
    """How many positions actually SURVIVE the preflop path (M52 fix).

    Load-bearing, and a real bug before this existed: /advise used to
    pick its 2-position-vs-multiway solver from `request.players` — the
    ORIGIN table size — which is the wrong question. M29 built support
    specifically for the most common real full-ring shape: everyone
    folds and two players see the flop heads-up. That hand has
    `players=6` but must use the EXACT 2-position solver, not MCCFR.
    Choosing on table size routed it to the multiway cell, which then
    correctly refused it — making /advise unusable for exactly the case
    M29 existed to serve.

    Counts from the resolved node's own `folded` set rather than calling
    derive_ranges_from_path: the reach-multiplication that function does
    is real work this question doesn't need, and it raises on a
    fold-out-to-one path that the orchestrators themselves report far
    more clearly. The preflop solve is already cached, so this is a tree
    walk, not a second solve.
    """
    preflop_result = _get_or_solve_preflop_raw(request.stack_bb, iterations, players=request.players)
    _actions, node = _resolve_action_path(preflop_result.root, request.preflop_action_path)
    return sum(1 for p in preflop_result.config.positions if p not in node.folded)


def _advise_preflop(request, iterations: int, hero_combo=None) -> dict:
    """The one /advise cell with no sibling endpoint behind it (M51):
    real preflop strategy at whatever node the action path reaches.

    Note the deliberately INVERTED terminal requirement relative to
    every postflop cell: those need the preflop action to have CLOSED
    (a TerminalNode) before a board is dealt, whereas preflop advice
    needs the opposite — a live DecisionNode with someone still to act.
    A path that already closed has no preflop decision left to advise.
    """
    preflop_result = _get_or_solve_preflop_raw(request.stack_bb, iterations, players=request.players)
    _actions, node = _resolve_action_path(preflop_result.root, request.preflop_action_path)

    if isinstance(node, TerminalNode):
        raise ValueError(
            "preflop_action_path already reaches a terminal — no preflop decision left to advise. "
            "Supply a board for postflop advice, or shorten the path."
        )

    strategy = preflop_result.strategy_at(node)
    trained = preflop_result.trained_hands(node)
    hero_key = None if hero_combo is None else str(_combo_to_class(hero_combo))
    live_positions = [p for p in preflop_result.config.positions if p not in node.folded]
    return {
        "street": "preflop",
        "players": request.players,
        "positions": live_positions,
        "position": node.player_to_act,
        "player_to_act": node.player_to_act,
        "is_terminal": False,
        "pot": node.pot,
        "effective_stack_bb": preflop_result.config.stack_bb - max(node.invested.values()),
        "strategy": strategy,
        "trained": trained,
        "source": "preflop",
        "solve_iterations": preflop_result.iterations,
        "elapsed_seconds": preflop_result.elapsed_seconds,
        # Preflop strategies are keyed by hand CLASS ("AKs"), not by
        # concrete combo ("AsKs") the way every postflop street is — the
        # preflop solver works over the 169-class abstraction (v1's own
        # foundational choice). So hero's lookup key differs by street,
        # and the route must not assume one shape; it reads hero_key.
        #
        # in_range USED to be hardcoded True here, on the reasoning that
        # "a preflop solve covers every class, so there's no cap for hero
        # to fall outside of." That premise is true at heads-up (which
        # really does solve all 169 classes) and was FALSE at multiway,
        # which solves only its own pool — so a 6-max request for a hand
        # outside that pool got `in_range: true` alongside a null
        # strategy, which is precisely the confidently-wrong output this
        # project's honesty signals exist to prevent (M67). Checked
        # against the solved strategy now, so the answer is derived
        # rather than assumed and stays correct whatever the pool is.
        "hero_key": hero_key,
        "hero_in_range": None if hero_key is None else hero_key in strategy,
    }


def _advise(request, street: str, iterations: int, solve_iterations: int, hero_combo, multiway: bool) -> dict:
    """Dispatches one AdviseRequest to whichever sibling orchestrator
    already serves its (street, table size) cell, then normalizes the
    result into AdviseResponse's own shape (M51).

    Deliberately delegates rather than reimplements: every cell's own
    cache, cap constant, and solver choice stays exactly as its sibling
    endpoint already had it — this is a unified FRONT DOOR, not a second
    implementation to keep in sync.
    """
    if street == "preflop":
        return _advise_preflop(request, iterations, hero_combo)

    # Postflop streets key their strategy dicts by concrete combo,
    # unlike preflop's 169-class abstraction handled above.
    hero_key = None if hero_combo is None else str(hero_combo)

    board_cards = tuple(parse_cards(request.board))
    if len(board_cards) != 3:
        raise ValueError(f"board must have exactly 3 cards for a flop, got {len(board_cards)}")

    if street == "flop" and not multiway and request.flop_action_path:
        # M84: a flop decision that isn't the street's first. Goes through
        # solve_flop_turn (which solves the whole flop subtree) rather
        # than the canonical library, because the library persists only a
        # flattened ROOT strategy and structurally cannot answer about a
        # deeper node. Shares the turn cell's cache, so asking about a
        # flop decision and then the turn costs one solve.
        raw = _query_flop_node_from_path(
            request.preflop_action_path, request.flop_action_path, request.stack_bb,
            board_cards, iterations, solve_iterations, request.players,
            hero_combo=hero_combo,
        )
        return {**raw, "source": "exact", "street": street, "hero_key": hero_key}

    if street == "flop" and not multiway:
        raw = _query_flop_from_path(
            request.preflop_action_path, request.stack_bb, board_cards, iterations, request.players,
            hero_combo=hero_combo,
        )
        # M76: the canonical library now persists per-combo `trained`
        # flags alongside the strategy, so this cell reports real
        # confidence instead of a null. It USED to be documented as a
        # structural limitation ("persists only a flattened strategy
        # dict") — but the data was never structurally unavailable, the
        # LibraryEntry dataclass simply did not carry it. Still falls back
        # to None for a library built before M76, where the flags really
        # are absent; that is a genuine "unknown", not a claim.
        return {**raw, "trained": raw.get("trained"),
                "source": "library_hit" if raw["hit"] else "library_miss",
                "solve_iterations": None, "is_terminal": False, "player_to_act": raw["position"],
                "street": street, "hero_key": hero_key}

    if street == "flop":
        # M87: flop_action_path works at multiway too. Unlike the turn and
        # river cells below, solve_flop_multiway hands back the whole flop
        # tree, so a deeper decision is already solved for.
        raw = _query_flop_multiway_from_path(
            request.preflop_action_path, request.stack_bb, board_cards, iterations, solve_iterations,
            request.players, hero_combo=hero_combo,
            flop_action_kinds=request.flop_action_path,
        )
        return {**raw, "source": "mccfr", "solve_iterations": raw["flop_iterations"],
                "is_terminal": False, "player_to_act": raw["position"], "street": street,
                "hero_key": hero_key}

    turn_cards = tuple(parse_cards(request.turn_card))
    if len(turn_cards) != 1:
        raise ValueError(f"turn_card must have exactly 1 card, got {len(turn_cards)}")

    if street == "turn":
        query = _query_turn_multiway_from_path if multiway else _query_turn_from_path
        # M85: turn_action_path reaches a turn decision deeper than the
        # street's first, the same way M84's flop_action_path does.
        # Heads-up only for now — the multiway turn cell reads its node
        # off a sampled chance branch and needs its own pass.
        extra = (
            {"turn_action_kinds": request.turn_action_path}
            if (not multiway and request.turn_action_path)
            else {}
        )
        if multiway and request.turn_action_path:
            raise ValueError(
                "turn_action_path isn't supported at multiway tables yet — only the turn's first "
                "decision is reachable there. Heads-up supports any turn decision."
            )
        raw = query(
            request.preflop_action_path, request.flop_action_path, turn_cards[0], request.stack_bb,
            board_cards, iterations, solve_iterations, request.players, hero_combo=hero_combo,
            **extra,
        )
        return {**raw, "source": "mccfr" if multiway else "exact",
                "solve_iterations": raw.get("flop_iterations", solve_iterations), "street": street,
                "hero_key": hero_key}

    river_cards = tuple(parse_cards(request.river_card))
    if len(river_cards) != 1:
        raise ValueError(f"river_card must have exactly 1 card, got {len(river_cards)}")
    if multiway:
        # M53: the last cell. Reuses the SAME generalized walker the turn
        # cell uses, one hop deeper — see ensure_mccfr_chance_branch for
        # why a second chained hop needed no structurally different
        # treatment, only a chain_to_river passthrough.
        raw = _query_turn_multiway_from_path(
            request.preflop_action_path, request.flop_action_path, turn_cards[0], request.stack_bb,
            board_cards, iterations, solve_iterations, request.players, hero_combo=hero_combo,
            turn_action_kinds=request.turn_action_path, river_card=river_cards[0],
        )
        return {**raw, "source": "mccfr", "solve_iterations": raw["flop_iterations"],
                "street": street, "hero_key": hero_key}
    # M86: river_action_path reaches a river decision deeper than the
    # street's first, completing what M84 (flop) and M85 (turn) began.
    if multiway and request.river_action_path:
        raise ValueError(
            "river_action_path isn't supported at multiway tables yet — only the river's first "
            "decision is reachable there. Heads-up supports any river decision."
        )
    raw = _query_river_from_path(
        request.preflop_action_path, request.flop_action_path, turn_cards[0], request.turn_action_path,
        river_cards[0], request.stack_bb, board_cards, iterations, solve_iterations, request.players,
        hero_combo=hero_combo, river_action_kinds=request.river_action_path,
    )
    return {**raw, "source": "exact", "solve_iterations": raw["river_iterations"], "street": street,
            "hero_key": hero_key}
