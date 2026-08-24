"""Convert a solver.StrategyResult into a plain JSON-serializable shape.

Kept separate from solver.py so the API layer (api/main.py) has a single
well-defined seam to depend on, rather than reaching into StrategyResult
internals directly.
"""

from .solver import StrategyResult


def format_solve_response(result: StrategyResult, position: str | None = None) -> dict:
    """Response shape: stack depth, solve stats, and one position's
    strategy the first time it's their turn assuming no raise yet (hand
    -> action -> frequency) — see StrategyResult.strategy_for_position.

    `position` defaults to the first-to-act position (result.root's
    player), matching the original BTN-RFI-only v1 response exactly —
    added for M8 so 3+ player results can expose every position's
    strategy, not just the first to act. `positions` is always included
    so a caller (the frontend) knows what else it could ask for.

    `trained` (M28) is `opening_range`'s exact confidence counterpart,
    hand-for-hand: `False` means that hand's `opening_range` entry is
    the untrained uniform-prior fallback, not a real converged answer —
    see StrategyResult.trained_for_position. Always all-`True` for the
    heads-up (players=2) exact solver, which visits every hand
    exhaustively; genuinely mixed for a multiway (players=3/6/9) MCCFR
    result, which only visits what a sampled path actually reaches —
    the exact gap docs/full-table-diagnostic-2026-08.md's SS3.3 named as
    otherwise invisible to a caller of this endpoint.
    """
    chosen_position = position or result.root.player_to_act
    return {
        "stack_bb": result.config.stack_bb,
        "iterations": result.iterations,
        "elapsed_seconds": result.elapsed_seconds,
        "opening_range": result.strategy_for_position(chosen_position),
        "trained": result.trained_for_position(chosen_position),
        "position": chosen_position,
        "positions": list(result.config.positions),
    }


def format_flop_response(result: StrategyResult, board: str, position: str | None = None) -> dict:
    """Same idea as format_solve_response, for a flop StrategyResult
    (M11) — a separate function, not a shared one with an extra optional
    param, since the two response shapes genuinely differ (a flop result
    has a board and an entering pot; a preflop result has neither, and
    a flop result's "opening_range" is one street's combo-level strategy,
    not a class-level RFI range).

    Shape-agnostic across solve *depth*, not just usable for M11's
    flop-only solve_flop: works identically for solve_flop_turn (M12)
    and solve_flop_to_river (M13) results too (M14 wires both up live)
    — every field this function reads (config.pot/.stack_bb,
    strategy_for_position(), root.player_to_act, config.positions) is
    present the same way on any of the three, since all three still
    only ever expose the *flop*-level strategy through this function.
    A deeper solve's chance_data (the turn/river tree) is simply
    invisible here — the response never reveals which of the three
    depths actually produced it.

    `board` is passed in as the caller's own display string rather than
    re-derived from `result` — `StreetConfig` doesn't carry the actual
    board cards (only combos.py/board_equity.py ever see them, to solve
    for the right equity_table), so the caller (who parsed the board to
    call solve_flop in the first place) is the only one who still has it.

    `trained` (M28), same field/meaning as format_solve_response's own —
    included here too for one uniform response shape across every
    solving endpoint.

    **It is genuinely mixed today, and the forward-compatibility this
    was originally written for has arrived (corrected M122).** The
    docstring used to say every postflop solve was heads-up and exact,
    "so this is currently always all-`True` in practice", with multiway
    postflop named as a still-unscoped future gap. That gap closed in
    M35 (`solve_flop_multiway`, sampled MCCFR): a three-position flop
    solve formatted through this function returns `trained` containing
    both `False` and `True`, because MCCFR only visits what a sampled
    path actually reaches. A reader trusting the old text would conclude
    the field was decorative here and could be dropped — the precise
    mistake CLAUDE.md warns against, since `trained` exists because
    output can look confident and be fabricated.
    """
    chosen_position = position or result.root.player_to_act
    return {
        "board": board,
        "pot": result.config.pot,
        "stack_bb": result.config.stack_bb,
        "iterations": result.iterations,
        "elapsed_seconds": result.elapsed_seconds,
        "strategy": result.strategy_for_position(chosen_position),
        "trained": result.trained_for_position(chosen_position),
        "position": chosen_position,
        "positions": list(result.config.positions),
    }
