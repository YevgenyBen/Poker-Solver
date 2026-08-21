import { gradientFor, sortedEntries } from '../colors';
import type { ActionFrequencies } from '../types';

/** Frequencies below this are noise from a real solve's own floating
 * point floor, not a strategy a player would ever act on. Every caller
 * that used to hand-roll this row applied the identical threshold. */
const VISIBLE_FREQUENCY = 0.005;

interface ComboRowProps {
  /** The hand/combo this row is for. Omitted by callers that title the
   * row separately (AdviseSolver's hero card names the hand in its own
   * heading, so repeating it in the row would be redundant). */
  label?: string;
  freqs: ActionFrequencies;
  /** M28's confidence signal. `true` (the default) renders nothing —
   * an absent indicator means "trust this", which is the common case. */
  trained?: boolean;
}

/** M59: one strategy row — the full-width gradient bar plus its
 * action-frequency breakdown — extracted from the NINE hand-rolled
 * copies that had accumulated across seven components (see
 * docs/project-audit-2026-08-21.md's SS2.3).
 *
 * Deliberately NOT unified with the superficially-similar rows in
 * DetailPanel/EquityCalculator. Those are a genuinely different shape:
 * a PROPORTIONAL bar (width tracks a percentage, single flat color)
 * answering "how much of the whole is this", where this one is a
 * FULL-WIDTH gradient answering "how is this hand's action split".
 * Merging them would mean a component whose bar means two different
 * things depending on props — exactly the "unify things that only look
 * alike" mistake this project has hit before (M32's postflop_action_
 * order misapplication, M47's rejected lazy-chance idea, M50's own
 * deliberately-parameterized differences). Two honest components beat
 * one dishonest one.
 */
export function ComboRow({ label, freqs, trained = true }: ComboRowProps) {
  const breakdown = sortedEntries(freqs)
    .filter(([, freq]) => freq > VISIBLE_FREQUENCY)
    .map(([action, freq]) => `${action} ${(freq * 100).toFixed(0)}%`)
    .join(', ');

  return (
    <div className="detail-row">
      {label !== undefined && <span className="label">{label}</span>}
      <span className="bar-track">
        <span className="bar-fill" style={{ width: '100%', background: gradientFor(freqs) }} />
      </span>
      <span className="breakdown">{breakdown}</span>
      {!trained && (
        <span
          className="trained-indicator untrained"
          title="Not enough data — the untrained default, not a real strategy"
        >
          low data
        </span>
      )}
    </div>
  );
}
