"""Pydantic request/response models for the solver API."""

from pydantic import BaseModel, ConfigDict


class _StrictRequest(BaseModel):
    """Every request model rejects fields it does not define (M102).

    Pydantic ignores unknown fields by default, which turns a typo into a
    confident answer to a question nobody asked. Measured against the real
    endpoint before changing it:

        {"hero_card": "AsAh", ...}   -> 200, hero: null
        {"player": 6, ...}           -> 200, players: 2

    The first silently drops the hand the user wanted advice for. The
    second answers a 6-max question with heads-up advice. Neither says
    anything is wrong, and both look exactly like a correct response —
    which is the failure mode this whole codebase keeps hunting.

    `extra="forbid"` turns both into a 422 naming the offending field.
    Safe for the frontend, which builds its body from a TypeScript
    `AdviseRequest` and sends no extras; the cost falls only on callers
    who were already sending something that did nothing.
    """

    model_config = ConfigDict(extra="forbid")


class AdviseRequest(_StrictRequest):
    """M51's request body — ONE shape describing a whole real situation,
    replacing the need to pick among five street-specific endpoints.

    Street depth is INFERRED from which fields are present, mirroring how
    a hand actually unfolds rather than making the client name a street
    it would then have to keep consistent with its own fields:
      * no `board`                              -> preflop
      * `board`                                 -> flop
      * + `flop_action_path` + `turn_card`      -> turn
      * + `turn_action_path` + `river_card`     -> river
    api/main.py's own _infer_street validates that no partial/skipped
    combination sneaks through (e.g. a river card with no turn card).

    `hero_cards` (optional, e.g. "AsKs") asks for YOUR hand's advice
    specifically. It's force-included in every live position's derived
    range BEFORE the cap is applied — without that, a hand outside the
    top-K would silently be absent from the very solve meant to advise
    it, which is exactly the marginal case advice matters most for. The
    response's own `hero.in_range` reports honestly whether your hand
    survived the cap on its own merits or had to be added.

    `iterations` is the preflop leg (inert when players != 2, per
    _get_or_solve_preflop_raw); `solve_iterations` is the postflop leg,
    capped per (street, table size) by whichever sibling endpoint's own
    separately-measured constant applies. Same no-numeric-constraints-
    here convention as every other request model in this file."""

    stack_bb: float
    preflop_action_path: list[str]
    players: int = 2
    board: str | None = None
    flop_action_path: list[str] | None = None
    turn_card: str | None = None
    turn_action_path: list[str] | None = None
    river_card: str | None = None
    # M86: which river decision is being asked about. Absent or empty
    # means the street's first, exactly as flop_action_path (M84) and
    # turn_action_path (M85) mean for theirs. Without it only the opening
    # river decision was reachable, so a player facing a river bet — the
    # single largest decision in a hand — could not ask.
    river_action_path: list[str] | None = None
    hero_cards: str | None = None
    iterations: int | None = None
    solve_iterations: int | None = None


class HeroAdvice(BaseModel):
    """Hero's own hand's advice, present iff `hero_cards` was supplied.

    `in_range` is False when hero's combo did NOT survive the derived
    range's own top-K cap on its own weight and had to be force-included
    — a real, honest quality signal: the surrounding range is still the
    solved one, but hero's own hand was rarer in it than the cap kept,
    so treat the advice as thinner than an in-range hand's.

    `strategy` is None only when the reached node is terminal (nobody
    acts — the hand resolved before this street)."""

    cards: str
    in_range: bool
    strategy: dict[str, float] | None
    trained: bool | None
    # M52. Deliberately a separate field from `trained` above, and easy
    # to conflate: `trained` is about the POSTFLOP solve node this
    # advice was read from; `range_trained` is about the PREFLOP
    # derivation that produced the range fed into that solve. Either can
    # be untrustworthy independently of the other.
    range_trained: bool | None = None


class RangeConfidence(BaseModel):
    """How much of one position's solved-against range was genuinely
    backed by real solving along the preflop path, rather than the
    untrained uniform default (M52, surfacing PathScenario.trained —
    the signal M29 measured, and M29/M42/M44 each deferred exposing).

    Counted over the classes that actually SURVIVED capping, not the
    full derived range: advice is only ever built from what got solved,
    so confidence over discarded classes would dilute the real number.

    `fully_trained: False` means part of the range fed into this solve
    was the untrained default — the advice is built on a partly
    fabricated-looking range and should be weighted accordingly."""

    trained_classes: int
    total_classes: int
    fully_trained: bool


class AdviseResponse(BaseModel):
    """M51's unified response. `trained` is None (not {}) specifically
    when the answer came from the canonical library (`source ==
    "library_hit"`/`"library_miss"`), which persists only a flattened
    strategy dict and structurally cannot report per-hand confidence —
    see M28's own documented scope boundary. Surfaced as an explicit
    null rather than silently omitted, so a caller can tell "no
    confidence data available here" apart from "every hand is trained".

    `source` names which backend actually answered:
      * "exact"        — the exact CFR+ solver (2-position postflop)
      * "mccfr"        — sampled MCCFR (3+ position postflop)
      * "library_hit"  — a canonical-library hit (~0.2ms, no `trained`)
      * "library_miss" — a library miss that solved on demand
      * "preflop"      — read straight off the cached preflop solve
    """

    street: str
    players: int
    positions: list[str]
    position: str
    player_to_act: str | None
    is_terminal: bool
    pot: float
    effective_stack_bb: float
    # M101: the largest TOTAL commitment the acting player can make on
    # this street — the field that makes M95's affordability guarantee
    # checkable, and the reason it needs its own name.
    #
    # `effective_stack_bb` cannot do this job. The audit found it means
    # different things at different nodes: at a street's opening decision
    # it is the money behind entering the street, one decision later it
    # is the SHORTEST remaining stack once someone has bet, and preflop
    # it is the stack net of blinds while preflop sizes are quoted as
    # total commitment. Each reading is defensible; none shares a
    # baseline with the action sizes. So a mid-street flop node can
    # legitimately report `effective_stack_bb: 85.0` next to
    # `all_in:97.50` — both correct, and not comparable.
    #
    # M95 promised no advice ever names a bet the player cannot make.
    # That promise was only verifiable at opening decisions, which is
    # exactly the case its own sweep tested. The invariant now holds
    # everywhere and is asserted at every street:
    #
    #     every size in `strategy` <= `max_affordable_bb`
    max_affordable_bb: float
    # M144/F40: the bet sizes this node's tree could actually offer,
    # ascending. Empty of intermediate sizes on the river at production
    # settings (FLOP_TO_RIVER_RAISE_SIZES is ()), where the only actions
    # are check/call and all-in — so this says what the advice was even
    # able to express, not just what it chose.
    modelled_bet_sizes: list[float] = []
    strategy: dict[str, dict[str, float]]
    trained: dict[str, bool] | None
    hero: HeroAdvice | None
    source: str
    solve_iterations: int | None
    elapsed_seconds: float
    # M52: position -> RangeConfidence. None for the preflop street,
    # which derives no range at all (it reads the full solved 169-class
    # strategy directly, so there's nothing to have been fabricated).
    range_confidence: dict[str, RangeConfidence] | None = None
    # M76: how far this cell's SOLVER can be trusted, independent of
    # whether any particular hand was trained.
    #
    # `trained` and `range_confidence` both answer "did we actually
    # compute this?". Neither answers "is what we computed right?", and
    # the 2026-08-22 diagnostic found a cell where the honest answer is
    # no: 9-max preflop returns `trained: true` on advice that is simply
    # wrong (T7s's top action under the gun comes back as *call*, where
    # correct play folds it near 100% of the time; AA comes back as a
    # 100bb shove). The cause is measured and is not fixable by tuning —
    # iterations divide among seats, so nine positions each get a third
    # of what six do, and raising the budget does not close it (T7s's
    # fold rate runs 0.117 at 3,000 iterations and only 0.301 at 9,000).
    #
    # So the cell is marked rather than silently served. A confidently
    # wrong answer is the worst failure this product can have — worse
    # than a slow one and worse than a refusal — and a caller that cannot
    # tell the difference will present it as fact.
    #
    #   "high"   — trust it (heads-up, and 3-max/6-max preflop)
    #   "low"    — a real solve, but known not to converge at this table
    #              size; present it as a hint, never as GTO
    solver_confidence: str = "high"
    # Present iff solver_confidence != "high": a plain-language reason,
    # so a consumer does not have to look one up.
    solver_confidence_reason: str | None = None

    # M98: confidence in the SIZING axis specifically — which of the
    # non-fold actions to take — as opposed to `solver_confidence`, which
    # is about the answer as a whole.
    #
    # They are separate because a multiway preflop solve is genuinely
    # good at one of its two jobs and genuinely bad at the other, and one
    # number cannot say that. `api/config.py` has recorded the split
    # since M67 and no response ever carried it, so a 6-max player asking
    # "raise or shove?" was answered with the same confidence as one
    # asking "play or fold?".
    #
    #   "high"  — trust the action sizes
    #   "low"   — the fold-vs-play call is sound, but the split among the
    #             non-fold actions moves with the random seed
    sizing_confidence: str = "high"
    sizing_confidence_reason: str | None = None
    # M128: scoped to how often to bet or raise, postflop only. The
    # range a postflop solve models is capped for COST, and sweeping that
    # cap moves a value hand's aggression non-monotonically over a 250x
    # range (0.3% to 77% for a flopped set) with no stable affordable
    # setting. Deliberately separate from `sizing_confidence`, which is
    # preflop and is about which size to pick among the non-fold actions.
    aggression_confidence: str = "high"
    aggression_confidence_reason: str | None = None


class SolveResponse(BaseModel):
    """M125 (E2) added the two confidence fields.

    They existed on `AdviseResponse` alone — one of eleven response
    models — and this is the response that serves `GET /solve/{stack_bb}
    ?players=9`, the 9-max preflop range chart. CLAUDE.md is explicit
    that "9-max preflop output is NOT reliable (M68, measured)" and
    "Don't present 9-max advice as authoritative"; M76 added
    `solver_confidence: "low"` for exactly that, and attached it to one
    endpoint. A caller here got a complete, confident-looking 169-class
    chart of an under-trained solve with nothing in the payload saying
    so — including the frontend's own Preflop Ranges tab, which is the
    most likely way a person ever sees it.

    Same values, same source constants, same meaning as
    `AdviseResponse`'s: `"low"` iff this table size is in
    cfg.LOW_CONFIDENCE_TABLE_SIZES / cfg.SIZING_CAVEAT_TABLE_SIZES, with
    the reason present exactly when the signal fires.
    """

    stack_bb: float
    iterations: int
    elapsed_seconds: float
    opening_range: dict[str, dict[str, float]]
    trained: dict[str, bool]
    position: str
    positions: list[str]
    solver_confidence: str = "high"
    solver_confidence_reason: str | None = None
    sizing_confidence: str = "high"
    sizing_confidence_reason: str | None = None


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


class ActionPathRequest(_StrictRequest):
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


class MultiwayFlopPathRequest(_StrictRequest):
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


class RiverPathRequest(_StrictRequest):
    """M46's request body — the river analog of TurnPathRequest, one
    street further: a real preflop path, a real flop board + flop action
    path, a real dealt turn card, a real TURN action path (new — the
    turn is itself a full betting round, unlike TurnPathQueryResponse's
    own "expose only the first turn decision" scope cut, which needed no
    turn_action_path at all), and a real dealt river card.

    Two independent iteration fields, mirroring TurnPathRequest's own
    preflop/turn split: `iterations` (the preflop leg — same behavior as
    every other path-based request) and `river_iterations` (the
    solve_flop_to_river leg — this endpoint's own real cost driver, far
    steeper even than solve_flop_turn's, see api/main.py's module
    docstring for the measured numbers). Same no-numeric-constraints-
    here convention as ActionPathRequest/TurnPathRequest.

    `players` (mirrors M29's own precedent): defaults to 2 — postflop
    solving here is 2-position only, regardless of origin table size,
    same restriction TurnPathRequest already has."""

    stack_bb: float
    preflop_action_path: list[str]
    board: str
    flop_action_path: list[str]
    turn_card: str
    turn_action_path: list[str]
    river_card: str
    iterations: int | None = None
    river_iterations: int | None = None
    players: int = 2


class RiverPathQueryResponse(BaseModel):
    """Mirrors TurnPathQueryResponse one street further. `river_
    iterations` echoes what was actually used — this endpoint's own
    solve-stage iteration count is real, request-controllable input
    (unlike /solve_flop_from_path's fixed, unreported PATH_QUERY_
    ITERATIONS), mirroring FlopMultiwayPathQueryResponse's/
    TurnMultiwayPathQueryResponse's own identical field."""

    board: str
    turn_card: str
    river_card: str
    preflop_action_path: list[str]
    flop_action_path: list[str]
    turn_action_path: list[str]
    stack_bb: float
    effective_stack_bb: float
    pot: float
    river_iterations: int
    is_terminal: bool
    player_to_act: str | None
    strategy: dict[str, dict[str, float]]
    trained: dict[str, bool]
    position: str
    positions: list[str]
    players: int
    elapsed_seconds: float


class PreflopWalkRequest(_StrictRequest):
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


class TurnPathRequest(_StrictRequest):
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


class MultiwayTurnPathRequest(_StrictRequest):
    """M44's request body — the multiway analog of TurnPathRequest, for
    a preflop path that leaves 3+ live positions at the flop (mirroring
    MultiwayFlopPathRequest's own M42 relationship to ActionPathRequest).
    `flop_iterations`, not `turn_iterations` — matches solve_flop_turn_
    multiway's own single-solve design (M36): unlike the exact 2-position
    solver, there's no SEPARATE turn-stage solve to name a second
    iteration count for; the same solve_flop_turn_multiway call that
    solves the flop also produces the turn strategy this endpoint reads
    back out. `players` defaults to 3, same reasoning as
    MultiwayFlopPathRequest's own default — a 2-player origin can never
    reach a 3+-live-position flop."""

    stack_bb: float
    preflop_action_path: list[str]
    board: str
    flop_action_path: list[str]
    turn_card: str
    iterations: int | None = None
    flop_iterations: int | None = None
    players: int = 3


class TurnMultiwayPathQueryResponse(BaseModel):
    """The multiway analog of TurnPathQueryResponse — `positions` carries
    all of the path's real surviving positions (3+), not a fixed 2-entry
    pair; `flop_iterations` echoes what was actually used, same reasoning
    FlopMultiwayPathQueryResponse's own field already established."""

    board: str
    turn_card: str
    preflop_action_path: list[str]
    flop_action_path: list[str]
    stack_bb: float
    effective_stack_bb: float
    pot: float
    flop_iterations: int
    is_terminal: bool
    player_to_act: str | None
    strategy: dict[str, dict[str, float]]
    trained: dict[str, bool]
    position: str
    positions: list[str]
    players: int
    elapsed_seconds: float
