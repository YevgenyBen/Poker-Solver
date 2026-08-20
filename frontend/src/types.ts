// Mirrors api/schemas.py's SolveResponse.
export type ActionFrequencies = Record<string, number>;
export type OpeningRange = Record<string, ActionFrequencies>;

export interface SolveResponse {
  stack_bb: number;
  iterations: number;
  elapsed_seconds: number;
  opening_range: OpeningRange;
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
  position: string;
  positions: string[];
}

// M14: which of the three /solve_flop* endpoints to call — see api.ts's
// fetchFlopStrategy.
export type FlopSolveDepth = 'flop' | 'flop_turn' | 'flop_to_river';

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
  pot: number;
  legal_actions: LegalActionOption[];
}
