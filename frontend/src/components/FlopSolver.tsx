import { useState } from 'react';
import { fetchFlopStrategy, SolveError } from '../api';
import { gradientFor, sortedEntries } from '../colors';
import type { FlopSolveResponse } from '../types';

const DEFAULT_BOARD = 'Jh7d2c';
const DEFAULT_POT = 10;
const DEFAULT_STACK_BB = 40;

/** M11: a flop-only betting round over a curated demo hero/villain
 * range (see api/main.py's DEMO_FLOP_HERO_CLASSES/DEMO_FLOP_VILLAIN_CLASSES) —
 * pick a board, pot, and effective stack, see one position's per-combo
 * strategy. Unlike RangeGrid's 169-cell layout (built for the fixed,
 * always-full 169-class preflop grid), a flop combo range is a much
 * smaller, board-dependent, variable-size set, so this renders it as a
 * plain sorted list instead of forcing it into that grid. */
export function FlopSolver() {
  const [board, setBoard] = useState(DEFAULT_BOARD);
  const [pot, setPot] = useState(DEFAULT_POT);
  const [stackBb, setStackBb] = useState(DEFAULT_STACK_BB);
  const [position, setPosition] = useState('OOP');
  const [result, setResult] = useState<FlopSolveResponse | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSolve() {
    setError('');
    setLoading(true);
    try {
      const response = await fetchFlopStrategy(board, pot, stackBb, position);
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
      <h2>Flop solver (demo)</h2>
      <p className="subtitle">
        A curated hero/villain range vs. a chosen board — not a full range chart, see api/main.py's demo classes.
      </p>
      <div className="flop-inputs">
        <label>
          Board
          <input value={board} onChange={(event) => setBoard(event.target.value)} placeholder="Jh7d2c" aria-label="Board" />
        </label>
        <label>
          Pot
          <input
            type="number"
            value={pot}
            onChange={(event) => setPot(Number(event.target.value))}
            aria-label="Pot"
          />
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
          Position
          <select value={position} onChange={(event) => setPosition(event.target.value)} aria-label="Position">
            <option value="OOP">OOP</option>
            <option value="IP">IP</option>
          </select>
        </label>
        <button type="button" onClick={handleSolve} disabled={loading}>
          {loading ? 'Solving…' : 'Solve flop'}
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
            {result.position}'s strategy on {result.board}, pot {result.pot} / {result.stack_bb}bb effective —{' '}
            {result.iterations} iterations, {result.elapsed_seconds.toFixed(2)}s
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
