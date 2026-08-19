import type { EquityResponse, FlopSolveResponse, SolveResponse } from './types';

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

export async function fetchFlopStrategy(
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
  return fetchJson<FlopSolveResponse>(`/solve_flop?${query.toString()}`, signal);
}
