import { useState } from 'react';
import { MULTIWAY_POSITIONS, MULTIWAY_TABLE_SIZES, type MultiwayTableSize } from '../hands';
import { useOpeningRange } from '../useOpeningRange';
import { DetailPanel } from './DetailPanel';
import { Legend } from './Legend';
import { RangeGrid } from './RangeGrid';
import { StackControl } from './StackControl';
import { TableModeControl } from './TableModeControl';

const DEFAULT_STACK_BB = 100;

function isMultiwayTableSize(players: number): players is MultiwayTableSize {
  return (MULTIWAY_TABLE_SIZES as readonly number[]).includes(players);
}

// The Preflop Ranges tab — extracted from App.tsx, which used to own
// this directly as its only content. Its own <h2> (not <h1>/<header>)
// matches every other tab's own heading convention, since the app
// shell now owns the single real <h1>/<header> landmark.
export function PreflopRangesPage() {
  const [stackBb, setStackBb] = useState(DEFAULT_STACK_BB);
  const [players, setPlayers] = useState(2);
  const [position, setPosition] = useState(MULTIWAY_POSITIONS[3][0]);
  const [selectedHand, setSelectedHand] = useState<string | null>(null);

  const isMultiway = isMultiwayTableSize(players);
  const { data, status } = useOpeningRange(stackBb, isMultiway ? { players, position } : undefined);
  const openingRange = data?.opening_range ?? null;
  const trained = data?.trained ?? null;
  const selectedFreqs = selectedHand && openingRange ? (openingRange[selectedHand] ?? null) : null;
  const selectedTrained = selectedHand && trained ? (trained[selectedHand] ?? null) : null;

  function handlePlayersChange(newPlayers: number) {
    setPlayers(newPlayers);
    // Default to that table size's own first-to-act position — BTN is
    // first to act at 3-max, but UTG is at 6/9-max, so a single
    // hardcoded default would silently ask for the wrong "opening
    // range" once the table has more than 3 seats.
    if (isMultiwayTableSize(newPlayers)) {
      setPosition(MULTIWAY_POSITIONS[newPlayers][0]);
    }
    setSelectedHand(null);
  }

  function handlePositionChange(newPosition: string) {
    setPosition(newPosition);
    setSelectedHand(null);
  }

  const title = isMultiway ? `${players}-max preflop solver` : 'Heads-up preflop solver';
  // M125 (E3). This used to read "a small curated hand subset (MCCFR),
  // not the full 169-hand exact solve". M67 replaced that 8-class pool
  // with all 169 classes, so it had been wrong for a long time — and
  // wrong in the reassuring direction, blaming pool size when the real
  // cause is sampling variance. It also lumped 3/6/9-max together as
  // "demo", where 3-max and 6-max are in much better shape and 9-max is
  // the one that must not be presented as authoritative.
  //
  // The caveats themselves now come from the API (solver_confidence /
  // sizing_confidence), so this line states the method and leaves the
  // reliability claims to the server that measured them.
  const subtitle = isMultiway
    ? `${position}'s strategy, action folded to them — ${players}-max, all 169 hand classes, sampled (MCCFR)`
    : 'BTN opening range (button vs. big blind, first action) — all 169 hand classes, exact (CFR+)';

  return (
    <section className="preflop-ranges">
      <h2>{title}</h2>
      <p className="subtitle">{subtitle}</p>

      <StackControl initialStackBb={stackBb} onChange={setStackBb} />
      <TableModeControl
        players={players}
        position={position}
        onPlayersChange={handlePlayersChange}
        onPositionChange={handlePositionChange}
      />
      <span className="status" role="status">
        {status}
      </span>

      {data?.solver_confidence === 'low' && (
        <p className="solver-warning" role="alert">
          <strong>Low confidence.</strong>{' '}
          {data.solver_confidence_reason ??
            'This table size does not converge at any affordable iteration count.'}
        </p>
      )}
      {data?.sizing_confidence === 'low' && (
        <p className="solver-warning" role="alert">
          <strong>Sizes are unreliable here.</strong>{' '}
          {data.sizing_confidence_reason ??
            'Multiway preflop is unreliable for which sizing to use.'}
        </p>
      )}

      <Legend />

      <div className="range-layout">
        <RangeGrid openingRange={openingRange} trained={trained} selectedHand={selectedHand} onSelect={setSelectedHand} />
        <DetailPanel hand={selectedHand} freqs={selectedFreqs} trained={selectedTrained} />
      </div>
    </section>
  );
}
