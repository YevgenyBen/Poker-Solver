import { useEffect, useState } from 'react';
import { fetchOpeningRange } from './api';
import type { SolveResponse } from './types';

interface OpeningRangeState {
  data: SolveResponse | null;
  status: string;
  loading: boolean;
}

/** Fetches the opening range for `stackBb`, re-fetching whenever it
 * changes and aborting any still-in-flight request from a previous
 * value. */
export function useOpeningRange(stackBb: number): OpeningRangeState {
  const [data, setData] = useState<SolveResponse | null>(null);
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setStatus('Solving…');

    fetchOpeningRange(stackBb, controller.signal)
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
  }, [stackBb]);

  return { data, status, loading };
}
