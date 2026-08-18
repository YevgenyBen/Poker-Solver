import { useState } from 'react';
import { DetailPanel } from './components/DetailPanel';
import { EquityCalculator } from './components/EquityCalculator';
import { Legend } from './components/Legend';
import { RangeGrid } from './components/RangeGrid';
import { StackControl } from './components/StackControl';
import { TableModeControl } from './components/TableModeControl';
import { MULTIWAY_POSITIONS, MULTIWAY_TABLE_SIZES, type MultiwayTableSize } from './hands';
import { useOpeningRange } from './useOpeningRange';

const DEFAULT_STACK_BB = 100;

function isMultiwayTableSize(players: number): players is MultiwayTableSize {
  return (MULTIWAY_TABLE_SIZES as readonly number[]).includes(players);
}

export function App() {
  const [stackBb, setStackBb] = useState(DEFAULT_STACK_BB);
  const [players, setPlayers] = useState(2);
  const [position, setPosition] = useState(MULTIWAY_POSITIONS[3][0]);
  const [selectedHand, setSelectedHand] = useState<string | null>(null);

  const isMultiway = isMultiwayTableSize(players);
  const { data, status } = useOpeningRange(stackBb, isMultiway ? { players, position } : undefined);
  const openingRange = data?.opening_range ?? null;
  const selectedFreqs = selectedHand && openingRange ? (openingRange[selectedHand] ?? null) : null;

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

  const title = isMultiway ? `${players}-max preflop solver (demo)` : 'Heads-up preflop solver';
  const subtitle = isMultiway
    ? `${position}'s strategy, action folded to them — ${players}-max demo: a small curated hand subset (MCCFR), not the full 169-hand exact solve`
    : 'BTN opening range (button vs. big blind, first action)';

  return (
    <>
      <header>
        <h1>{title}</h1>
        <p className="subtitle">{subtitle}</p>
      </header>

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

      <Legend />

      <main>
        <RangeGrid openingRange={openingRange} selectedHand={selectedHand} onSelect={setSelectedHand} />
        <DetailPanel hand={selectedHand} freqs={selectedFreqs} />
      </main>

      <EquityCalculator />
    </>
  );
}
