"""Pydantic request/response models for the solver API."""

from pydantic import BaseModel


class SolveResponse(BaseModel):
    stack_bb: float
    iterations: int
    elapsed_seconds: float
    opening_range: dict[str, dict[str, float]]
    position: str
    positions: list[str]


class EquityResponse(BaseModel):
    hand_a: str
    hand_b: str
    board: str
    equity_a: float
    equity_b: float


class FlopSolveResponse(BaseModel):
    board: str
    pot: float
    stack_bb: float
    iterations: int
    elapsed_seconds: float
    strategy: dict[str, dict[str, float]]
    position: str
    positions: list[str]


class FlopQueryResponse(BaseModel):
    board: str
    canonical_board: str
    pot: float
    stack_bb: float
    canonical_stack_bb: float
    hit: bool
    elapsed_seconds: float
    strategy: dict[str, dict[str, float]]
    position: str
    positions: list[str]


class ActionPathRequest(BaseModel):
    """M24's request body — the first request (not just response) model
    in this API. Deliberately no numeric constraints here (e.g. on
    `iterations`) — every existing bound lives on a route's own
    `Query(...)`/`Path(...)` in main.py, which imports this module, so
    a `le=MAX_ITERATIONS`-style constraint here would be circular.
    `stack_bb <= 0` is already caught for free by GameConfig's own
    __post_init__, the same way /solve/{stack_bb} gets it today."""

    stack_bb: float
    action_path: list[str]
    board: str
    iterations: int | None = None


class FlopPathQueryResponse(BaseModel):
    """Deliberately not a reuse of FlopQueryResponse — position/
    positions are the real resolved labels here (e.g. "BB"/["BB",
    "BTN"]), not FlopQueryResponse's hardcoded "OOP"/"IP"; pot is
    genuinely request-varying, not a fixed constant; and stack_bb (the
    preflop starting depth) and effective_stack_bb (what's left behind
    entering the flop, after preflop investment) are two distinct
    numbers that must not be conflated into one field."""

    board: str
    canonical_board: str
    action_path: list[str]
    stack_bb: float
    effective_stack_bb: float
    canonical_stack_bb: float
    pot: float
    hit: bool
    elapsed_seconds: float
    strategy: dict[str, dict[str, float]]
    position: str
    positions: list[str]
