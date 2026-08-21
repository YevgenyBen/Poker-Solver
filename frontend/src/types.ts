// Mirrors api/schemas.py's SolveResponse.
export type ActionFrequencies = Record<string, number>;
export type OpeningRange = Record<string, ActionFrequencies>;

// M28: hand label -> whether that hand's entry in a same-shaped
// OpeningRange reflects real accumulated solving, or the untrained
// uniform-prior default — see poker_solver/solver.py's
// StrategyResult.trained_hands and CLAUDE.md's M28 entry. `false` can
// mean either "MCCFR never sampled this hand at this node" or "this
// hand has zero weight in this position's own range to begin with" —
// both are correctly "don't trust this number," this map doesn't
// distinguish which.
export type TrainedMap = Record<string, boolean>;

export interface SolveResponse {
  stack_bb: number;
  iterations: number;
  elapsed_seconds: number;
  opening_range: OpeningRange;
  trained: TrainedMap;
  position: string;
  positions: string[];
}

// Mirrors api/schemas.py's EquityResponse.
export interface EquityResponse {
  hand_a: string;
  hand_b: string;
  board: string;
  equity_a: number;
  equity_b: number;
}

// Mirrors api/schemas.py's FlopSolveResponse — shared by /solve_flop,
// /solve_flop_turn, and /solve_flop_to_river (M14): same response
// shape at every runout depth, see strategy_format.format_flop_response.
export interface FlopSolveResponse {
  board: string;
  pot: number;
  stack_bb: number;
  iterations: number;
  elapsed_seconds: number;
  strategy: OpeningRange;
  trained: TrainedMap;
  position: string;
  positions: string[];
}

// M14: which of the three /solve_flop* endpoints to call — see api.ts's
// fetchFlopStrategy.
export type FlopSolveDepth = 'flop' | 'flop_turn' | 'flop_to_river';

// M37/M40: which of the three /solve_flop*_multiway endpoints to call —
// see api.ts's fetchMultiwayFlopStrategy. Deliberately its own type
// (not FlopSolveDepth reused) even though the three depth labels now
// match 1:1 — the response shape is identical to FlopSolveResponse
// either way (reused unchanged, per M37's own finding), just with
// `position` legitimately carrying 'MID' too and `positions` holding 3
// entries, which is reason enough to keep this its own named type
// rather than silently coupling the 2-position and multiway endpoint
// families' depth options together.
export type MultiwayFlopSolveDepth = 'flop' | 'flop_turn' | 'flop_to_river';

// Mirrors api/schemas.py's FlopQueryResponse — /solve_flop_cached
// (M22), backed by poker_solver.library.query_strategy (M21). A
// structurally different shape from FlopSolveResponse above, not an
// extension of it: no `iterations` (not meaningful for a cache hit),
// plus `hit`/`canonical_board`/`canonical_stack_bb`, which none of the
// other three endpoints have.
export interface FlopQueryResponse {
  board: string;
  canonical_board: string;
  pot: number;
  stack_bb: number;
  canonical_stack_bb: number;
  hit: boolean;
  elapsed_seconds: number;
  strategy: OpeningRange;
  position: string;
  positions: string[];
}

// Mirrors api/schemas.py's FlopPathQueryResponse — /solve_flop_from_path
// (M24), backed by poker_solver.library.query_strategy_from_path (M23).
// Deliberately not a reuse of FlopQueryResponse above: position/positions
// are the real resolved labels here (e.g. "BB"/["BB","BTN"]), not a
// hardcoded "OOP"/"IP"; pot is genuinely request-varying, not fixed; and
// stack_bb (the preflop starting depth) vs. effective_stack_bb (what's
// left entering the flop, after preflop investment) are two distinct
// numbers.
export interface FlopPathQueryResponse {
  board: string;
  canonical_board: string;
  action_path: string[];
  stack_bb: number;
  effective_stack_bb: number;
  canonical_stack_bb: number;
  pot: number;
  hit: boolean;
  elapsed_seconds: number;
  strategy: OpeningRange;
  position: string;
  positions: string[];
  players: number;
}

// Mirrors api/schemas.py's LegalActionOption. size/to_call use `| null`,
// not `?:` — Pydantic serializes a None field as JSON null, not an
// omitted key (this app sets no exclude_none anywhere), so a missing
// key here would silently never happen and `?:` would be the wrong type.
export interface LegalActionOption {
  kind: string;
  size: number | null;
  to_call: number | null;
}

// Mirrors api/schemas.py's PreflopWalkResponse — /preflop_walk (M25), a
// pure preflop-tree-state query (no board, no CFR strategy) backed by
// api/main.py's _preflop_walk. player_to_act is null exactly when
// is_terminal is true (the hand is over, nobody's left to act).
export interface PreflopWalkResponse {
  stack_bb: number;
  action_path: string[];
  is_terminal: boolean;
  player_to_act: string | null;
  live_positions: string[];
  positions: string[];
  pot: number;
  legal_actions: LegalActionOption[];
}

// Mirrors api/schemas.py's FlopMultiwayPathQueryResponse —
// /solve_flop_multiway_from_path (M42), backed by poker_solver.solver.
// solve_flop_multiway (M35), directly (no canonical library involved,
// unlike FlopPathQueryResponse's own `hit`/`canonical_board`/
// `canonical_stack_bb` — this endpoint has none of those). `positions`
// carries all of a real 3+-live-position path's surviving positions, in
// real postflop acting order, not FlopPathQueryResponse's fixed 2-entry
// pair. `flop_iterations` echoes what was actually used — this
// endpoint's own flop-stage iteration count is real, request-
// controllable input, unlike /solve_flop_from_path's hidden constant.
export interface FlopMultiwayPathQueryResponse {
  board: string;
  action_path: string[];
  stack_bb: number;
  effective_stack_bb: number;
  pot: number;
  flop_iterations: number;
  elapsed_seconds: number;
  strategy: OpeningRange;
  trained: TrainedMap;
  position: string;
  positions: string[];
  players: number;
}

// Mirrors api/schemas.py's TurnMultiwayPathQueryResponse —
// /solve_turn_multiway_from_path (M44), backed by poker_solver.solver.
// solve_flop_turn_multiway (M36) directly, the multiway analog of
// TurnPathQueryResponse below. `positions` carries all of a real
// 3+-live-position path's surviving positions, in real postflop acting
// order, not TurnPathQueryResponse's fixed 2-entry pair.
// `flop_iterations` echoes what was actually used — this endpoint's own
// flop-stage iteration count is real, request-controllable input,
// mirroring FlopMultiwayPathQueryResponse's own identical field.
export interface TurnMultiwayPathQueryResponse {
  board: string;
  turn_card: string;
  preflop_action_path: string[];
  flop_action_path: string[];
  stack_bb: number;
  effective_stack_bb: number;
  pot: number;
  flop_iterations: number;
  is_terminal: boolean;
  player_to_act: string | null;
  strategy: OpeningRange;
  trained: TrainedMap;
  position: string;
  positions: string[];
  players: number;
  elapsed_seconds: number;
}

// Mirrors api/schemas.py's TurnPathQueryResponse — /solve_turn_from_path
// (M26). player_to_act/strategy reflect the turn decision reached after
// preflop_action_path, a real flop board, flop_action_path, and a real
// dealt turn_card; is_terminal covers two distinct real outcomes (a
// fold-out on the flop, or the flop action already left both players
// fully all-in) — strategy is empty ({}) in both.
export interface TurnPathQueryResponse {
  board: string;
  turn_card: string;
  preflop_action_path: string[];
  flop_action_path: string[];
  stack_bb: number;
  effective_stack_bb: number;
  pot: number;
  is_terminal: boolean;
  player_to_act: string | null;
  strategy: OpeningRange;
  trained: TrainedMap;
  position: string;
  positions: string[];
  players: number;
  elapsed_seconds: number;
}
