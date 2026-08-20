import { useState } from 'react';
import { fetchCachedFlopStrategy, SolveError } from '../api';
import { shuffleSuits } from '../boardSuits';
import { gradientFor, sortedEntries } from '../colors';
import type { FlopQueryResponse } from '../types';

const DEFAULT_BOARD = 'Jh7d2c';
const DEFAULT_STACK_BB = 40;
// Mirrors api/main.py's FLOP_QUERY_POT — display-only, since pot isn't
// part of query_strategy's cache key and so isn't a query param here
// (see api/main.py's /solve_flop_cached module-docstring paragraph).
const FIXED_POT = 10;

/** M22: /solve_flop_cached — canonicalize-then-lookup, falling back to
 * an on-demand solve on a miss (poker_solver.library.query_strategy,
 * M21). A separate component from FlopSolver, not a 4th runout depth
 * there — this endpoint's response shape (FlopQueryResponse) and
 * interaction (no pot input, no position toggle, a hit/miss indicator
 * instead of an iteration count) differ enough that folding it into
 * FlopSolver's depth selector would need messy conditional branching.
 *
 * The "Shuffle suits" button exists to make the actual point of the
 * whole real-time-speed roadmap (M17-M22) visible, not just ordinary
 * memoization: clicking Solve twice on identical, unmodified text only
 * proves a plain cache hit — indistinguishable from what every other
 * /solve_flop* endpoint's own cache already does. Shuffling first, then
 * solving, hits a board that was never itself queried. */
export function CachedFlopSolver() {
  const [board, setBoard] = useState(DEFAULT_BOARD);
  const [stackBb, setStackBb] = useState(DEFAULT_STACK_BB);
  const [result, setResult] = useState<FlopQueryResponse | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSolve() {
    setError('');
    setLoading(true);
    try {
      const response = await fetchCachedFlopStrategy(board, stackBb);
      setResult(response);
    } catch (err) {
      setResult(null);
      setError(err instanceof SolveError ? err.message : 'Something went wrong');
    } finally {
      setLoading(false);
    }
  }

  function handleShuffle() {
    setBoard((current) => shuffleSuits(current));
  }

  const combos = result ? Object.keys(result.strategy).sort() : [];

  return (
    <section className="flop-solver">
      <h2>Cached flop solver (demo)</h2>
      <p className="subtitle">
        The same demo idea as the flop solver above, but backed by a precomputed-spot cache
        (poker_solver.library.query_strategy) — the first query for a board solves live and caches the
        result; a repeat query, or a query against any board that's merely a suit relabeling of one
        already solved, hits instantly.
      </p>
      <div className="flop-inputs">
        <label>
          Board
          <input value={board} onChange={(event) => setBoard(event.target.value)} placeholder="Jh7d2c" aria-label="Board" />
        </label>
        <label>
          Stack (bb)
          <input
            type="number"
            value={stackBb}
            onChange={(event) => setStackBb(Number(event.target.value))}
            aria-label="Stack (bb)"
          />
        </label>
        <button type="button" onClick={handleSolve} disabled={loading}>
          {loading ? 'Solving…' : 'Solve'}
        </button>
        <button type="button" onClick={handleShuffle} disabled={loading}>
          Shuffle suits
        </button>
      </div>

      <p className="depth-hint">
        Pot is fixed at {FIXED_POT} for this demo (not part of the cache key, so it isn't editable here —
        see api/main.py). "Shuffle suits" relabels the board's suits in place (ranks unchanged) — solving
        afterward still hits the cache if the original board was already solved, since GTO strategy never
        depends on which physical suit is which.
      </p>

      {error && (
        <p className="flop-error" role="alert">
          {error}
        </p>
      )}

      {result && (
        <div className="flop-result">
          <p className="status">
            <span className={`hit-indicator ${result.hit ? 'hit' : 'miss'}`}>
              {result.hit ? 'Cache hit' : 'Solved live'}
            </span>{' '}
            — {result.position}'s strategy on {result.board} (canonically {result.canonical_board}), pot{' '}
            {result.pot} / {result.stack_bb}bb effective (canonically {result.canonical_stack_bb}bb) —{' '}
            {(result.elapsed_seconds * 1000).toFixed(2)}ms
          </p>
          {combos.map((combo) => {
            const freqs = result.strategy[combo];
            const breakdown = sortedEntries(freqs)
              .filter(([, freq]) => freq > 0.005)
              .map(([action, freq]) => `${action} ${(freq * 100).toFixed(0)}%`)
              .join(', ');
            return (
              <div className="detail-row" key={combo}>
                <span className="label">{combo}</span>
                <span className="bar-track">
                  <span className="bar-fill" style={{ width: '100%', background: gradientFor(freqs) }} />
                </span>
                <span className="breakdown">{breakdown}</span>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
