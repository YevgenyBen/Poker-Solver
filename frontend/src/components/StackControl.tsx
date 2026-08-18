import { useState } from 'react';
import { PRESET_STACKS } from '../hands';

interface StackControlProps {
  initialStackBb: number;
  onChange: (stackBb: number) => void;
}

const MIN_STACK = 10;
const MAX_STACK = 250;
const STEP = 5;
const DEBOUNCE_MS = 350;

export function StackControl({ initialStackBb, onChange }: StackControlProps) {
  const [sliderValue, setSliderValue] = useState(initialStackBb);
  const [debounceTimer, setDebounceTimer] = useState<ReturnType<typeof setTimeout> | null>(null);

  function handleSliderInput(value: number) {
    setSliderValue(value);
    if (debounceTimer) clearTimeout(debounceTimer);
    setDebounceTimer(setTimeout(() => onChange(value), DEBOUNCE_MS));
  }

  function handlePresetClick(depth: number) {
    if (debounceTimer) clearTimeout(debounceTimer);
    setSliderValue(depth);
    onChange(depth);
  }

  return (
    <section className="controls">
      <div className="stack-control">
        <label htmlFor="stack-slider">Effective stack</label>
        <input
          id="stack-slider"
          type="range"
          min={MIN_STACK}
          max={MAX_STACK}
          step={STEP}
          value={sliderValue}
          onChange={(event) => handleSliderInput(Number(event.target.value))}
        />
        <span className="stack-value">{sliderValue}bb</span>
      </div>
      <div className="presets">
        {PRESET_STACKS.map((depth) => (
          <button
            key={depth}
            type="button"
            className={depth === sliderValue ? 'active' : ''}
            onClick={() => handlePresetClick(depth)}
          >
            {depth}bb
          </button>
        ))}
      </div>
    </section>
  );
}
