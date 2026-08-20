"""Offline precomputed spot library — Phase 3 of the real-time-speed
roadmap (see CLAUDE.md's "### The real-time-speed roadmap" section).

Batch-solves a set of real flop boards, deduplicating by canonicalize.py's
(M19) suit-isomorphism key, and stores each distinct canonical solve so a
later lookup can serve *any* real board isomorphic to one already solved
— not just the literal board a solve happened to run against.
`query_strategy` (M21) closes Phase 4 itself: canonicalize a real
query, look it up, fall back to an on-demand solve on a miss, cache the
result. No API/frontend wiring here — that's still a separate,
unscoped follow-on once this module's contract is proven (it is,
below and in query_strategy's own docstring).

The crux design constraint, worth stating up front: `build_library`
only accepts *class*-frequency dicts (StartingHand -> weight, the same
shape combos.range_from_class_frequencies already consumes), never raw
per-combo HandCombo dicts. This isn't a convenience simplification —
it's the only way a canonical hit can correctly serve any isomorphic
real board rather than just the one that was solved.

For a real board B with canonicalize_board(B) -> (C, suit_map), and a
hero class dict, build_library derives hero_range = range_from_class_
frequencies(hero_classes, exclude=frozenset(B)) against the REAL board,
then translates every combo into canonical space via translate_combo
before solving against C. For two different real boards B1/B2 that both
canonicalize to the same C (generally via *different* winning
permutations) to safely share one library entry, the translated
(combo -> weight) dict must come out identical either way. It does,
because range_from_class_frequencies is suit-blind by construction:
combos_for_class enumerates a class's combos from StartingHand.high_
rank/.low_rank alone, never a fixed suit, so applying any full 4-suit
bijection just relabels suits throughout without changing which combos
exist or how many — and since both permutations send their own real
board to the identical C, the *set* of translated pairs matches. The
uniform per-class weighting (freq / len(combos)) makes this exact, not
approximate, since len(combos) is itself preserved under any suit
bijection.

This equivalence FAILS for a hand-picked, suit-asymmetric range (e.g.
just {"AcKc": 1.0}) — translating that one combo under two different
winning permutations generally produces two different canonical
combos, so a canonical entry built from B1's asymmetric range would
silently misrepresent a B2 query. There's no cheap general runtime
check for "is this range suit-symmetric," so the design forecloses the
footgun at the API boundary instead: build_library only accepts class
dicts, which are suit-symmetric by construction. A caller needing an
arbitrary/asymmetric combo-level range is out of scope for this
primitive — that's combos.py/solver.py's derive_ranges_from_path (M16)
territory, not yet connected to query_strategy (see its own docstring
for the one real wrinkle: PathScenario.stacks is a per-position dict,
not the single effective_stack_bb float this module expects).

Also deliberately out of scope: non-root-node storage (a library entry
only answers "what should first-to-act do on this canonical flop, at
this stack depth" — not facing-a-bet or any deeper node; the solve
already produces that data cheaply, but which node(s) to store is
really "which action paths does this library serve," the same question
M16's derive_ranges_from_path already generalizes, a natural, cheap
follow-on once a real consumer needs it) and pot from the canonical key
(one build_library call uses one fixed pot for every entry it builds,
the same "fixed menu" reasoning canonicalize.py's own docstring already
applies to raise_sizes/max_raises — a multi-pot/SPR-indexed library is
a flagged, explicit out-of-scope follow-on).
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .board_equity import DEFAULT_SEED as DEFAULT_EQUITY_SEED
from .canonicalize import (
    DEFAULT_STACK_BUCKET_BB,
    canonical_stack_depth,
    canonicalize_board,
    invert_suit_map,
    translate_combo,
)
from .cards import Card
from .combos import HandCombo, range_from_class_frequencies
from .solver import solve_flop

LIBRARY_FORMAT_VERSION = 1


@dataclass(frozen=True)
class LibraryEntry:
    """One precomputed flop solve, keyed by (canonical_board,
    canonical_stack_bb) in the `library` dict build_library returns.

    `strategy` is opening_range()'s output verbatim, in canonical-suit
    space (the solve itself ran against the canonical board) —
    {combo_str: {action_str: freq}}.
    """

    canonical_board: tuple
    canonical_stack_bb: float
    pot: float
    strategy: dict
    iterations: int
    elapsed_seconds: float


def build_library(
    boards,
    hero_classes: dict,
    villain_classes: dict,
    pot: float,
    effective_stack_bb: float,
    positions: tuple = ("OOP", "IP"),
    raise_sizes: tuple = (2.5, 3.0, 2.2),
    max_raises: int = 4,
    iterations: int = None,
    equity_samples: int = None,
    equity_seed: int = DEFAULT_EQUITY_SEED,
    stack_bucket_bb: float = DEFAULT_STACK_BUCKET_BB,
) -> dict:
    """Batch-solves `boards`, deduplicated by canonical (board, stack)
    key — two real boards that canonicalize to an already-seen key are
    skipped, not re-solved, the actual efficiency point of
    canonicalization. Returns {(canonical_board, canonical_stack_bb):
    LibraryEntry}.
    """
    library: dict = {}
    canonical_stack_bb = canonical_stack_depth(effective_stack_bb, stack_bucket_bb)

    for board in boards:
        board = tuple(board)
        canonical_board, suit_map = canonicalize_board(board)
        key = (canonical_board, canonical_stack_bb)
        if key in library:
            continue

        exclude = frozenset(board)
        hero_range = range_from_class_frequencies(hero_classes, exclude=exclude)
        villain_range = range_from_class_frequencies(villain_classes, exclude=exclude)
        canonical_hero_range = {translate_combo(combo, suit_map): weight for combo, weight in hero_range.items()}
        canonical_villain_range = {
            translate_combo(combo, suit_map): weight for combo, weight in villain_range.items()
        }

        result = solve_flop(
            board=canonical_board,
            hero_range=canonical_hero_range,
            villain_range=canonical_villain_range,
            pot=pot,
            effective_stack_bb=canonical_stack_bb,
            positions=positions,
            raise_sizes=raise_sizes,
            max_raises=max_raises,
            iterations=iterations,
            equity_samples=equity_samples,
            equity_seed=equity_seed,
        )

        library[key] = LibraryEntry(
            canonical_board=canonical_board,
            canonical_stack_bb=canonical_stack_bb,
            pot=pot,
            strategy=result.opening_range(),
            iterations=result.iterations,
            elapsed_seconds=result.elapsed_seconds,
        )

    return library


def save_library(library: dict, path) -> None:
    """Serializes `library` to JSON at `path`, creating parent
    directories if needed (mirrors equity.py's on-disk-cache
    precedent). An entry-list shape, not a packed string dict key —
    self-describing and hand-inspectable.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    entries = [
        {
            "canonical_board": [str(card) for card in entry.canonical_board],
            "canonical_stack_bb": entry.canonical_stack_bb,
            "pot": entry.pot,
            "iterations": entry.iterations,
            "elapsed_seconds": entry.elapsed_seconds,
            "strategy": entry.strategy,
        }
        for entry in library.values()
    ]
    with open(path, "w") as f:
        json.dump({"version": LIBRARY_FORMAT_VERSION, "entries": entries}, f)


def load_library(path) -> dict:
    """Inverse of save_library — reconstructs {(canonical_board,
    canonical_stack_bb): LibraryEntry} from a JSON file."""
    with open(path) as f:
        data = json.load(f)

    library: dict = {}
    for raw_entry in data["entries"]:
        canonical_board = tuple(Card.from_str(text) for text in raw_entry["canonical_board"])
        canonical_stack_bb = raw_entry["canonical_stack_bb"]
        entry = LibraryEntry(
            canonical_board=canonical_board,
            canonical_stack_bb=canonical_stack_bb,
            pot=raw_entry["pot"],
            strategy=raw_entry["strategy"],
            iterations=raw_entry["iterations"],
            elapsed_seconds=raw_entry["elapsed_seconds"],
        )
        library[(canonical_board, canonical_stack_bb)] = entry
    return library


def lookup_strategy(
    library: dict, board, effective_stack_bb: float, stack_bucket_bb: float = DEFAULT_STACK_BUCKET_BB
) -> dict:
    """Canonicalizes the real query board/stack, looks up a matching
    LibraryEntry. Returns None on a miss (no fallback solving — see
    query_strategy, below, for that). On a hit, translates the stored
    canonical-space strategy back to the query board's real suits via
    invert_suit_map + translate_combo, so a caller who solved nothing
    themselves still gets a strategy keyed by combos legal on their own
    real board. No filtering is needed: since translate_combo is a
    bijection, the translated-back combos are automatically legal
    against the real query board too.
    """
    board = tuple(board)
    canonical_board, suit_map = canonicalize_board(board)
    canonical_stack_bb = canonical_stack_depth(effective_stack_bb, stack_bucket_bb)

    entry = library.get((canonical_board, canonical_stack_bb))
    if entry is None:
        return None

    inverse_map = invert_suit_map(suit_map)
    return {
        str(translate_combo(HandCombo.from_str(combo_str), inverse_map)): freqs
        for combo_str, freqs in entry.strategy.items()
    }


@dataclass(frozen=True)
class QueryResult:
    """The outcome of one query_strategy call.

    `strategy` is always in the QUERY board's real suit space —
    identical shape whether it came from a hit or a miss, since a
    miss's own final step re-runs lookup_strategy to produce it, the
    same translate-back path a hit always took anyway.
    """

    strategy: dict
    hit: bool
    elapsed_seconds: float


def query_strategy(
    library: dict,
    board,
    hero_classes: dict,
    villain_classes: dict,
    pot: float,
    effective_stack_bb: float,
    positions: tuple = ("OOP", "IP"),
    raise_sizes: tuple = (2.5, 3.0, 2.2),
    max_raises: int = 4,
    iterations: int = None,
    equity_samples: int = None,
    equity_seed: int = DEFAULT_EQUITY_SEED,
    stack_bucket_bb: float = DEFAULT_STACK_BUCKET_BB,
) -> QueryResult:
    """Phase 4: canonicalize-then-lookup, falling back to an on-demand
    solve on a miss — the live query path the real-time-speed roadmap
    has been building toward since M17.

    Tries lookup_strategy first: a hit costs one canonicalize_board
    call, one dict .get(), and a handful of translate_combo calls — no
    CFR, no equity-table construction. On a miss, delegates to
    build_library(boards=[board], ...) (reusing its exact canonicalize+
    translate+solve_flop logic, not duplicating it), dict.update()-
    inserts the single resulting entry into `library` IN PLACE (mutates
    the caller's own dict object — a caller wanting a read-only lookup
    should pass a copy), then re-runs lookup_strategy to produce the
    final answer.

    hero_classes/villain_classes MUST be StartingHand-keyed class-
    frequency dicts, never raw per-combo HandCombo dicts — the same
    build_library constraint, for the same reason (see this module's
    docstring): only a suit-blind range makes a canonical entry correct
    for every isomorphic real board, not just the one solved. A caller
    holding a real, concrete hero hand doesn't need any new surface
    here either — index the returned strategy dict by str(hero_combo).

    The post-insert lookup is provably a hit, not just expected to be:
    canonicalize_board/canonical_stack_depth are pure functions of
    board / (effective_stack_bb, stack_bucket_bb) alone, and this
    function threads the SAME board/effective_stack_bb/stack_bucket_bb
    into the triggering lookup, the build_library call, and the final
    lookup — so the key the entry was just stored under and the key
    the final lookup derives are necessarily identical. Enforced with
    an explicit RuntimeError, not a bare assert (python -O would
    silently strip an assert, and no other module in poker_solver/
    uses a bare assert for an invariant check).

    Known, deliberate limitations:
      - Mutates `library` in place; no automatic save_library
        persistence on a miss (in-memory only — a caller who wants
        durability calls save_library themselves, whenever they
        choose).
      - No concurrency control: two simultaneous callers hitting the
        same miss could both solve and both write (correct final
        state, wasted duplicate work). A real live API/server layer
        (a still-unscoped future milestone) would need its own
        serialization strategy for concurrent misses — not attempted
        here, fine for this module's own sequential tests/measurement.
      - pot, positions, raise_sizes, and max_raises are NOT part of the
        canonical key (inherited from build_library/lookup_strategy's
        own "fixed menu" cut, documented at the top of this module). A
        second query_strategy call against an already-cached (board,
        stack) with a DIFFERENT pot (or bet-sizing menu) still HITS and
        silently returns the FIRST call's strategy, solved under the
        first call's pot — it does not re-solve and does not raise.
        Callers sharing one `library` across genuinely different
        pots/sizing menus need their own external key discipline; a
        multi-pot/SPR-indexed library remains the explicit
        out-of-scope follow-on this module's docstring already flags.
      - No connection yet to solver.py's derive_ranges_from_path (M16)
        — translating a real, user-described action history into this
        function's hero_classes/villain_classes is mostly a direct fit
        for a preflop-rooted path (derive_ranges_from_path already
        returns StartingHand-keyed ranges), but PathScenario.stacks is
        a per-position dict, not the single effective_stack_bb float
        this function expects — an arbitrary path needs an explicit
        "both live positions' remaining stacks are equal here" check
        before that hookup would be safe. Not attempted here.
    """
    start = time.perf_counter()
    strategy = lookup_strategy(library, board, effective_stack_bb, stack_bucket_bb)
    if strategy is not None:
        return QueryResult(strategy=strategy, hit=True, elapsed_seconds=time.perf_counter() - start)

    new_entries = build_library(
        boards=[board],
        hero_classes=hero_classes,
        villain_classes=villain_classes,
        pot=pot,
        effective_stack_bb=effective_stack_bb,
        positions=positions,
        raise_sizes=raise_sizes,
        max_raises=max_raises,
        iterations=iterations,
        equity_samples=equity_samples,
        equity_seed=equity_seed,
        stack_bucket_bb=stack_bucket_bb,
    )
    library.update(new_entries)

    strategy = lookup_strategy(library, board, effective_stack_bb, stack_bucket_bb)
    if strategy is None:
        raise RuntimeError(
            "query_strategy: the just-inserted entry was not a hit for the same "
            "board/stack — a canonicalize_board/canonical_stack_depth determinism "
            "invariant was violated; this should be impossible, please report"
        )
    return QueryResult(strategy=strategy, hit=False, elapsed_seconds=time.perf_counter() - start)
