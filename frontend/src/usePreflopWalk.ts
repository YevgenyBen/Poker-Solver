import { useEffect, useState } from 'react';
import { fetchPreflopWalk, SolveError } from './api';
import type { PreflopWalkResponse } from './types';

interface PreflopWalkState {
  data: PreflopWalkResponse | null;
  error: string;
  loading: boolean;
}

// Mirrors useOpeningRange.ts's own effect+abort shape. actionPath is an
// array (an unstable reference every render), so it's flattened to a
// primitive (pathKey) for the effect's dependency array, the same way
// useOpeningRange.ts destructures its params into primitives — an
// array/object dependency would otherwise re-fire the effect every
// render regardless of whether the path actually changed.
//
// Error handling is SolveError-aware (unlike useOpeningRange.ts's
// generic handling) to match ActionPathSolver.tsx's own existing
// convention — a deliberate deviation from the hook this mirrors, kept
// for consistency with its sibling component instead.
export function usePreflopWalk(stackBb: number, actionPath: string[]): PreflopWalkState {
  const [data, setData] = useState<PreflopWalkResponse | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const pathKey = actionPath.join('|');

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    fetchPreflopWalk(stackBb, actionPath, controller.signal)
      .then((response) => {
        setData(response);
      })
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === 'AbortError') return;
        setData(null);
        setError(err instanceof SolveError ? err.message : 'Something went wrong');
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stackBb, pathKey]);

  return { data, error, loading };
}
