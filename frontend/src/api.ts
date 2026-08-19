import type { EquityResponse, FlopSolveDepth, FlopSolveResponse, SolveResponse } from './types';

/** Thrown for any non-2xx API response, wrapping the server's `detail`
 * message when there is one (used for both /solve and /equity — the
 * name predates /equity, kept as-is rather than churning it). */
export class SolveError extends Error {}

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
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
  return fetchJson<SolveResponse>(`/solve/${stackBb}${queryString ? `?${queryString}` : ''}`, signal);
}

export async function fetchEquity(
  handA: string,
  handB: string,
  board: string,
  signal?: AbortSignal,
): Promise<EquityResponse> {
  const query = new URLSearchParams({ hand_a: handA, hand_b: handB, board });
  return fetchJson<EquityResponse>(`/equity?${query.toString()}`, signal);
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
  return fetchJson<FlopSolveResponse>(`${FLOP_DEPTH_ENDPOINTS[depth]}?${query.toString()}`, signal);
}
