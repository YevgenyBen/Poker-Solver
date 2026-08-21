import { useState } from 'react';
import { fetchMultiwayTurnStrategyFromPath, fetchTurnStrategyFromPath, SolveError } from '../api';
import { gradientFor, sortedEntries } from '../colors';
import { MULTIWAY_TABLE_SIZES, type MultiwayTableSize } from '../hands';
import { usePreflopWalk } from '../usePreflopWalk';
import type { TurnMultiwayPathQueryResponse, TurnPathQueryResponse } from '../types';

const DEFAULT_STACK_BB = 100;
const DEFAULT_BOARD = 'Jh7d2c';
const DEFAULT_TURN_CARD = '2h';

// M29: mirrors ActionPathSolver.tsx's own table-size toggle exactly —
// same reasoning for not reusing TableModeControl (no per-position
// browsing concept in a step-by-step wizard).
const TABLE_SIZE_LABELS: Record<MultiwayTableSize, string> = {
  3: '3-max',
  6: '6-max',
  9: '9-max',
};

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
 * curated preset dropdown for now — see FLOP_PRESETS above.
 *
 * M45: FLOP_PRESETS is hand-enumerated against the 2-position solve_
 * flop_turn tree specifically (matching FLOP_TURN_MAX_RAISES/
 * RAISE_SIZES's own values) — a real preflop path can also reach a
 * genuine 3+-live-position flop at any table size >= 3 (mirroring
 * ActionPathSolver.tsx's own M43 fix), which /solve_turn_from_path
 * can't serve. handleSolve routes such a terminal to /solve_turn_
 * multiway_from_path (M44) instead, using the ONE flop line guaranteed
 * structurally valid at any live-position count — everyone checks —
 * rather than trying to hand-enumerate a second FLOP_PRESETS-shaped set
 * per table size (a real, stated scope cut: a general "what's legal on
 * the flop from here" walker, already named as the natural next step by
 * both M26's and M42/M43's own notes, is the eventual fix for this, not
 * attempted here). */
export function TurnPathSolver() {
  const [stackBb, setStackBb] = useState(DEFAULT_STACK_BB);
  const [players, setPlayers] = useState(2);
  const [actionPath, setActionPath] = useState<string[]>([]);
  const [board, setBoard] = useState(DEFAULT_BOARD);
  const [flopPreset, setFlopPreset] = useState<FlopPresetId>(DEFAULT_FLOP_PRESET);
  const [turnCard, setTurnCard] = useState(DEFAULT_TURN_CARD);
  const [solveResult, setSolveResult] = useState<TurnPathQueryResponse | null>(null);
  const [multiwaySolveResult, setMultiwaySolveResult] = useState<TurnMultiwayPathQueryResponse | null>(null);
  const [solveError, setSolveError] = useState('');
  const [solveLoading, setSolveLoading] = useState(false);

  const walk = usePreflopWalk(stackBb, actionPath, players);

  function clearSolveState() {
    setSolveResult(null);
    setMultiwaySolveResult(null);
    setSolveError('');
  }

  function handleStackChange(newStack: number) {
    setStackBb(newStack);
    setActionPath([]);
    clearSolveState();
  }

  function handlePlayersChange(newPlayers: number) {
    // Same reset-on-change discipline as handleStackChange — a preflop
    // path walked against one table size's tree isn't meaningful
    // against a different one.
    setPlayers(newPlayers);
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
      if (isMultiwayTerminal) {
        const response = await fetchMultiwayTurnStrategyFromPath(
          stackBb,
          actionPath,
          board,
          multiwayFlopActionPath,
          turnCard,
          players,
        );
        setMultiwaySolveResult(response);
        setSolveResult(null);
      } else {
        const response = await fetchTurnStrategyFromPath(
          stackBb,
          actionPath,
          board,
          FLOP_PRESETS[flopPreset].actionPath,
          turnCard,
          undefined,
          players,
        );
        setSolveResult(response);
        setMultiwaySolveResult(null);
      }
    } catch (err) {
      setSolveResult(null);
      setMultiwaySolveResult(null);
      setSolveError(err instanceof SolveError ? err.message : 'Something went wrong');
    } finally {
      setSolveLoading(false);
    }
  }

  const combos = solveResult ? Object.keys(solveResult.strategy).sort() : [];
  const multiwayCombos = multiwaySolveResult ? Object.keys(multiwaySolveResult.strategy).sort() : [];
  const walkData = walk.data;
  // A terminal node isn't automatically postflop-eligible — a fold-out
  // is also terminal, but with only 1 live position (mirrors Action
  // PathSolver.tsx's own identical distinction).
  const isRealTerminal = walkData !== null && walkData.is_terminal && walkData.live_positions.length >= 2;
  const isMultiwayTerminal = walkData !== null && walkData.is_terminal && walkData.live_positions.length >= 3;
  const isFoldOut = walkData !== null && walkData.is_terminal && walkData.live_positions.length === 1;
  // M45: the only multiway flop line this component supports — see the
  // component's own top docstring for why (no per-table-size FLOP_
  // PRESETS-shaped set exists yet).
  const multiwayFlopActionPath = walkData ? Array(walkData.live_positions.length).fill('call_or_check') : [];
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

      <div className="players-toggle" role="group" aria-label="Table size">
        <button type="button" className={players === 2 ? 'active' : ''} onClick={() => handlePlayersChange(2)}>
          Heads-up
        </button>
        {MULTIWAY_TABLE_SIZES.map((size) => (
          <button
            key={size}
            type="button"
            className={players === size ? 'active' : ''}
            onClick={() => handlePlayersChange(size)}
          >
            {TABLE_SIZE_LABELS[size]}
          </button>
        ))}
      </div>

      {players === 2 && (
        // Hand-authored against the heads-up (2-position) preflop tree
        // shape — see ActionPathSolver.tsx's identical reasoning for
        // hiding these rather than showing them broken at a 3+ table.
        <div className="presets">
          {Object.entries(PRESETS).map(([id, config]) => (
            <button key={id} type="button" onClick={() => handlePresetClick(id as PresetId)} disabled={walk.loading}>
              {config.label}
            </button>
          ))}
        </div>
      )}

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
          {isMultiwayTerminal ? (
            <p className="depth-hint">
              {walkData?.live_positions.length} live positions reached the flop — this calls
              /solve_turn_multiway_from_path (M44) with everyone checking through (the only multiway flop line
              supported so far), not the curated 2-position flop-line presets below.
            </p>
          ) : (
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
          )}
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
            const isTrained = solveResult.trained[combo] ?? true;
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
                  <span className="trained-indicator untrained" title="Not enough data — the untrained default, not a real strategy">
                    low data
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}

      {multiwaySolveResult?.is_terminal && (
        <p className="status">Already resolved on the flop — no turn decision to make.</p>
      )}

      {multiwaySolveResult && !multiwaySolveResult.is_terminal && (
        <div className="flop-result">
          <p className="status">
            {multiwaySolveResult.player_to_act}'s strategy on {multiwaySolveResult.board}, turn{' '}
            {multiwaySolveResult.turn_card}, pot {multiwaySolveResult.pot} /{' '}
            {multiwaySolveResult.effective_stack_bb}bb effective — {multiwaySolveResult.flop_iterations}{' '}
            iterations, {multiwaySolveResult.elapsed_seconds.toFixed(2)}s ({multiwaySolveResult.positions.join('/')})
          </p>
          {multiwayCombos.map((combo) => {
            const freqs = multiwaySolveResult.strategy[combo];
            const isTrained = multiwaySolveResult.trained[combo] ?? true;
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
