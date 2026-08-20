import { useState } from 'react';
import { fetchFlopStrategyFromPath, SolveError } from '../api';
import { gradientFor, sortedEntries } from '../colors';
import { usePreflopWalk } from '../usePreflopWalk';
import type { FlopPathQueryResponse, LegalActionOption } from '../types';

const DEFAULT_STACK_BB = 100;
const DEFAULT_BOARD = 'Jh7d2c';

// M25: the general step-by-step wizard M24's own docstring (and
// CLAUDE.md's v3 vision) flagged as remaining — action_path is now
// built one legal click at a time against the real tree (POST
// /preflop_walk, M25's own companion endpoint), not chosen from a
// fixed set. Presets survive as one-click shortcuts into the same
// growing path, not the only paths reachable.
type PresetId = 'open_call' | 'open_3bet_call' | 'limp_check';

const PRESETS: Record<PresetId, { label: string; actionPath: string[] }> = {
  open_call: { label: 'BTN opens, BB calls', actionPath: ['raise', 'call_or_check'] },
  open_3bet_call: {
    label: 'BTN opens, BB 3-bets, BTN calls',
    actionPath: ['raise', 'raise', 'call_or_check'],
  },
  limp_check: { label: 'BTN limps, BB checks back', actionPath: ['call_or_check', 'call_or_check'] },
};

/** Client-side label for one legal action — raw numbers come from the
 * server (POST /preflop_walk), formatting stays in the frontend, the
 * same division of labor FlopSolver.tsx's own .toFixed() calls already
 * establish (this app never sends pre-formatted display strings). */
function labelFor(action: LegalActionOption): string {
  switch (action.kind) {
    case 'fold':
      return 'Fold';
    case 'call_or_check':
      return action.to_call === 0 ? 'Check' : `Call ${action.to_call}`;
    case 'raise':
      return `Raise to ${action.size}`;
    case 'all_in':
      return `All-in ${action.size}`;
    default:
      return action.kind;
  }
}

/** M24's /solve_flop_from_path — a real preflop action sequence (not a
 * fixed demo range) in, flop advice out, chaining poker_solver.solver.
 * derive_ranges_from_path (M16) into poker_solver.library.query_
 * strategy_from_path (M23). M25 adds the companion /preflop_walk
 * endpoint, so action_path is built interactively against the real
 * tree instead of only chosen from a curated preset. */
export function ActionPathSolver() {
  const [stackBb, setStackBb] = useState(DEFAULT_STACK_BB);
  const [actionPath, setActionPath] = useState<string[]>([]);
  const [board, setBoard] = useState(DEFAULT_BOARD);
  const [solveResult, setSolveResult] = useState<FlopPathQueryResponse | null>(null);
  const [solveError, setSolveError] = useState('');
  const [solveLoading, setSolveLoading] = useState(false);

  const walk = usePreflopWalk(stackBb, actionPath);

  function clearSolveState() {
    // A stale result/error from a different path would otherwise sit
    // under a heading (position/pot) that no longer describes it.
    setSolveResult(null);
    setSolveError('');
  }

  function handleStackChange(newStack: number) {
    setStackBb(newStack);
    setActionPath([]);
    clearSolveState();
  }

  function handleActionClick(kind: string) {
    setActionPath((path) => [...path, kind]);
    clearSolveState();
  }

  function handleUndo() {
    setActionPath((path) => path.slice(0, -1));
    clearSolveState();
  }

  function handleReset() {
    // A guaranteed escape hatch — a preset can legitimately 422 against
    // /preflop_walk at a shallow enough stack, the same risk this
    // component always had against the solve endpoint, just surfaced
    // earlier/cheaper here.
    setActionPath([]);
    clearSolveState();
  }

  function handlePresetClick(id: PresetId) {
    setActionPath(PRESETS[id].actionPath);
    clearSolveState();
  }

  async function handleSolve() {
    setSolveError('');
    setSolveLoading(true);
    try {
      const response = await fetchFlopStrategyFromPath(stackBb, actionPath, board);
      setSolveResult(response);
    } catch (err) {
      setSolveResult(null);
      setSolveError(err instanceof SolveError ? err.message : 'Something went wrong');
    } finally {
      setSolveLoading(false);
    }
  }

  const combos = solveResult ? Object.keys(solveResult.strategy).sort() : [];
  const walkData = walk.data;
  // A terminal node isn't automatically postflop-eligible — a fold-out
  // is also terminal, but with only 1 live position, which /solve_flop_
  // from_path would 422 on. live_positions disambiguates the two.
  const isRealTerminal = walkData !== null && walkData.is_terminal && walkData.live_positions.length >= 2;
  const isFoldOut = walkData !== null && walkData.is_terminal && walkData.live_positions.length === 1;

  return (
    <section className="flop-solver">
      <h2>Action-path flop solver</h2>
      <p className="subtitle">
        Build a real preflop action sequence one legal click at a time (POST /preflop_walk), then hand it to
        derive_ranges_from_path and query_strategy_from_path for real flop advice.
      </p>
      <div className="flop-inputs">
        <label>
          Stack (bb)
          <input
            type="number"
            value={stackBb}
            onChange={(event) => handleStackChange(Number(event.target.value))}
            aria-label="Stack (bb)"
          />
        </label>
      </div>

      <div className="presets">
        {Object.entries(PRESETS).map(([id, config]) => (
          <button key={id} type="button" onClick={() => handlePresetClick(id as PresetId)} disabled={walk.loading}>
            {config.label}
          </button>
        ))}
      </div>

      <p className="status" aria-label="Action path so far">
        {actionPath.length === 0 ? 'Root' : actionPath.map(pathStepLabel).join(' → ')}
      </p>

      {walk.error && (
        <p className="flop-error" role="alert">
          {walk.error}
        </p>
      )}

      {walkData && !walkData.is_terminal && (
        <>
          <p className="status">
            {walkData.player_to_act} to act, pot {walkData.pot}bb
          </p>
          <div className="presets">
            {walkData.legal_actions.map((action) => (
              <button
                key={action.kind}
                type="button"
                onClick={() => handleActionClick(action.kind)}
                disabled={walk.loading}
              >
                {labelFor(action)}
              </button>
            ))}
          </div>
        </>
      )}

      <div className="presets">
        <button type="button" onClick={handleUndo} disabled={walk.loading || actionPath.length === 0}>
          Undo
        </button>
        <button type="button" onClick={handleReset} disabled={walk.loading || actionPath.length === 0}>
          Reset
        </button>
      </div>

      {isFoldOut && walkData && (
        <p className="status">
          Hand's over — {walkData.live_positions[0]} wins the {walkData.pot}bb pot.
        </p>
      )}

      {isRealTerminal && (
        <div className="flop-inputs">
          <label>
            Board
            <input
              value={board}
              onChange={(event) => setBoard(event.target.value)}
              placeholder="Jh7d2c"
              aria-label="Board"
            />
          </label>
          <button type="button" onClick={handleSolve} disabled={solveLoading}>
            {solveLoading ? 'Solving…' : 'Solve'}
          </button>
        </div>
      )}

      {solveError && (
        <p className="flop-error" role="alert">
          {solveError}
        </p>
      )}

      {solveResult && (
        <div className="flop-result">
          <p className="status">
            <span className={`hit-indicator ${solveResult.hit ? 'hit' : 'miss'}`}>
              {solveResult.hit ? 'Cache hit' : 'Solved live'}
            </span>{' '}
            — {solveResult.position}'s strategy on {solveResult.board} (canonically{' '}
            {solveResult.canonical_board}), pot {solveResult.pot} / {solveResult.effective_stack_bb}bb effective
            (started at {solveResult.stack_bb}bb) — {(solveResult.elapsed_seconds * 1000).toFixed(2)}ms
          </p>
          {combos.map((combo) => {
            const freqs = solveResult.strategy[combo];
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

/** A bare kind string (from actionPath's own history) has no size/
 * to_call attached — unlike labelFor above (which formats a live
 * LegalActionOption from the walk response), this only ever needs to
 * name *which* kind of action was taken, for the breadcrumb trail. */
function pathStepLabel(kind: string): string {
  switch (kind) {
    case 'fold':
      return 'Fold';
    case 'call_or_check':
      return 'Call/Check';
    case 'raise':
      return 'Raise';
    case 'all_in':
      return 'All-in';
    default:
      return kind;
  }
}
