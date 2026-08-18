import type { SolveResponse } from './types';

export class SolveError extends Error {}

export async function fetchOpeningRange(stackBb: number, signal?: AbortSignal): Promise<SolveResponse> {
  const response = await fetch(`/solve/${stackBb}`, { signal });
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
