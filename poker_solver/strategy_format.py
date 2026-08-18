"""Convert a solver.StrategyResult into a plain JSON-serializable shape.

Kept separate from solver.py so the API layer (api/main.py) has a single
well-defined seam to depend on, rather than reaching into StrategyResult
internals directly.
"""

from .solver import StrategyResult


def format_solve_response(result: StrategyResult) -> dict:
    """v1 response shape: stack depth, solve stats, and BTN's opening
    range (hand -> action -> frequency).

    Only the opening range is exposed for now — matches the project
    plan's v1 frontend scope (BTN RFI only). Other spots (e.g. BB facing
    an open) can be added here later without changing this function's
    contract, once the frontend actually needs them.
    """
    return {
        "stack_bb": result.config.stack_bb,
        "iterations": result.iterations,
        "elapsed_seconds": result.elapsed_seconds,
        "opening_range": result.opening_range(),
    }
