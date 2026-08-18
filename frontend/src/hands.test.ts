import { describe, expect, it } from 'vitest';
import { gridCells, handLabelAt, PRESET_STACKS, RANKS } from './hands';

describe('handLabelAt', () => {
  it('labels the diagonal as pocket pairs', () => {
    expect(handLabelAt(0, 0)).toBe('AA');
    expect(handLabelAt(12, 12)).toBe('22');
  });

  it('labels the upper-right triangle as suited', () => {
    expect(handLabelAt(0, 1)).toBe('AKs');
  });

  it('labels the lower-left triangle as offsuit', () => {
    expect(handLabelAt(1, 0)).toBe('AKo');
  });

  it('always puts the higher rank first regardless of row/col order', () => {
    expect(handLabelAt(0, 12)).toBe('A2s');
    expect(handLabelAt(12, 0)).toBe('A2o');
  });
});

describe('gridCells', () => {
  it('produces exactly 169 cells', () => {
    expect(gridCells()).toHaveLength(169);
  });

  it('has no duplicate hand labels', () => {
    const labels = gridCells().map((cell) => cell.hand);
    expect(new Set(labels).size).toBe(169);
  });

  it('includes all 13 pocket pairs', () => {
    const labels = new Set(gridCells().map((cell) => cell.hand));
    for (const rank of RANKS) {
      expect(labels.has(rank + rank)).toBe(true);
    }
  });
});

describe('PRESET_STACKS', () => {
  it('matches the server pre-warm depths', () => {
    expect(PRESET_STACKS).toEqual([20, 40, 50, 75, 100, 150, 200]);
  });
});
