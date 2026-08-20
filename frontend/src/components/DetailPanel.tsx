import { colorForAction, sortedEntries } from '../colors';
import type { ActionFrequencies } from '../types';

interface DetailPanelProps {
  hand: string | null;
  freqs: ActionFrequencies | null;
  trained: boolean | null;
}

export function DetailPanel({ hand, freqs, trained }: DetailPanelProps) {
  if (!hand || !freqs) {
    return (
      <aside className="detail">
        <p className="detail-placeholder">Click a hand to see its full strategy.</p>
      </aside>
    );
  }

  // `trained === null` means the caller doesn't have a value yet (or
  // never wired one through) — treated as trained, same "absent means
  // trusted" default RangeGrid uses for the identical reason.
  const isTrained = trained ?? true;

  return (
    <aside className="detail">
      <h2>{hand}</h2>
      {!isTrained && (
        <p className="untrained-warning" role="status">
          Not enough data yet — the solver never sampled this hand here at this iteration budget. The numbers below
          are the untrained default, not a real strategy.
        </p>
      )}
      {sortedEntries(freqs).map(([action, freq]) => {
        const pct = (freq * 100).toFixed(1);
        return (
          <div className="detail-row" key={action}>
            <span className="label">{action}</span>
            <span className="bar-track">
              <span
                className="bar-fill"
                style={{ width: `${pct}%`, background: colorForAction(action) }}
              />
            </span>
            <span className="pct">{pct}%</span>
          </div>
        );
      })}
    </aside>
  );
}
