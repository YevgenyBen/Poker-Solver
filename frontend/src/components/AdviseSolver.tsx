import { useState } from 'react';
import { ComboRow } from './ComboRow';
import { fetchAdvice, SolveError } from '../api';
import { MULTIWAY_TABLE_SIZES, type MultiwayTableSize } from '../hands';
import { usePreflopWalk } from '../usePreflopWalk';
import type { AdviseRequest, AdviseResponse, LegalActionOption } from '../types';

const DEFAULT_STACK_BB = 100;
const DEFAULT_BOARD = 'Jh7d2c';
const DEFAULT_TURN_CARD = 'Ts';
const DEFAULT_RIVER_CARD = '4h';

const TABLE_SIZE_LABELS: Record<MultiwayTableSize, string> = { 3: '3-max', 6: '6-max', 9: '9-max' };

/** How far the hand actually went. Maps 1:1 onto /advise's own street
 * inference (see api/schemas.py's AdviseRequest): the UI simply omits
 * the fields for streets that haven't happened, and the server infers
 * the rest. Chosen over progressive disclosure because "how far did
 * this hand go" is the question a user can answer up front, whereas
 * revealing inputs one at a time hides what's still to come. */
type Street = 'preflop' | 'flop' | 'turn' | 'river';

const STREETS: { id: Street; label: string }[] = [
  { id: 'preflop', label: 'Preflop' },
  { id: 'flop', label: 'Flop' },
  { id: 'turn', label: 'Turn' },
  { id: 'river', label: 'River' },
];

/** Postflop action lines, kept deliberately generic so they stay legal
 * at ANY live-position count — unlike TurnPathSolver's own curated
 * FLOP_PRESETS, which are hand-enumerated against the 2-position tree
 * and would silently drift at 3+. "Everyone checks" and "one bet, all
 * call" are structurally valid whatever N is, which is what lets this
 * one component serve every table size. A general "what's legal on this
 * street from here" walker is the real fix (named as an open gap since
 * M26) and would replace this. */
type LineId = 'checked' | 'bet_called';

/** M94: WHICH decision on the street being asked about.
 *
 * The LINES above close a street so the hand can advance to the next
 * one. This is the other half, and it was missing: a PARTIAL line naming
 * the decision you actually face. M84-M89 made every decision reachable
 * through /advise — a player facing a bet on the flop, the turn or the
 * river — and the UI had no way to express any of it, so it could only
 * ever ask about each street's opening decision.
 *
 * Deliberately three fixed options rather than a free-form action
 * builder: they cover what a player actually needs to describe ("it's on
 * me", "they checked", "they bet"), stay legal at any live-position
 * count, and need no legal-action walker. */
type SpotId = 'first' | 'after_check' | 'facing_bet';

const SPOTS: Record<SpotId, { label: string; build: () => string[] }> = {
  first: { label: "I'm first to act", build: () => [] },
  after_check: { label: 'They checked to me', build: () => ['call_or_check'] },
  facing_bet: { label: "I'm facing a bet", build: () => ['raise'] },
};

const LINES: Record<LineId, { label: string; build: (liveCount: number) => string[] }> = {
  checked: {
    label: 'Checked through',
    build: (n) => Array(n).fill('call_or_check'),
  },
  bet_called: {
    label: 'Bet, everyone called',
    build: (n) => ['raise', ...Array(Math.max(n - 1, 1)).fill('call_or_check')],
  },
};

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

const SOURCE_LABELS: Record<string, string> = {
  preflop: 'Preflop solve',
  exact: 'Exact solver',
  mccfr: 'MCCFR (sampled)',
  library_hit: 'Cache hit',
  library_miss: 'Solved live',
};

/** M56: the UI for POST /advise — the unified front door (M51-M53).
 * Deliberately one component rather than a 4th/5th sibling of
 * FlopSolver/TurnPathSolver: the whole point of /advise is that street
 * depth and table size are ONE request shape, so splitting the UI back
 * apart per street would reintroduce exactly the sprawl M50/M51
 * consolidated away on the server.
 *
 * Shows three things no other page in this app does: hero's OWN hand's
 * advice (the actual product question), which backend answered
 * (`source`), and how much of the solved-against range was real rather
 * than the untrained default (`range_confidence`, M52). */
export function AdviseSolver() {
  const [stackBb, setStackBb] = useState(DEFAULT_STACK_BB);
  const [players, setPlayers] = useState(2);
  const [actionPath, setActionPath] = useState<string[]>([]);
  // M94: which decision on the CURRENT street is being asked about.
  const [spot, setSpot] = useState<SpotId>('first');
  const [street, setStreet] = useState<Street>('preflop');
  const [heroCards, setHeroCards] = useState('');
  const [board, setBoard] = useState(DEFAULT_BOARD);
  const [flopLine, setFlopLine] = useState<LineId>('checked');
  const [turnCard, setTurnCard] = useState(DEFAULT_TURN_CARD);
  const [turnLine, setTurnLine] = useState<LineId>('checked');
  const [riverCard, setRiverCard] = useState(DEFAULT_RIVER_CARD);
  const [result, setResult] = useState<AdviseResponse | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const walk = usePreflopWalk(stackBb, actionPath, players);
  const walkData = walk.data;

  function clearResult() {
    setResult(null);
    setError('');
  }

  function resetPath(next: () => void) {
    next();
    setActionPath([]);
    clearResult();
  }

  async function handleSolve() {
    setError('');
    setLoading(true);
    try {
      const liveCount = walkData?.live_positions.length ?? 2;
      const request: AdviseRequest = {
        stack_bb: stackBb,
        preflop_action_path: actionPath,
        players,
        ...(heroCards.trim() ? { hero_cards: heroCards.trim() } : {}),
        ...(street !== 'preflop' ? { board } : {}),
        ...(street === 'turn' || street === 'river'
          ? { flop_action_path: LINES[flopLine].build(liveCount), turn_card: turnCard }
          : {}),
        ...(street === 'river'
          ? { turn_action_path: LINES[turnLine].build(liveCount), river_card: riverCard }
          : {}),
        // M94: the partial line for the street being ASKED about — the
        // decision the player actually faces. Earlier streets use the
        // CLOSING lines above; this one must not close, or there would be
        // no decision left on it. Omitted entirely for 'first', which is
        // what an absent path already means.
        ...(street === 'flop' && spot !== 'first'
          ? { flop_action_path: SPOTS[spot].build() }
          : {}),
        ...(street === 'turn' && spot !== 'first'
          ? { turn_action_path: SPOTS[spot].build() }
          : {}),
        ...(street === 'river' && spot !== 'first'
          ? { river_action_path: SPOTS[spot].build() }
          : {}),
      };
      setResult(await fetchAdvice(request));
    } catch (err) {
      setResult(null);
      setError(err instanceof SolveError ? err.message : 'Something went wrong');
    } finally {
      setLoading(false);
    }
  }

  // Preflop advice needs a node with someone still to act; every
  // postflop street needs the preflop action to have CLOSED first with
  // 2+ players live. The exact inversion /advise itself enforces.
  const preflopStillOpen = walkData !== null && !walkData.is_terminal;
  const realTerminal = walkData !== null && walkData.is_terminal && walkData.live_positions.length >= 2;
  const foldedOut = walkData !== null && walkData.is_terminal && walkData.live_positions.length === 1;
  const canSolve = street === 'preflop' ? preflopStillOpen : realTerminal;

  const combos = result ? Object.keys(result.strategy).sort() : [];

  return (
    <section className="flop-solver">
      <h2>Advisor</h2>
      <p className="subtitle">
        Describe a real situation — your cards, the board, the action so far — and get GTO advice for the
        decision you actually face. One request (POST /advise) covering every street and table size.
      </p>

      <div className="flop-inputs">
        <label>
          Stack (bb)
          <input
            type="number"
            value={stackBb}
            onChange={(event) => resetPath(() => setStackBb(Number(event.target.value)))}
            aria-label="Stack (bb)"
          />
        </label>
        <label>
          Your cards
          <input
            value={heroCards}
            onChange={(event) => {
              setHeroCards(event.target.value);
              clearResult();
            }}
            placeholder="AsKs (optional)"
            aria-label="Your cards"
          />
        </label>
      </div>

      <div className="players-toggle" role="group" aria-label="Table size">
        <button type="button" className={players === 2 ? 'active' : ''} onClick={() => resetPath(() => setPlayers(2))}>
          Heads-up
        </button>
        {MULTIWAY_TABLE_SIZES.map((size) => (
          <button
            key={size}
            type="button"
            className={players === size ? 'active' : ''}
            onClick={() => resetPath(() => setPlayers(size))}
          >
            {TABLE_SIZE_LABELS[size]}
          </button>
        ))}
      </div>

      <div className="players-toggle" role="group" aria-label="Street">
        {STREETS.map((option) => (
          <button
            key={option.id}
            type="button"
            className={street === option.id ? 'active' : ''}
            onClick={() => {
              setStreet(option.id);
              clearResult();
            }}
          >
            {option.label}
          </button>
        ))}
      </div>

      <p className="status action-path-trail" aria-label="Preflop action so far">
        Preflop: {actionPath.length === 0 ? 'nothing yet' : actionPath.join(' → ')}
      </p>

      {walk.error && (
        <p className="flop-error" role="alert">
          {walk.error}
        </p>
      )}

      {preflopStillOpen && walkData && (
        <>
          <p className="status">
            {walkData.player_to_act} to act, pot {walkData.pot}bb
          </p>
          <div className="presets">
            {walkData.legal_actions.map((action) => (
              <button
                key={action.kind}
                type="button"
                disabled={walk.loading}
                onClick={() => {
                  setActionPath((path) => [...path, action.kind]);
                  clearResult();
                }}
              >
                {labelFor(action)}
              </button>
            ))}
          </div>
        </>
      )}

      <div className="presets">
        <button
          type="button"
          disabled={walk.loading || actionPath.length === 0}
          onClick={() => {
            setActionPath((path) => path.slice(0, -1));
            clearResult();
          }}
        >
          Undo
        </button>
        <button
          type="button"
          disabled={walk.loading || actionPath.length === 0}
          onClick={() => {
            setActionPath([]);
            clearResult();
          }}
        >
          Reset
        </button>
      </div>

      {foldedOut && walkData && (
        <p className="status">Hand&rsquo;s over — {walkData.live_positions[0]} wins the {walkData.pot}bb pot.</p>
      )}

      {street === 'preflop' && realTerminal && (
        <p className="depth-hint">
          The preflop action has closed, so there&rsquo;s no preflop decision left to advise. Pick a later street,
          or undo a step.
        </p>
      )}

      {street !== 'preflop' && realTerminal && (
        <div className="flop-inputs">
          <label>
            Board
            <input
              value={board}
              onChange={(event) => {
                setBoard(event.target.value);
                clearResult();
              }}
              placeholder="Jh7d2c"
              aria-label="Board"
            />
          </label>
          {/* M94: which decision on THIS street. Without it the UI could
              only ever ask about the street's opening decision, even
              though /advise has answered any of them since M84-M89. */}
          <label>
            Your spot
            <select
              value={spot}
              onChange={(event) => {
                setSpot(event.target.value as SpotId);
                clearResult();
              }}
              aria-label="Your spot"
            >
              {Object.entries(SPOTS).map(([id, option]) => (
                <option key={id} value={id}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          {(street === 'turn' || street === 'river') && (
            <>
              <label>
                Flop line
                <select
                  value={flopLine}
                  onChange={(event) => {
                    setFlopLine(event.target.value as LineId);
                    clearResult();
                  }}
                  aria-label="Flop line"
                >
                  {Object.entries(LINES).map(([id, line]) => (
                    <option key={id} value={id}>
                      {line.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Turn card
                <input
                  value={turnCard}
                  onChange={(event) => {
                    setTurnCard(event.target.value);
                    clearResult();
                  }}
                  placeholder="Ts"
                  aria-label="Turn card"
                />
              </label>
            </>
          )}
          {street === 'river' && (
            <>
              <label>
                Turn line
                <select
                  value={turnLine}
                  onChange={(event) => {
                    setTurnLine(event.target.value as LineId);
                    clearResult();
                  }}
                  aria-label="Turn line"
                >
                  {Object.entries(LINES).map(([id, line]) => (
                    <option key={id} value={id}>
                      {line.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                River card
                <input
                  value={riverCard}
                  onChange={(event) => {
                    setRiverCard(event.target.value);
                    clearResult();
                  }}
                  placeholder="4h"
                  aria-label="River card"
                />
              </label>
            </>
          )}
        </div>
      )}

      {canSolve && (
        <div className="flop-inputs">
          <button type="button" onClick={handleSolve} disabled={loading}>
            {loading ? 'Solving…' : 'Get advice'}
          </button>
        </div>
      )}

      {error && (
        <p className="flop-error" role="alert">
          {error}
        </p>
      )}

      {result && (
        <div className="flop-result">
          <p className="status">
            <span className={`hit-indicator ${result.source === 'library_hit' ? 'hit' : 'miss'}`}>
              {SOURCE_LABELS[result.source] ?? result.source}
            </span>{' '}
            — {result.street}, {result.player_to_act ?? 'no one'} to act, pot {result.pot} /{' '}
            {result.effective_stack_bb}bb effective ({result.positions.join('/')}) —{' '}
            {result.elapsed_seconds.toFixed(2)}s
          </p>

          {result.hero && (
            <div className="hero-advice">
              <h3>Your hand: {result.hero.cards}</h3>
              {result.hero.strategy ? (
                <>
                  <ComboRow freqs={result.hero.strategy} trained={result.hero.trained !== false} />
                  {/* M83: the most direct honesty signal there is — whether
                      YOUR hand's numbers came from real solving or are the
                      uniform placeholder. It was computed all along and
                      never shown, so a placeholder rendered identically to
                      a solved strategy. Louder than the hints below because
                      those qualify a real answer; this one says there
                      isn't one. */}
                  {result.hero.trained === false && (
                    <p className="solver-warning" role="alert">
                      <strong>Not solved for your hand.</strong> These numbers are the untrained
                      default, not real advice — the solver never reached your hand at this node.
                    </p>
                  )}
                  {!result.hero.in_range && (
                    <p className="depth-hint">
                      Your hand wasn&rsquo;t in the top of the derived range — it was added so it could be solved
                      for, so treat this as thinner than an in-range hand&rsquo;s advice.
                    </p>
                  )}
                  {result.hero.range_trained === false && (
                    <p className="depth-hint">
                      The preflop derivation for your hand&rsquo;s class wasn&rsquo;t fully backed by real solving.
                    </p>
                  )}
                </>
              ) : result.is_terminal ? (
                <p className="status">No decision to make here — the hand resolved before this street.</p>
              ) : (
                /* M83: this branch used to show the "hand resolved" message
                   too, which was simply untrue. A null hero strategy at a
                   LIVE node means the opposite: there is a decision, we
                   just have nothing to say about this particular hand. */
                <p className="solver-warning" role="alert">
                  <strong>No advice for this hand.</strong> There is a decision here, but your hand
                  wasn&rsquo;t part of the solved range — so we have nothing to tell you about it.
                </p>
              )}
            </div>
          )}

          {result.solver_confidence === 'low' && (
            <p className="solver-warning" role="alert">
              <strong>Low confidence.</strong>{' '}
              {result.solver_confidence_reason ??
                'This table size is known not to converge — treat the advice as a hint, not GTO.'}
            </p>
          )}

          {result.sizing_confidence === 'low' && (
            <p className="solver-warning" role="alert">
              <strong>Sizes are unreliable here.</strong>{' '}
              {result.sizing_confidence_reason ??
                'The fold-vs-play call is sound, but the split among the non-fold actions moves with the random seed.'}
            </p>
          )}

          {result.range_confidence && (
            <p className="depth-hint">
              Range confidence:{' '}
              {Object.entries(result.range_confidence)
                .map(([pos, c]) => `${pos} ${c.trained_classes}/${c.total_classes}`)
                .join(' · ')}
              {Object.values(result.range_confidence).some((c) => !c.fully_trained) &&
                ' — part of the solved-against range was the untrained default.'}
            </p>
          )}

          {result.trained === null && (
            <p className="depth-hint">
              Per-hand confidence isn&rsquo;t available for a cached answer (the library stores only the strategy).
            </p>
          )}

          {combos.map((combo) => {
            const freqs = result.strategy[combo];
            const isTrained = result.trained?.[combo] ?? true;
            return <ComboRow key={combo} label={combo} freqs={freqs} trained={isTrained} />;
          })}
        </div>
      )}
    </section>
  );
}
