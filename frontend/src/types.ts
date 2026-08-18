// Mirrors api/schemas.py's SolveResponse.
export type ActionFrequencies = Record<string, number>;
export type OpeningRange = Record<string, ActionFrequencies>;

export interface SolveResponse {
  stack_bb: number;
  iterations: number;
  elapsed_seconds: number;
  opening_range: OpeningRange;
}
