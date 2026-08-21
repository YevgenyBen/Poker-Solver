import { useState } from 'react';
import { fetchMultiwayFlopStrategy, SolveError } from '../api';
import { gradientFor, sortedEntries } from '../colors';
import type { FlopSolveResponse, MultiwayFlopSolveDepth } from '../types';

// Matches api/main.py's DEFAULT_MULTIWAY_FLOP_BOARD and the two new
// endpoints' own pot=10/stack_bb=40 query-param defaults (M37).
const DEFAULT_BOARD = 'Jh7d2c';
const DEFAULT_POT = 10;
const DEFAULT_STACK_BB = 40;

// M37's own measured numbers (see api/main.py's module docstring) —
// solve_flop_multiway is close to flat across iteration count (a few
// seconds regardless), but solve_flop_turn_multiway genuinely scales
// with it (every iteration can sample a new (terminal, card) pair), so
// its own hint names a real range rather than a single number.
const DEPTH_CONFIG: Record<
  MultiwayFlopSolveDepth,
  { label: string; buttonLabel: string; solvingLabel: string; hint: string }
> = {
  flop: {
    label: 'Flop only',
    buttonLabel: 'Solve flop',
    solvingLabel: 'Solving…',
    hint: 'Runouts averaged at showdown — typically a few seconds.',
  },
  flop_turn: {
    label: 'Flop + turn',
    buttonLabel: 'Solve flop + turn',
    solvingLabel: 'Solving flop + turn (up to about 15 seconds)…',
    hint: 'Chains a real turn betting round in via a sampled chance branch — can take up to about 15 seconds.',
  },
};

// M37: a real 3-max (OOP/MID/IP) multiway flop — a separate component
// from FlopSolver, not a 3rd position bolted onto it, mirroring
// CachedFlopSolver's own "different interaction shape -> separate
// component" precedent (M22): a 3rd live position and a narrower
// (2-, not 3-entry) depth selector are enough of a shape change that
// shoehorning them into FlopSolver's own 2-position form would need
// messy conditional branching throughout.
export function MultiwayFlopSolver() {
  const [board, setBoard] = useState(DEFAULT_BOARD);
  const [pot, setPot] = useState(DEFAULT_POT);
  const [stackBb, setStackBb] = useState(DEFAULT_STACK_BB);
  const [position, setPosition] = useState('OOP');
  const [depth, setDepth] = useState<MultiwayFlopSolveDepth>('flop');
  const [result, setResult] = useState<FlopSolveResponse | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  function handleDepthChange(newDepth: MultiwayFlopSolveDepth) {
    setDepth(newDepth);
    setResult(null);
    setError('');
  }

  async function handleSolve() {
    setError('');
    setLoading(true);
    try {
      const response = await fetchMultiwayFlopStrategy(depth, board, pot, stackBb, position);
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
    <section className="flop-solver multiway-flop-solver">
      <h2>Multiway flop solver (3-max demo)</h2>
      <p className="subtitle">
        A curated OOP/MID/IP range vs. a chosen board — the first true 3+ live position postflop
        solve in this project (see api/main.py's DEMO_MULTIWAY_FLOP_CLASSES), not a full range chart.
      </p>
      <div className="flop-inputs">
        <label>
          Board
          <input value={board} onChange={(event) => setBoard(event.target.value)} placeholder="Jh7d2c" aria-label="Board" />
        </label>
        <label>
          Pot
          <input type="number" value={pot} onChange={(event) => setPot(Number(event.target.value))} aria-label="Pot" />
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
            <option value="MID">MID</option>
            <option value="IP">IP</option>
          </select>
        </label>
        <label>
          Runout depth
          <select
            value={depth}
            onChange={(event) => handleDepthChange(event.target.value as MultiwayFlopSolveDepth)}
            aria-label="Runout depth"
          >
            <option value="flop">Flop only</option>
            <option value="flop_turn">Flop + turn</option>
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
            {result.position}&rsquo;s strategy on {result.board}, pot {result.pot} / {result.stack_bb}bb effective
            — {result.iterations} iterations, {result.elapsed_seconds.toFixed(2)}s ({result.positions.join('/')})
          </p>
          {combos.map((combo) => {
            const freqs = result.strategy[combo];
            const isTrained = result.trained[combo] ?? true;
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
                {!isTrained && (
                  <span
                    className="trained-indicator untrained"
                    title="Not enough data — the untrained default, not a real strategy"
                  >
                    low data
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
