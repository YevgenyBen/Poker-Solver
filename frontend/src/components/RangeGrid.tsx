import { gradientFor } from '../colors';
import { gridCells } from '../hands';
import type { OpeningRange } from '../types';

interface RangeGridProps {
  openingRange: OpeningRange | null;
  selectedHand: string | null;
  onSelect: (hand: string) => void;
}

export function RangeGrid({ openingRange, selectedHand, onSelect }: RangeGridProps) {
  return (
    <div className="grid" aria-label="Starting hand grid">
      {gridCells().map(({ hand }) => {
        const freqs = openingRange?.[hand];
        return (
          <div
            key={hand}
            className={`cell${hand === selectedHand ? ' selected' : ''}`}
            style={{ background: freqs ? gradientFor(freqs) : '#999' }}
            onClick={() => onSelect(hand)}
            role="button"
            tabIndex={0}
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
