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
