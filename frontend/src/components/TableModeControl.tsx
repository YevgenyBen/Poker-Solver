import { MULTIWAY_POSITIONS, MULTIWAY_TABLE_SIZES, type MultiwayTableSize } from '../hands';

interface TableModeControlProps {
  players: number;
  position: string;
  onPlayersChange: (players: number) => void;
  onPositionChange: (position: string) => void;
}

// M125 (E3): these read "3-max (demo)" / "6-max (demo)" / "9-max
// (demo)". Two problems. The pools stopped being demos in M67, which
// replaced the 8-class curated set with all 169 classes. And a uniform
// "(demo)" label flattened a distinction the engine draws sharply:
// 3-max and 6-max are "in much better shape", while 9-max is the one
// that must not be presented as authoritative. The per-table-size
// caveat now arrives from the API (solver_confidence), which says it
// accurately and only where it applies, so the label says the table
// size and nothing more.
const TABLE_SIZE_LABELS: Record<MultiwayTableSize, string> = {
  3: '3-max',
  6: '6-max',
  9: '9-max',
};

/** Lets the user switch between the heads-up solver (all 169 hand
 * classes, exact CFR+) and a multiway one (3/6/9-max, all 169 hand
 * classes, sampled MCCFR), and pick which position's strategy to view
 * once in a multiway mode. Reliability differs sharply by table size
 * and is reported per response by the API rather than asserted here. */
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
