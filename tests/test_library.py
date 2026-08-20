import pytest

from poker_solver.canonicalize import canonical_stack_depth, canonicalize_board, translate_combo
from poker_solver.cards import Card
from poker_solver.combos import range_from_class_frequencies
from poker_solver.equity import build_equity_table
from poker_solver.game_tree import CALL_OR_CHECK, RAISE, GameConfig, TerminalNode, build_game_tree
from poker_solver.library import (
    build_library,
    load_library,
    lookup_strategy,
    query_strategy,
    query_strategy_from_path,
    save_library,
)
from poker_solver.solver import StrategyResult, derive_ranges_from_path, solve_flop, solve_preflop
from poker_solver.starting_hands import StartingHand


def cards(text: str) -> list:
    return [Card.from_str(token) for token in text.split()]


# ---------------------------------------------------------------------------
# A tiny, fast class pool, kept small and self-contained (not imported from
# api/main.py — poker_solver's own tests never pull fixtures from the API
# layer, and this milestone's library.py can't depend on api/ anyway, see
# tests/test_package_boundary.py).
# ---------------------------------------------------------------------------

HERO_CLASSES = {StartingHand("A", "A"): 1.0}
VILLAIN_CLASSES = {StartingHand("K", "K"): 1.0}
POT = 10.0
STACK_BB = 17.0  # buckets to 15.0 under the default 5bb bucket
RAISE_SIZES = ()
MAX_RAISES = 1
ITERATIONS = 50
EQUITY_SAMPLES = 50
EQUITY_SEED = 1

BOARD_A = tuple(cards("2h 7s 9d"))  # rainbow, no rank ties


def _build_library_kwargs(boards):
    return dict(
        boards=boards,
        hero_classes=HERO_CLASSES,
        villain_classes=VILLAIN_CLASSES,
        pot=POT,
        effective_stack_bb=STACK_BB,
        raise_sizes=RAISE_SIZES,
        max_raises=MAX_RAISES,
        iterations=ITERATIONS,
        equity_samples=EQUITY_SAMPLES,
        equity_seed=EQUITY_SEED,
    )


def _query_kwargs(board):
    return dict(
        board=board,
        hero_classes=HERO_CLASSES,
        villain_classes=VILLAIN_CLASSES,
        pot=POT,
        effective_stack_bb=STACK_BB,
        raise_sizes=RAISE_SIZES,
        max_raises=MAX_RAISES,
        iterations=ITERATIONS,
        equity_samples=EQUITY_SAMPLES,
        equity_seed=EQUITY_SEED,
    )


# ---------------------------------------------------------------------------
# build_library
# ---------------------------------------------------------------------------


def test_build_library_covers_the_requested_boards():
    # Different ranks from BOARD_A (2,7,9) -> genuinely distinct canonical
    # form, not just a suit relabeling of it.
    board_b = tuple(cards("3c 8d Th"))
    library = build_library(**_build_library_kwargs([BOARD_A, board_b]))
    assert len(library) == 2


def test_build_library_dedupes_isomorphic_boards_to_one_solve():
    # 2h 7s 9d and 2c 7d 9h are both rainbow boards with the exact same
    # ranks (2, 7, 9) — genuinely suit-isomorphic (some 4-suit bijection
    # maps one to the other). build_library must recognize this and only
    # solve once.
    board_b = tuple(cards("2c 7d 9h"))
    canonical_a, _ = canonicalize_board(BOARD_A)
    canonical_b, _ = canonicalize_board(board_b)
    assert canonical_a == canonical_b  # sanity: confirms they really are isomorphic

    library = build_library(**_build_library_kwargs([BOARD_A, board_b]))
    assert len(library) == 1


def test_build_library_solves_at_the_canonical_bucketed_stack_depth():
    # STACK_BB=17.0 buckets to 15.0 under the default 5bb bucket.
    library = build_library(**_build_library_kwargs([BOARD_A]))
    (entry,) = library.values()
    assert entry.canonical_stack_bb == pytest.approx(15.0)

    # Cross-check: solving directly at stack_bb=15 (not 17) with matching
    # seed/iterations against the same canonical board/ranges must produce
    # the identical stored strategy — proving it solved at 15, not 17.
    canonical_board, suit_map = canonicalize_board(BOARD_A)
    exclude = frozenset(BOARD_A)
    hero_range = range_from_class_frequencies(HERO_CLASSES, exclude=exclude)
    villain_range = range_from_class_frequencies(VILLAIN_CLASSES, exclude=exclude)
    canonical_hero_range = {translate_combo(c, suit_map): w for c, w in hero_range.items()}
    canonical_villain_range = {translate_combo(c, suit_map): w for c, w in villain_range.items()}
    direct = solve_flop(
        board=canonical_board,
        hero_range=canonical_hero_range,
        villain_range=canonical_villain_range,
        pot=POT,
        effective_stack_bb=15.0,
        raise_sizes=RAISE_SIZES,
        max_raises=MAX_RAISES,
        iterations=ITERATIONS,
        equity_samples=EQUITY_SAMPLES,
        equity_seed=EQUITY_SEED,
    )
    assert entry.strategy == direct.opening_range()


# ---------------------------------------------------------------------------
# save_library / load_library
# ---------------------------------------------------------------------------


def test_save_load_round_trip(tmp_path):
    library = build_library(**_build_library_kwargs([BOARD_A]))
    path = tmp_path / "library.json"
    save_library(library, path)
    loaded = load_library(path)

    assert set(loaded.keys()) == set(library.keys())
    for key, entry in library.items():
        loaded_entry = loaded[key]
        assert loaded_entry.canonical_board == entry.canonical_board
        assert loaded_entry.canonical_stack_bb == pytest.approx(entry.canonical_stack_bb)
        assert loaded_entry.pot == pytest.approx(entry.pot)
        assert loaded_entry.iterations == entry.iterations
        assert set(loaded_entry.strategy.keys()) == set(entry.strategy.keys())
        for combo_key, freqs in entry.strategy.items():
            for action, freq in freqs.items():
                assert loaded_entry.strategy[combo_key][action] == pytest.approx(freq)


def test_save_library_creates_parent_directories(tmp_path):
    library = build_library(**_build_library_kwargs([BOARD_A]))
    path = tmp_path / "nested" / "dir" / "library.json"
    assert not path.parent.exists()
    save_library(library, path)
    assert path.exists()


# ---------------------------------------------------------------------------
# lookup_strategy — the actual point of this milestone: a hit correctly
# serves a real board isomorphic to, but physically different from, the one
# actually solved.
# ---------------------------------------------------------------------------


def test_lookup_strategy_hit_for_a_board_isomorphic_to_but_different_from_the_one_solved():
    library = build_library(**_build_library_kwargs([BOARD_A]))

    board_b = tuple(cards("2c 7d 9h"))  # isomorphic to BOARD_A, physically different
    assert board_b != BOARD_A

    looked_up = lookup_strategy(library, board_b, effective_stack_bb=STACK_BB)
    assert looked_up is not None

    # Independently solve directly against board_b and confirm an exact
    # match (suit relabeling preserves all hand-strength/showdown outcomes
    # exactly, so this is an exact match, not approximate-with-slack).
    exclude = frozenset(board_b)
    hero_range = range_from_class_frequencies(HERO_CLASSES, exclude=exclude)
    villain_range = range_from_class_frequencies(VILLAIN_CLASSES, exclude=exclude)
    direct = solve_flop(
        board=board_b,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=POT,
        effective_stack_bb=15.0,  # the canonical bucket STACK_BB=17.0 maps to
        raise_sizes=RAISE_SIZES,
        max_raises=MAX_RAISES,
        iterations=ITERATIONS,
        equity_samples=EQUITY_SAMPLES,
        equity_seed=EQUITY_SEED,
    )
    direct_strategy = direct.opening_range()

    assert set(looked_up.keys()) == set(direct_strategy.keys())
    for combo_key, freqs in direct_strategy.items():
        for action, freq in freqs.items():
            assert looked_up[combo_key][action] == pytest.approx(freq)


def test_lookup_strategy_miss_for_an_unrelated_board():
    library = build_library(**_build_library_kwargs([BOARD_A]))
    unrelated_board = tuple(cards("3c 8d Th"))  # different ranks, not isomorphic to BOARD_A
    assert lookup_strategy(library, unrelated_board, effective_stack_bb=STACK_BB) is None


def test_lookup_strategy_miss_and_hit_across_a_stack_bucket_boundary():
    library = build_library(**_build_library_kwargs([BOARD_A]))  # built at 17bb -> buckets to 15
    assert lookup_strategy(library, BOARD_A, effective_stack_bb=25.0) is None  # different bucket
    assert lookup_strategy(library, BOARD_A, effective_stack_bb=17.0) is not None  # same bucket, matches build


# ---------------------------------------------------------------------------
# query_strategy — Phase 4: canonicalize-then-lookup, falling back to an
# on-demand solve on a miss and caching the result.
# ---------------------------------------------------------------------------


def test_query_strategy_first_miss_then_hit_on_the_same_board():
    library = {}

    first = query_strategy(library, **_query_kwargs(BOARD_A))
    assert first.hit is False

    second = query_strategy(library, **_query_kwargs(BOARD_A))
    assert second.hit is True
    assert second.strategy == first.strategy

    # Cross-check the stored entry against a fresh direct solve of the
    # CANONICAL board with translated ranges — the same exact,
    # deterministic pattern test_build_library_solves_at_the_canonical_
    # bucketed_stack_depth already uses, and deliberately NOT a direct
    # solve of BOARD_A itself: board-level equity for a flop
    # (remaining_needed=2) is Monte Carlo sampled, and remaining_deck's
    # suit-dependent iteration order means the same equity_seed draws
    # genuinely different specific runouts for two differently-suited
    # (even if isomorphic) boards — so a translated round-trip through
    # canonical space is exactly reproducible against *itself*
    # (deterministic), but not bit-identical to an independently
    # re-seeded fresh solve of a non-canonical real board. Comparing
    # against a solve of the SAME canonical board sidesteps that
    # entirely (identical remaining_deck ordering, identical seed).
    canonical_board, suit_map = canonicalize_board(BOARD_A)
    canonical_stack_bb = canonical_stack_depth(STACK_BB)
    entry = library[(canonical_board, canonical_stack_bb)]

    exclude = frozenset(BOARD_A)
    hero_range = range_from_class_frequencies(HERO_CLASSES, exclude=exclude)
    villain_range = range_from_class_frequencies(VILLAIN_CLASSES, exclude=exclude)
    canonical_hero_range = {translate_combo(c, suit_map): w for c, w in hero_range.items()}
    canonical_villain_range = {translate_combo(c, suit_map): w for c, w in villain_range.items()}
    direct = solve_flop(
        board=canonical_board,
        hero_range=canonical_hero_range,
        villain_range=canonical_villain_range,
        pot=POT,
        effective_stack_bb=15.0,
        raise_sizes=RAISE_SIZES,
        max_raises=MAX_RAISES,
        iterations=ITERATIONS,
        equity_samples=EQUITY_SAMPLES,
        equity_seed=EQUITY_SEED,
    )
    assert entry.strategy == direct.opening_range()


def test_query_strategy_hits_a_board_isomorphic_to_a_previous_miss():
    library = {}
    miss = query_strategy(library, **_query_kwargs(BOARD_A))
    assert miss.hit is False

    board_b = tuple(cards("2c 7d 9h"))  # isomorphic to BOARD_A, physically different
    assert board_b != BOARD_A
    hit = query_strategy(library, **_query_kwargs(board_b))
    assert hit.hit is True

    # board_b is (not by coincidence of this test, but worth being exact
    # about) exactly BOARD_A's own canonical form — canonicalize_board(
    # board_b) is therefore an identity map, so this exact-match
    # cross-check against a fresh direct solve of board_b is safe for
    # the same reason test_query_strategy_first_miss_then_hit_on_the_
    # same_board's own cross-check is: it's really comparing against a
    # solve of the SAME canonical board, not two differently-suited
    # boards sharing one Monte Carlo seed (see that test's comment for
    # why the latter is not bit-exact).
    exclude = frozenset(board_b)
    hero_range = range_from_class_frequencies(HERO_CLASSES, exclude=exclude)
    villain_range = range_from_class_frequencies(VILLAIN_CLASSES, exclude=exclude)
    direct = solve_flop(
        board=board_b,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=POT,
        effective_stack_bb=15.0,
        raise_sizes=RAISE_SIZES,
        max_raises=MAX_RAISES,
        iterations=ITERATIONS,
        equity_samples=EQUITY_SAMPLES,
        equity_seed=EQUITY_SEED,
    )
    direct_strategy = direct.opening_range()
    assert set(hit.strategy.keys()) == set(direct_strategy.keys())
    for combo_key, freqs in direct_strategy.items():
        for action, freq in freqs.items():
            assert hit.strategy[combo_key][action] == pytest.approx(freq)


def test_query_strategy_mutates_the_passed_in_library_object_in_place():
    library = {}
    original_ref = library  # captured before the call, on purpose

    query_strategy(library, **_query_kwargs(BOARD_A))

    # Asserting against original_ref (not a fresh reference to `library`)
    # is the point: it would only pass if query_strategy mutated the
    # caller's own dict object rather than rebinding `library` to a new
    # one internally.
    assert len(original_ref) == 1
    canonical_board, _ = canonicalize_board(BOARD_A)
    assert (canonical_board, 15.0) in original_ref


def test_query_strategy_hit_is_faster_than_miss_on_the_same_spot():
    library = {}
    miss = query_strategy(library, **_query_kwargs(BOARD_A))
    hit = query_strategy(library, **_query_kwargs(BOARD_A))
    assert miss.hit is False
    assert hit.hit is True
    assert hit.elapsed_seconds < miss.elapsed_seconds


def test_query_strategy_ignores_a_changed_pot_on_a_cached_hit():
    library = {}
    first = query_strategy(library, **_query_kwargs(BOARD_A))
    assert first.hit is False

    kwargs = _query_kwargs(BOARD_A)
    kwargs["pot"] = POT * 3
    second = query_strategy(library, **kwargs)

    # A changed pot doesn't trigger a re-solve or an error — pot isn't
    # part of the canonical key, so this still hits and silently
    # returns the first call's (pot=POT) strategy.
    assert second.hit is True
    assert second.strategy == first.strategy


# ---------------------------------------------------------------------------
# query_strategy_from_path — M23: bridges M16's derive_ranges_from_path
# into query_strategy. A small, fast, real preflop pool (module-scoped so
# tests 1/2/5 below don't each pay for their own solve_preflop call).
# ---------------------------------------------------------------------------

_PATH_HANDS = [StartingHand("A", "A"), StartingHand("K", "K"), StartingHand("7", "2", suited=False)]
_PATH_EQUITY_TABLE = build_equity_table(hands=_PATH_HANDS, samples=30)
_PATH_BOARD = tuple(Card.from_str(t) for t in ["2h", "6d", "9c"])


@pytest.fixture(scope="module")
def preflop_pipeline_result():
    config = GameConfig(raise_sizes=(2.5,), max_raises=2)
    return solve_preflop(iterations=300, config=config, hands=_PATH_HANDS, equity_table=_PATH_EQUITY_TABLE)


def _open_then_call_path_scenario(preflop_result):
    root = preflop_result.root
    open_raise = next(a for a in root.legal_actions if a.kind == RAISE)
    bb_node = root.children[open_raise]
    call_action = next(a for a in bb_node.legal_actions if a.kind == CALL_OR_CHECK)
    return derive_ranges_from_path(preflop_result, [open_raise, call_action])


def test_query_strategy_from_path_real_pipeline_end_to_end(preflop_pipeline_result):
    path_scenario = _open_then_call_path_scenario(preflop_pipeline_result)
    # Explicit precondition, not assumed: open-then-call closes the
    # betting round for a heads-up root, reaching a genuine TerminalNode
    # with both original positions still live.
    assert path_scenario.live_positions == ("BTN", "BB")
    assert isinstance(path_scenario.node, TerminalNode)

    result = query_strategy_from_path(
        {},
        preflop_pipeline_result,
        path_scenario,
        _PATH_BOARD,
        raise_sizes=RAISE_SIZES,
        max_raises=MAX_RAISES,
        iterations=ITERATIONS,
        equity_samples=EQUITY_SAMPLES,
        equity_seed=EQUITY_SEED,
    )
    assert result.hit is False
    assert len(result.strategy) > 0
    for freqs in result.strategy.values():
        assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)


def test_query_strategy_from_path_rejects_a_non_terminal_path(preflop_pipeline_result):
    # BB hasn't responded to the open yet — the round isn't closed, so
    # live positions' remaining stacks aren't provably equal.
    root = preflop_pipeline_result.root
    open_raise = next(a for a in root.legal_actions if a.kind == RAISE)
    path_scenario = derive_ranges_from_path(preflop_pipeline_result, [open_raise])

    with pytest.raises(ValueError):
        query_strategy_from_path({}, preflop_pipeline_result, path_scenario, _PATH_BOARD)


def test_query_strategy_from_path_rejects_a_multiway_origin_result():
    config = GameConfig(positions=("BTN", "SB", "BB"))
    root = build_game_tree(config)
    # A stub (node_data={}) is sufficient — the multiway guard fires on
    # result.config.positions alone, before touching solved frequencies.
    stub_result = StrategyResult(config=config, root=root, hands=_PATH_HANDS, node_data={}, iterations=0, elapsed_seconds=0.0)
    path_scenario = derive_ranges_from_path(stub_result, [])

    with pytest.raises(ValueError):
        query_strategy_from_path({}, stub_result, path_scenario, _PATH_BOARD)


def test_query_strategy_from_path_rejects_a_postflop_rooted_result():
    # A real solve_flop result, walked to a genuine TerminalNode (OOP
    # checks, IP checks back) — a valid PathScenario on its own terms,
    # but its ranges are HandCombo-keyed, not the StartingHand-keyed
    # class dicts query_strategy requires.
    exclude = frozenset(BOARD_A)
    hero_range = range_from_class_frequencies(HERO_CLASSES, exclude=exclude)
    villain_range = range_from_class_frequencies(VILLAIN_CLASSES, exclude=exclude)
    flop_result = solve_flop(
        board=BOARD_A,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=POT,
        effective_stack_bb=STACK_BB,
        raise_sizes=RAISE_SIZES,
        max_raises=MAX_RAISES,
        iterations=ITERATIONS,
        equity_samples=EQUITY_SAMPLES,
        equity_seed=EQUITY_SEED,
    )
    root = flop_result.root
    check_action = next(a for a in root.legal_actions if a.kind == CALL_OR_CHECK)
    ip_node = root.children[check_action]
    check_back = next(a for a in ip_node.legal_actions if a.kind == CALL_OR_CHECK)
    path_scenario = derive_ranges_from_path(flop_result, [check_action, check_back])

    with pytest.raises(ValueError):
        query_strategy_from_path({}, flop_result, path_scenario, BOARD_A)


def test_query_strategy_from_path_maps_btn_to_ip_and_bb_to_oop(preflop_pipeline_result):
    path_scenario = _open_then_call_path_scenario(preflop_pipeline_result)
    # Explicit precondition: BTN's (compound open-and-call) range and
    # BB's (single-step) range are real, different distributions — not
    # coincidentally identical, so a backwards mapping would be
    # detectable, not silently masked.
    assert path_scenario.ranges["BTN"] != path_scenario.ranges["BB"]

    via_path = query_strategy_from_path(
        {},
        preflop_pipeline_result,
        path_scenario,
        _PATH_BOARD,
        raise_sizes=RAISE_SIZES,
        max_raises=MAX_RAISES,
        iterations=ITERATIONS,
        equity_samples=EQUITY_SAMPLES,
        equity_seed=EQUITY_SEED,
    )

    # Independent, direct cross-check: BB (button-of-two's opponent, the
    # big-blind-equivalent) is postflop OOP; BTN is IP. A backwards
    # mapping would make this direct call disagree with the wrapper.
    direct = query_strategy(
        {},
        board=_PATH_BOARD,
        hero_classes=path_scenario.ranges["BB"],
        villain_classes=path_scenario.ranges["BTN"],
        pot=path_scenario.pot,
        effective_stack_bb=path_scenario.stacks["BB"],
        positions=("BB", "BTN"),
        raise_sizes=RAISE_SIZES,
        max_raises=MAX_RAISES,
        iterations=ITERATIONS,
        equity_samples=EQUITY_SAMPLES,
        equity_seed=EQUITY_SEED,
    )
    assert via_path.strategy == direct.strategy
