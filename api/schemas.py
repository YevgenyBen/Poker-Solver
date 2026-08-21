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


class MultiwayFlopPathRequest(BaseModel):
    """M42's request body — the multiway analog of ActionPathRequest,
    for a genuine 3+ live-position postflop situation reached by a real
    action path (a 2-survivor path stays served by ActionPathRequest/
    /solve_flop_from_path — see api/main.py's module docstring for why
    this is a deliberately separate endpoint, not a `players`-driven
    branch inside that one). Two independent iteration fields, mirroring
    TurnPathRequest's own preflop/turn split: `iterations` (the preflop
    leg — inert whenever `players != 2`, since _get_or_solve_preflop_raw
    already ignores it there in favor of MULTIWAY_TABLE_CONFIGS' own
    fixed budget, same as every other path-based endpoint) and
    `flop_iterations` (the solve_flop_multiway leg — this endpoint's own
    real cost driver, see api/main.py's module docstring for the
    measured numbers behind its cap). `players` defaults to 3, not 2 —
    unlike every other path-based request model, a 2-player origin can
    never reach a 3+-live-position flop, so 2 is not a meaningful
    default here."""

    stack_bb: float
    action_path: list[str]
    board: str
    iterations: int | None = None
    flop_iterations: int | None = None
    players: int = 3


class FlopMultiwayPathQueryResponse(BaseModel):
    """The multiway analog of FlopPathQueryResponse — no `canonical_
    board`/`canonical_stack_bb`/`hit`, since this endpoint has no
    canonicalized library behind it (query_strategy/query_strategy_
    from_path are both 2-position machinery, see api/main.py's module
    docstring); `positions` carries all of the path's real surviving
    positions (3+), in real postflop acting order, not a fixed 2-entry
    OOP/IP pair. `flop_iterations` echoes what was actually used —
    included here (unlike FlopPathQueryResponse's own fixed, unreported
    PATH_QUERY_ITERATIONS) because this endpoint's own flop-stage
    iteration count is real, request-controllable input, not a hidden
    constant."""

    board: str
    action_path: list[str]
    stack_bb: float
    effective_stack_bb: float
    pot: float
    flop_iterations: int
    elapsed_seconds: float
    strategy: dict[str, dict[str, float]]
    trained: dict[str, bool]
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
