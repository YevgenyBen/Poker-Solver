import { useEffect, useState } from 'react';
import { fetchOpeningRange, type SolveParams } from './api';
import type { SolveResponse } from './types';

interface OpeningRangeState {
  data: SolveResponse | null;
  status: string;
  loading: boolean;
}

/** Fetches the opening range for `stackBb` (and optional `players`/
 * `position`), re-fetching whenever any of them change and aborting any
 * still-in-flight request from a previous value. */
export function useOpeningRange(stackBb: number, params?: SolveParams): OpeningRangeState {
  const [data, setData] = useState<SolveResponse | null>(null);
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);
  const players = params?.players;
  const position = params?.position;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setStatus('Solving…');

    fetchOpeningRange(stackBb, controller.signal, { players, position })
      .then((response) => {
        setData(response);
        setStatus(`Solved in ${response.elapsed_seconds.toFixed(2)}s (${response.iterations} iterations)`);
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === 'AbortError') return;
        const message = err instanceof Error ? err.message : String(err);
        setStatus(`Error: ${message}`);
      })
      .finally(() => setLoading(false));

    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stackBb, players, position]);

  return { data, status, loading };
}
