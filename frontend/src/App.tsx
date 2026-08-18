import { useState } from 'react';
import { DetailPanel } from './components/DetailPanel';
import { Legend } from './components/Legend';
import { RangeGrid } from './components/RangeGrid';
import { StackControl } from './components/StackControl';
import { TableModeControl } from './components/TableModeControl';
import { useOpeningRange } from './useOpeningRange';

const DEFAULT_STACK_BB = 100;
const DEFAULT_MULTIWAY_POSITION = 'BTN';

export function App() {
  const [stackBb, setStackBb] = useState(DEFAULT_STACK_BB);
  const [players, setPlayers] = useState(2);
  const [position, setPosition] = useState(DEFAULT_MULTIWAY_POSITION);
  const [selectedHand, setSelectedHand] = useState<string | null>(null);

  const { data, status } = useOpeningRange(stackBb, players === 3 ? { players, position } : undefined);
  const openingRange = data?.opening_range ?? null;
  const selectedFreqs = selectedHand && openingRange ? (openingRange[selectedHand] ?? null) : null;

  function handlePlayersChange(newPlayers: number) {
    setPlayers(newPlayers);
    setPosition(DEFAULT_MULTIWAY_POSITION);
    setSelectedHand(null);
  }

  function handlePositionChange(newPosition: string) {
    setPosition(newPosition);
    setSelectedHand(null);
  }

  const title = players === 2 ? 'Heads-up preflop solver' : '3-max preflop solver (demo)';
  const subtitle =
    players === 2
      ? 'BTN opening range (button vs. big blind, first action)'
      : `${position}'s strategy, action folded to them — 3-max demo: a small curated hand subset (MCCFR), not the full 169-hand exact solve`;

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
    </>
  );
}
