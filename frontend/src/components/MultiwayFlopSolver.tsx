import { useState } from 'react';
import { ComboRow } from './ComboRow';
import { fetchMultiwayFlopStrategy, SolveError } from '../api';
import type { FlopSolveResponse, MultiwayFlopSolveDepth } from '../types';

// Matches api/main.py's DEFAULT_MULTIWAY_FLOP_BOARD and the two new
// endpoints' own pot=10/stack_bb=40 query-param defaults (M37).
const DEFAULT_BOARD = 'Jh7d2c';
const DEFAULT_POT = 10;
const DEFAULT_STACK_BB = 40;

// M37/M40's own measured numbers (see api/main.py's module docstring)
// — solve_flop_multiway is close to flat across iteration count (a few
// seconds regardless). solve_flop_turn_multiway and solve_flop_to_
// river_multiway both genuinely scale with iteration count (every
// iteration can sample a new (terminal, card) pair), so their hints
// name a real range rather than a single number — and, surprisingly,
// the river hop is measured *cheaper* than the turn-only hop at every
// iteration count compared (the opposite of the 2-position FlopSolver's
// own flop_turn/flop_to_river relationship, see CLAUDE.md's M39 entry
// for why), so its own worst case is a smaller number than flop_turn's.
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
  flop_to_river: {
    label: 'Flop + turn + river',
    buttonLabel: 'Solve flop + turn + river',
    solvingLabel: 'Solving flop + turn + river (up to about 10 seconds)…',
    hint: 'Chains both turn and river betting in via sampled chance branches — measured cheaper than flop + turn alone, up to about 10 seconds.',
  },
};

// M37/M40: a real 3-max (OOP/MID/IP) multiway flop — a separate
// component from FlopSolver, not a 3rd position bolted onto it,
// mirroring CachedFlopSolver's own "different interaction shape ->
// separate component" precedent (M22): a 3rd live position is enough
// of a shape change on its own that shoehorning it into FlopSolver's
// own 2-position form would need messy conditional branching
// throughout — even though, as of M40, both components' depth
// selectors now offer the same 3 options.
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
            {result.position}&rsquo;s strategy on {result.board}, pot {result.pot} / {result.stack_bb}bb effective
            — {result.iterations} iterations, {result.elapsed_seconds.toFixed(2)}s ({result.positions.join('/')})
          </p>
          {combos.map((combo) => {
            const freqs = result.strategy[combo];
            const isTrained = result.trained[combo] ?? true;
            return <ComboRow key={combo} label={combo} freqs={freqs} trained={isTrained} />;
          })}
        </div>
      )}
    </section>
  );
}
