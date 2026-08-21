import type {
  EquityResponse,
  FlopPathQueryResponse,
  FlopQueryResponse,
  FlopSolveDepth,
  FlopSolveResponse,
  MultiwayFlopSolveDepth,
  PreflopWalkResponse,
  SolveResponse,
  TurnPathQueryResponse,
} from './types';

/** Thrown for any non-2xx API response, wrapping the server's `detail`
 * message when there is one (used for both /solve and /equity — the
 * name predates /equity, kept as-is rather than churning it). */
export class SolveError extends Error {}

// M24: generalized from a bare `signal?: AbortSignal` second param to a
// full RequestInit — /solve_flop_from_path is this app's first POST/
// JSON-body request, and every prior GET-only caller below still
// passes just `{ signal }`, so `fetch(url, init)`'s actual call shape
// is unchanged for them (confirmed: existing tests asserting
// `toHaveBeenCalledWith(url, { signal: undefined })` needed no changes).
async function fetchJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // body wasn't JSON — fall back to the generic message above.
    }
    throw new SolveError(detail);
  }
  return (await response.json()) as T;
}

export interface SolveParams {
  /** 2 (heads-up, default) or 3/6/9 (multiway demo) — see api/main.py. */
  players?: number;
  /** Which position's strategy to fetch; defaults to first-to-act. */
  position?: string;
}

export async function fetchOpeningRange(
  stackBb: number,
  signal?: AbortSignal,
  params?: SolveParams,
): Promise<SolveResponse> {
  const query = new URLSearchParams();
  if (params?.players !== undefined) query.set('players', String(params.players));
  if (params?.position !== undefined) query.set('position', params.position);
  const queryString = query.toString();
  return fetchJson<SolveResponse>(`/solve/${stackBb}${queryString ? `?${queryString}` : ''}`, { signal });
}

export async function fetchEquity(
  handA: string,
  handB: string,
  board: string,
  signal?: AbortSignal,
): Promise<EquityResponse> {
  const query = new URLSearchParams({ hand_a: handA, hand_b: handB, board });
  return fetchJson<EquityResponse>(`/equity?${query.toString()}`, { signal });
}

// M14: one endpoint per runout depth — see api/main.py's module
// docstring for why these are separate routes (a fixed, server-side
// curated demo pool/tree per depth, not a client-controlled one).
const FLOP_DEPTH_ENDPOINTS: Record<FlopSolveDepth, string> = {
  flop: '/solve_flop',
  flop_turn: '/solve_flop_turn',
  flop_to_river: '/solve_flop_to_river',
};

export async function fetchFlopStrategy(
  depth: FlopSolveDepth,
  board: string,
  pot: number,
  stackBb: number,
  position: string,
  signal?: AbortSignal,
): Promise<FlopSolveResponse> {
  const query = new URLSearchParams({
    board,
    pot: String(pot),
    stack_bb: String(stackBb),
    position,
  });
  return fetchJson<FlopSolveResponse>(`${FLOP_DEPTH_ENDPOINTS[depth]}?${query.toString()}`, { signal });
}

// M37: same one-endpoint-per-depth idiom as FLOP_DEPTH_ENDPOINTS above,
// a separate lookup table (not a wider FLOP_DEPTH_ENDPOINTS) since
// MultiwayFlopSolveDepth is its own, narrower type — no 'flop_to_river'
// entry exists here.
const MULTIWAY_FLOP_DEPTH_ENDPOINTS: Record<MultiwayFlopSolveDepth, string> = {
  flop: '/solve_flop_multiway',
  flop_turn: '/solve_flop_turn_multiway',
};

export async function fetchMultiwayFlopStrategy(
  depth: MultiwayFlopSolveDepth,
  board: string,
  pot: number,
  stackBb: number,
  position: string,
  signal?: AbortSignal,
): Promise<FlopSolveResponse> {
  const query = new URLSearchParams({
    board,
    pot: String(pot),
    stack_bb: String(stackBb),
    position,
  });
  return fetchJson<FlopSolveResponse>(`${MULTIWAY_FLOP_DEPTH_ENDPOINTS[depth]}?${query.toString()}`, { signal });
}

// M22: a standalone function, not folded into FLOP_DEPTH_ENDPOINTS/
// FlopSolveDepth above — /solve_flop_cached returns a structurally
// different shape (FlopQueryResponse, not FlopSolveResponse: no `pot`/
// `position` input, a `hit` flag instead of `iterations`) and a
// different interaction (no runout-depth choice), so it gets its own
// component (CachedFlopSolver.tsx) rather than a 4th depth option here.
export async function fetchCachedFlopStrategy(
  board: string,
  stackBb: number,
  signal?: AbortSignal,
): Promise<FlopQueryResponse> {
  const query = new URLSearchParams({ board, stack_bb: String(stackBb) });
  return fetchJson<FlopQueryResponse>(`/solve_flop_cached?${query.toString()}`, { signal });
}

// M24: the first POST/JSON-body request in this API — action_path is a
// variable-length structured sequence, a genuinely awkward fit for
// query params, unlike every GET-based function above.
export async function fetchFlopStrategyFromPath(
  stackBb: number,
  actionPath: string[],
  board: string,
  signal?: AbortSignal,
  players: number = 2,
): Promise<FlopPathQueryResponse> {
  return fetchJson<FlopPathQueryResponse>('/solve_flop_from_path', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stack_bb: stackBb, action_path: actionPath, board, players }),
    signal,
  });
}

// M25: a board-independent, pure preflop-tree-state query — what's
// legal at the node action_path resolves to. POST/JSON-body for the
// same reason fetchFlopStrategyFromPath is: action_path is a
// variable-length structured sequence.
//
// M29: `players` (default 2, heads-up) walks that table size's own
// real tree instead — see api/main.py's PreflopWalkRequest.
export async function fetchPreflopWalk(
  stackBb: number,
  actionPath: string[],
  signal?: AbortSignal,
  players: number = 2,
): Promise<PreflopWalkResponse> {
  return fetchJson<PreflopWalkResponse>('/preflop_walk', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stack_bb: stackBb, action_path: actionPath, players }),
    signal,
  });
}

// M26: real turn-level advice — a preflop path, a real flop board, a
// real flop-line action path, and a real dealt turn card in, the turn
// decision's strategy out.
export async function fetchTurnStrategyFromPath(
  stackBb: number,
  preflopActionPath: string[],
  board: string,
  flopActionPath: string[],
  turnCard: string,
  signal?: AbortSignal,
  players: number = 2,
): Promise<TurnPathQueryResponse> {
  return fetchJson<TurnPathQueryResponse>('/solve_turn_from_path', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      stack_bb: stackBb,
      preflop_action_path: preflopActionPath,
      board,
      flop_action_path: flopActionPath,
      turn_card: turnCard,
      players,
    }),
    signal,
  });
}
