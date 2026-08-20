import { gradientFor } from '../colors';
import { gridCells } from '../hands';
import type { OpeningRange, TrainedMap } from '../types';

interface RangeGridProps {
  openingRange: OpeningRange | null;
  trained: TrainedMap | null;
  selectedHand: string | null;
  onSelect: (hand: string) => void;
}

// M28: a hand missing from `trained` entirely (not present in the map
// at all) is treated as trained — every real API response's trained
// map covers exactly the same keys as its opening_range (see
// StrategyResult.trained_hands), so "absent" only actually happens for
// a caller that hasn't wired trained through yet, or the initial
// `openingRange: null` state before any response has arrived at all
// (gridCells still render then, colored '#999', same as before this
// feature existed) — defaulting to trained keeps that case looking
// exactly as it did before, rather than marking a not-yet-loaded grid
// as suspect.
export function RangeGrid({ openingRange, trained, selectedHand, onSelect }: RangeGridProps) {
  return (
    <div className="grid" aria-label="Starting hand grid">
      {gridCells().map(({ hand }) => {
        const freqs = openingRange?.[hand];
        const isTrained = trained?.[hand] ?? true;
        return (
          <div
            key={hand}
            className={`cell${hand === selectedHand ? ' selected' : ''}${isTrained ? '' : ' untrained'}`}
            style={{ background: freqs ? gradientFor(freqs) : '#999' }}
            onClick={() => onSelect(hand)}
            role="button"
            tabIndex={0}
            title={isTrained ? undefined : 'Not enough data — this is the untrained default, not a real strategy'}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') onSelect(hand);
            }}
          >
            {hand}
          </div>
        );
      })}
    </div>
  );
}
