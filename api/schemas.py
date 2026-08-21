"""Pydantic request/response models for the solver API."""

from pydantic import BaseModel


class SolveResponse(BaseModel):
    stack_bb: float
    iterations: int
    elapsed_seconds: float
    opening_range: dict[str, dict[str, float]]
    trained: dict[str, bool]
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
    trained: dict[str, bool]
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
    __post_init__, the same way /solve/{stack_bb} gets it today.

    `players` (M29): defaults to 2 (heads-up, the original behavior) —
    3/6/9 derives the path against that table size's own real tree."""

    stack_bb: float
    action_path: list[str]
    board: str
    iterations: int | None = None
    players: int = 2


class FlopPathQueryResponse(BaseModel):
    """Deliberately not a reuse of FlopQueryResponse — position/
    positions are the real resolved labels here (e.g. "BB"/["BB",
    "BTN"]), not FlopQueryResponse's hardcoded "OOP"/"IP"; pot is
    genuinely request-varying, not a fixed constant; and stack_bb (the
    preflop starting depth) and effective_stack_bb (what's left behind
    entering the flop, after preflop investment) are two distinct
    numbers that must not be conflated into one field.

    `players` (M29) echoes the request's own origin table size —
    `positions` above is always the resolved 2-element (OOP, IP) flop
    pair regardless, so this is the only place in the response that
    says how many players the *hand* actually started with."""

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
    players: int


class PreflopWalkRequest(BaseModel):
    """M25's request body — board-independent, unlike ActionPathRequest:
    a "what's legal from here" query is a pure preflop-tree-state check,
    with no board/flop involved at all. Same no-numeric-constraints-here
    convention as ActionPathRequest, for the same circular-import reason.

    `players` (M29): defaults to 2 (heads-up, the original behavior) —
    3/6/9 walks that table size's own real tree instead. Validated the
    same way GET /solve/{stack_bb}?players=N already is (api/main.py's
    route, not this model — see MULTIWAY_TABLE_CONFIGS)."""

    stack_bb: float
    action_path: list[str]
    iterations: int | None = None
    players: int = 2


class LegalActionOption(BaseModel):
    """One action legal at the node action_path currently resolves to.
    `size` (total commitment) is set for raise/all_in; `to_call` (amount
    owed, 0 meaning a free check) is set for call_or_check; fold sets
    neither. Structured numbers, not a pre-formatted label — matches
    this app's existing division of labor (the frontend formats, e.g.
    FlopSolver.tsx's own .toFixed() calls)."""

    kind: str
    size: float | None = None
    to_call: float | None = None


class PreflopWalkResponse(BaseModel):
    stack_bb: float
    action_path: list[str]
    is_terminal: bool
    player_to_act: str | None
    live_positions: list[str]
    positions: list[str]
    pot: float
    legal_actions: list[LegalActionOption]


class TurnPathRequest(BaseModel):
    """M26's request body — two action paths (preflop, then flop) plus
    a real dealt turn card. Two independent iteration fields, not one:
    `iterations` (the preflop leg) and `turn_iterations` (the
    solve_flop_turn leg) are capped very differently server-side (see
    api/main.py's module docstring for why coupling them would be
    wrong), so both need their own field. Same no-numeric-constraints-
    here convention as ActionPathRequest/PreflopWalkRequest.

    `players` (M29): defaults to 2 (heads-up, the original behavior) —
    3/6/9 derives the preflop leg against that table size's own real
    tree, same as ActionPathRequest/PreflopWalkRequest."""

    stack_bb: float
    preflop_action_path: list[str]
    board: str
    flop_action_path: list[str]
    turn_card: str
    iterations: int | None = None
    turn_iterations: int | None = None
    players: int = 2


class TurnPathQueryResponse(BaseModel):
    board: str
    turn_card: str
    preflop_action_path: list[str]
    flop_action_path: list[str]
    stack_bb: float
    effective_stack_bb: float
    pot: float
    is_terminal: bool
    player_to_act: str | None
    strategy: dict[str, dict[str, float]]
    trained: dict[str, bool]
    position: str
    positions: list[str]
    players: int
    elapsed_seconds: float
