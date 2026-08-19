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
    """
    chosen_position = position or result.root.player_to_act
    return {
        "stack_bb": result.config.stack_bb,
        "iterations": result.iterations,
        "elapsed_seconds": result.elapsed_seconds,
        "opening_range": result.strategy_for_position(chosen_position),
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

    `board` is passed in as the caller's own display string rather than
    re-derived from `result` — `StreetConfig` doesn't carry the actual
    board cards (only combos.py/board_equity.py ever see them, to solve
    for the right equity_table), so the caller (who parsed the board to
    call solve_flop in the first place) is the only one who still has it.
    """
    chosen_position = position or result.root.player_to_act
    return {
        "board": board,
        "pot": result.config.pot,
        "stack_bb": result.config.stack_bb,
        "iterations": result.iterations,
        "elapsed_seconds": result.elapsed_seconds,
        "strategy": result.strategy_for_position(chosen_position),
        "position": chosen_position,
        "positions": list(result.config.positions),
    }
