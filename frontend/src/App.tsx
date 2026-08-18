import { useState } from 'react';
import { DetailPanel } from './components/DetailPanel';
import { Legend } from './components/Legend';
import { RangeGrid } from './components/RangeGrid';
import { StackControl } from './components/StackControl';
import { useOpeningRange } from './useOpeningRange';

const DEFAULT_STACK_BB = 100;

export function App() {
  const [stackBb, setStackBb] = useState(DEFAULT_STACK_BB);
  const [selectedHand, setSelectedHand] = useState<string | null>(null);
  const { data, status } = useOpeningRange(stackBb);

  const openingRange = data?.opening_range ?? null;
  const selectedFreqs = selectedHand && openingRange ? (openingRange[selectedHand] ?? null) : null;

  return (
    <>
      <header>
        <h1>Heads-up preflop solver</h1>
        <p className="subtitle">BTN opening range (button vs. big blind, first action)</p>
      </header>

      <StackControl initialStackBb={stackBb} onChange={setStackBb} />
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
