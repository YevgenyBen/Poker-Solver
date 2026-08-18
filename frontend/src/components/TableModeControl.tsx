import { MULTIWAY_POSITIONS, MULTIWAY_TABLE_SIZES, type MultiwayTableSize } from '../hands';

interface TableModeControlProps {
  players: number;
  position: string;
  onPlayersChange: (players: number) => void;
  onPositionChange: (position: string) => void;
}

const TABLE_SIZE_LABELS: Record<MultiwayTableSize, string> = {
  3: '3-max (demo)',
  6: '6-max (demo)',
  9: '9-max (demo)',
};

/** Lets the user switch between the heads-up solver (full 169-hand,
 * exact CFR+) and a multiway demo (3/6/9-max, small curated hand
 * subset, MCCFR — see api/main.py's module docstring for why), and pick
 * which position's strategy to view once in a multiway mode. */
export function TableModeControl({
  players,
  position,
  onPlayersChange,
  onPositionChange,
}: TableModeControlProps) {
  const multiwayPositions = MULTIWAY_TABLE_SIZES.includes(players as MultiwayTableSize)
    ? MULTIWAY_POSITIONS[players as MultiwayTableSize]
    : null;

  return (
    <section className="table-mode-control">
      <div className="players-toggle" role="group" aria-label="Table size">
        <button type="button" className={players === 2 ? 'active' : ''} onClick={() => onPlayersChange(2)}>
          Heads-up
        </button>
        {MULTIWAY_TABLE_SIZES.map((size) => (
          <button
            key={size}
            type="button"
            className={players === size ? 'active' : ''}
            onClick={() => onPlayersChange(size)}
          >
            {TABLE_SIZE_LABELS[size]}
          </button>
        ))}
      </div>
      {multiwayPositions && (
        <div className="position-selector" role="group" aria-label="Position">
          {multiwayPositions.map((pos) => (
            <button
              key={pos}
              type="button"
              className={pos === position ? 'active' : ''}
              onClick={() => onPositionChange(pos)}
            >
              {pos}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
