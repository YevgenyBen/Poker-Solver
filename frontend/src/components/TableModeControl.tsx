import { MULTIWAY_POSITIONS } from '../hands';

interface TableModeControlProps {
  players: number;
  position: string;
  onPlayersChange: (players: number) => void;
  onPositionChange: (position: string) => void;
}

/** M8: lets the user switch between the heads-up solver (full 169-hand,
 * exact CFR+) and a 3-max demo (small curated hand subset, MCCFR — see
 * api/main.py's module docstring for why), and pick which position's
 * strategy to view once in 3-max mode. */
export function TableModeControl({
  players,
  position,
  onPlayersChange,
  onPositionChange,
}: TableModeControlProps) {
  return (
    <section className="table-mode-control">
      <div className="players-toggle" role="group" aria-label="Table size">
        <button type="button" className={players === 2 ? 'active' : ''} onClick={() => onPlayersChange(2)}>
          Heads-up
        </button>
        <button type="button" className={players === 3 ? 'active' : ''} onClick={() => onPlayersChange(3)}>
          3-max (demo)
        </button>
      </div>
      {players === 3 && (
        <div className="position-selector" role="group" aria-label="Position">
          {MULTIWAY_POSITIONS.map((pos) => (
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
