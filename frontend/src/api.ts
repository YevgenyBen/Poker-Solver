import type { SolveResponse } from './types';

export class SolveError extends Error {}

export interface SolveParams {
  /** 2 (heads-up, default) or 3 (3-max demo) — see api/main.py. */
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
  const url = `/solve/${stackBb}${queryString ? `?${queryString}` : ''}`;

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
  return (await response.json()) as SolveResponse;
}
