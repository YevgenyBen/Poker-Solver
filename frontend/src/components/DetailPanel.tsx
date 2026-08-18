import { colorForAction, sortedEntries } from '../colors';
import type { ActionFrequencies } from '../types';

interface DetailPanelProps {
  hand: string | null;
  freqs: ActionFrequencies | null;
}

export function DetailPanel({ hand, freqs }: DetailPanelProps) {
  if (!hand || !freqs) {
    return (
      <aside className="detail">
        <p className="detail-placeholder">Click a hand to see its full strategy.</p>
      </aside>
    );
  }

  return (
    <aside className="detail">
      <h2>{hand}</h2>
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
