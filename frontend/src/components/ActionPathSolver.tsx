import { useState } from 'react';
import { fetchFlopStrategyFromPath, SolveError } from '../api';
import { gradientFor, sortedEntries } from '../colors';
import type { FlopPathQueryResponse } from '../types';

const DEFAULT_STACK_BB = 100;
const DEFAULT_BOARD = 'Jh7d2c';

// M24: a curated preset selector, not a general step-by-step action-path
// builder. A true general wizard needs its own companion "what's legal
// from here" endpoint plus incremental round-trip state management — real,
// separate scope (this project has never built the maximally-general UI
// in the same milestone as the maximally-general backend; the backend
// here stays fully general regardless, so a future milestone can build
// that wizard without touching the route/schema again).
type PresetId = 'open_call' | 'open_3bet_call' | 'limp_check';

const PRESETS: Record<PresetId, { label: string; actionPath: string[] }> = {
  open_call: { label: 'BTN opens, BB calls', actionPath: ['raise', 'call_or_check'] },
  open_3bet_call: {
    label: 'BTN opens, BB 3-bets, BTN calls',
    actionPath: ['raise', 'raise', 'call_or_check'],
  },
  limp_check: { label: 'BTN limps, BB checks back', actionPath: ['call_or_check', 'call_or_check'] },
};

/** M24: /solve_flop_from_path — a real preflop action sequence (not a
 * fixed demo range) in, flop advice out, chaining poker_solver.solver.
 * derive_ranges_from_path (M16) into poker_solver.library.query_
 * strategy_from_path (M23). Closes the last thing M21/M22/M23 each
 * still listed as remaining: a live endpoint serving a real, user-
 * described situation end to end, not a curated one. */
export function ActionPathSolver() {
  const [preset, setPreset] = useState<PresetId>('open_call');
  const [stackBb, setStackBb] = useState(DEFAULT_STACK_BB);
  const [board, setBoard] = useState(DEFAULT_BOARD);
  const [result, setResult] = useState<FlopPathQueryResponse | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  function handlePresetChange(newPreset: PresetId) {
    setPreset(newPreset);
    // A stale result from a different preset would otherwise sit under
    // a heading (position/pot) that no longer describes it.
    setResult(null);
    setError('');
  }

  async function handleSolve() {
    setError('');
    setLoading(true);
    try {
      const response = await fetchFlopStrategyFromPath(stackBb, PRESETS[preset].actionPath, board);
      setResult(response);
    } catch (err) {
      setResult(null);
      setError(err instanceof SolveError ? err.message : 'Something went wrong');
    } finally {
      setLoading(false);
    }
  }

  const combos = result ? Object.keys(result.strategy).sort() : [];

  return (
    <section className="flop-solver">
      <h2>Action-path flop solver (demo)</h2>
      <p className="subtitle">
        A real preflop action sequence — not a fixed demo range — walked with derive_ranges_from_path and
        handed to query_strategy_from_path. A curated preset selector for now (see api/main.py's own
        module docstring for why the full range is capped to a top-K class subset per side).
      </p>
      <div className="flop-inputs">
        <label>
          Preflop line
          <select
            value={preset}
            onChange={(event) => handlePresetChange(event.target.value as PresetId)}
            aria-label="Preflop line"
          >
            {Object.entries(PRESETS).map(([id, config]) => (
              <option key={id} value={id}>
                {config.label}
              </option>
            ))}
          </select>
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
        <label>
          Board
          <input value={board} onChange={(event) => setBoard(event.target.value)} placeholder="Jh7d2c" aria-label="Board" />
        </label>
        <button type="button" onClick={handleSolve} disabled={loading}>
          {loading ? 'Solving…' : 'Solve'}
        </button>
      </div>

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
            {result.pot} / {result.effective_stack_bb}bb effective (started at {result.stack_bb}bb) —{' '}
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
