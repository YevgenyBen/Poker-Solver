import { useState } from 'react';
import { fetchFlopStrategy, SolveError } from '../api';
import { gradientFor, sortedEntries } from '../colors';
import type { FlopSolveDepth, FlopSolveResponse } from '../types';

const DEFAULT_BOARD = 'Jh7d2c';
const DEFAULT_POT = 10;
const DEFAULT_STACK_BB = 40;

// M14: per-depth copy — /solve_flop stays a couple seconds, but
// /solve_flop_turn/-flop_to_river chain in real turn/river betting via a
// chance node (see api/main.py's module docstring) and cost real
// seconds-to-a-minute-plus, board-dependent. These numbers are pinned to
// api/main.py's own MAX_FLOP_TURN_ITERATIONS/MAX_FLOP_TO_RIVER_ITERATIONS
// worst-case findings — keep them in sync if those caps ever change.
const DEPTH_CONFIG: Record<FlopSolveDepth, { label: string; buttonLabel: string; solvingLabel: string; hint: string }> = {
  flop: {
    label: 'Flop only',
    buttonLabel: 'Solve flop',
    solvingLabel: 'Solving…',
    hint: 'Runouts averaged at showdown — typically a couple seconds.',
  },
  flop_turn: {
    label: 'Flop + turn',
    buttonLabel: 'Solve flop + turn',
    solvingLabel: 'Solving flop + turn (up to about a minute)…',
    hint: 'Chains a real turn betting round in via a chance node — can take up to about a minute.',
  },
  flop_to_river: {
    label: 'Flop + turn + river',
    buttonLabel: 'Solve flop + turn + river',
    solvingLabel: 'Solving flop + turn + river (up to about two minutes)…',
    hint: 'Chains both turn and river betting in — can take up to about two minutes, and varies more by board than the other two depths.',
  },
};

/** M11: a flop-only betting round over a curated demo hero/villain
 * range (see api/main.py's DEMO_FLOP_HERO_CLASSES/DEMO_FLOP_VILLAIN_CLASSES) —
 * pick a board, pot, and effective stack, see one position's per-combo
 * strategy. Unlike RangeGrid's 169-cell layout (built for the fixed,
 * always-full 169-class preflop grid), a flop combo range is a much
 * smaller, board-dependent, variable-size set, so this renders it as a
 * plain sorted list instead of forcing it into that grid.
 *
 * M14: a "runout depth" selector picks which of /solve_flop,
 * /solve_flop_turn, or /solve_flop_to_river to call — all three return
 * the same response shape (a flop-level strategy), just computed with
 * progressively more real postflop action baked in and progressively
 * higher latency (see DEPTH_CONFIG above and api/main.py's module
 * docstring for the real measured numbers). One component, not three,
 * since the board/pot/stack/position inputs and per-combo rendering are
 * identical at every depth. */
export function FlopSolver() {
  const [board, setBoard] = useState(DEFAULT_BOARD);
  const [pot, setPot] = useState(DEFAULT_POT);
  const [stackBb, setStackBb] = useState(DEFAULT_STACK_BB);
  const [position, setPosition] = useState('OOP');
  const [depth, setDepth] = useState<FlopSolveDepth>('flop');
  const [result, setResult] = useState<FlopSolveResponse | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  function handleDepthChange(newDepth: FlopSolveDepth) {
    setDepth(newDepth);
    // A stale result/error from a different depth would otherwise sit
    // under a heading that no longer describes it.
    setResult(null);
    setError('');
  }

  async function handleSolve() {
    setError('');
    setLoading(true);
    try {
      const response = await fetchFlopStrategy(depth, board, pot, stackBb, position);
      setResult(response);
    } catch (err) {
      setResult(null);
      setError(err instanceof SolveError ? err.message : 'Something went wrong');
    } finally {
      setLoading(false);
    }
  }

  const combos = result ? Object.keys(result.strategy).sort() : [];
  const config = DEPTH_CONFIG[depth];

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
        <label>
          Runout depth
          <select
            value={depth}
            onChange={(event) => handleDepthChange(event.target.value as FlopSolveDepth)}
            aria-label="Runout depth"
          >
            <option value="flop">Flop only</option>
            <option value="flop_turn">Flop + turn</option>
            <option value="flop_to_river">Flop + turn + river</option>
          </select>
        </label>
        <button type="button" onClick={handleSolve} disabled={loading}>
          {loading ? config.solvingLabel : config.buttonLabel}
        </button>
      </div>

      <p className="depth-hint">{config.hint}</p>

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
