import { useState } from 'react';
import { fetchTurnStrategyFromPath, SolveError } from '../api';
import { gradientFor, sortedEntries } from '../colors';
import { usePreflopWalk } from '../usePreflopWalk';
import type { TurnPathQueryResponse } from '../types';

const DEFAULT_STACK_BB = 100;
const DEFAULT_BOARD = 'Jh7d2c';
const DEFAULT_TURN_CARD = '2h';

// Quick-jump shortcuts into the SAME growing preflop path usePreflopWalk
// (M25) already builds interactively — mirrors ActionPathSolver.tsx's
// own PRESETS object (a small, deliberate duplication, not a
// reimplementation of the walking mechanism itself, which this
// component reuses unchanged via usePreflopWalk).
type PresetId = 'open_call' | 'open_3bet_call' | 'limp_check';

const PRESETS: Record<PresetId, { label: string; actionPath: string[] }> = {
  open_call: { label: 'BTN opens, BB calls', actionPath: ['raise', 'call_or_check'] },
  open_3bet_call: {
    label: 'BTN opens, BB 3-bets, BTN calls',
    actionPath: ['raise', 'raise', 'call_or_check'],
  },
  limp_check: { label: 'BTN limps, BB checks back', actionPath: ['call_or_check', 'call_or_check'] },
};

type FlopPresetId =
  | 'check_check'
  | 'check_bet_call'
  | 'check_bet_allin_call'
  | 'check_allin_call'
  | 'bet_call'
  | 'bet_allin_call'
  | 'allin_call'
  | 'bet_fold';

// The 7 real, empirically-enumerated flop-terminal action-kind paths at
// this endpoint's own demo scale (api/main.py's FLOP_TURN_MAX_RAISES=2,
// FLOP_TURN_RAISE_SIZES=(2.5,)), plus one fold-out line (bet_fold) to
// exercise that outcome — a curated set, not a general flop-action
// wizard. The natural, explicitly deferred next step (mirroring
// ActionPathSolver's own M24-then-M25 history: ship curated now,
// generalize once proven live) is an interactive "what's legal on the
// flop from here" walker. Known, stated limitation: this list is
// hardcoded to match those two server constants — if they ever change,
// this list would silently drift.
const FLOP_PRESETS: Record<FlopPresetId, { label: string; actionPath: string[] }> = {
  check_check: { label: 'Check, check', actionPath: ['call_or_check', 'call_or_check'] },
  check_bet_call: { label: 'Check, bet, call', actionPath: ['call_or_check', 'raise', 'call_or_check'] },
  check_bet_allin_call: {
    label: 'Check, bet, raise all-in, call',
    actionPath: ['call_or_check', 'raise', 'all_in', 'call_or_check'],
  },
  check_allin_call: { label: 'Check, all-in, call', actionPath: ['call_or_check', 'all_in', 'call_or_check'] },
  bet_call: { label: 'Bet, call', actionPath: ['raise', 'call_or_check'] },
  bet_allin_call: { label: 'Bet, raise all-in, call', actionPath: ['raise', 'all_in', 'call_or_check'] },
  allin_call: { label: 'All-in, call', actionPath: ['all_in', 'call_or_check'] },
  bet_fold: { label: 'Bet, fold', actionPath: ['raise', 'fold'] },
};

const DEFAULT_FLOP_PRESET: FlopPresetId = 'bet_call';

/** M26: /solve_turn_from_path — real turn-level advice, not just a
 * flop-level number improved by real turn action baked in. Reuses
 * usePreflopWalk (M25) directly for its own preflop leg — not a
 * re-implementation of curated preflop presets, since ActionPathSolver.
 * tsx already generalized that once; duplicating it here would be a
 * real regression against what's already shipped. The flop leg is a
 * curated preset dropdown for now — see FLOP_PRESETS above. */
export function TurnPathSolver() {
  const [stackBb, setStackBb] = useState(DEFAULT_STACK_BB);
  const [actionPath, setActionPath] = useState<string[]>([]);
  const [board, setBoard] = useState(DEFAULT_BOARD);
  const [flopPreset, setFlopPreset] = useState<FlopPresetId>(DEFAULT_FLOP_PRESET);
  const [turnCard, setTurnCard] = useState(DEFAULT_TURN_CARD);
  const [solveResult, setSolveResult] = useState<TurnPathQueryResponse | null>(null);
  const [solveError, setSolveError] = useState('');
  const [solveLoading, setSolveLoading] = useState(false);

  const walk = usePreflopWalk(stackBb, actionPath);

  function clearSolveState() {
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
    setActionPath([]);
    clearSolveState();
  }

  function handlePresetClick(id: PresetId) {
    setActionPath(PRESETS[id].actionPath);
    clearSolveState();
  }

  function handleFlopPresetChange(id: FlopPresetId) {
    setFlopPreset(id);
    clearSolveState();
  }

  async function handleSolve() {
    setSolveError('');
    setSolveLoading(true);
    try {
      const response = await fetchTurnStrategyFromPath(
        stackBb,
        actionPath,
        board,
        FLOP_PRESETS[flopPreset].actionPath,
        turnCard,
      );
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
  // is also terminal, but with only 1 live position (mirrors Action
  // PathSolver.tsx's own identical distinction).
  const isRealTerminal = walkData !== null && walkData.is_terminal && walkData.live_positions.length >= 2;
  const isFoldOut = walkData !== null && walkData.is_terminal && walkData.live_positions.length === 1;
  // Known locally, not inferred from response data — the frontend
  // already controls which flop line it submitted, so it can tell a
  // flop fold-out from an already-all-in terminal directly, rather
  // than guessing from the response's is_terminal/effective_stack_bb.
  const flopPathFoldsOut = FLOP_PRESETS[flopPreset].actionPath.includes('fold');

  return (
    <section className="flop-solver">
      <h2>Turn advisor</h2>
      <p className="subtitle">
        Build a real preflop line, pick a real flop line, deal a real turn card — real advice for the resulting
        turn decision (poker_solver.solver.solve_flop_turn's own chance_data, read live, not a flop-level number
        alone).
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

      <p className="status action-path-trail" aria-label="Preflop action path so far">
        {actionPath.length === 0 ? 'Root' : actionPath.join(' → ')}
      </p>

      {walk.error && (
        <p className="flop-error" role="alert">
          {walk.error}
        </p>
      )}

      {walkData && !walkData.is_terminal && (
        <>
          <p className="status">
            {walkData.player_to_act} to act preflop, pot {walkData.pot}bb
          </p>
          <div className="presets">
            {walkData.legal_actions.map((action) => (
              <button
                key={action.kind}
                type="button"
                onClick={() => handleActionClick(action.kind)}
                disabled={walk.loading}
              >
                {action.kind === 'call_or_check'
                  ? action.to_call === 0
                    ? 'Check'
                    : `Call ${action.to_call}`
                  : action.kind === 'fold'
                    ? 'Fold'
                    : `${action.kind === 'raise' ? 'Raise to' : 'All-in'} ${action.size}`}
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
          Hand's over preflop — {walkData.live_positions[0]} wins the {walkData.pot}bb pot.
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
          <label>
            Flop line
            <select
              value={flopPreset}
              onChange={(event) => handleFlopPresetChange(event.target.value as FlopPresetId)}
              aria-label="Flop line"
            >
              {Object.entries(FLOP_PRESETS).map(([id, config]) => (
                <option key={id} value={id}>
                  {config.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Turn card
            <input
              value={turnCard}
              onChange={(event) => setTurnCard(event.target.value)}
              placeholder="2h"
              aria-label="Turn card"
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

      {solveResult?.is_terminal &&
        (flopPathFoldsOut ? (
          <p className="status">Folded on the flop — no turn decision to make.</p>
        ) : (
          <p className="status">Already all in on the flop — no more decisions, just a showdown.</p>
        ))}

      {solveResult && !solveResult.is_terminal && (
        <div className="flop-result">
          <p className="status">
            {solveResult.player_to_act}'s strategy on {solveResult.board}, turn {solveResult.turn_card}, pot{' '}
            {solveResult.pot} / {solveResult.effective_stack_bb}bb effective —{' '}
            {(solveResult.elapsed_seconds * 1000).toFixed(2)}ms
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
