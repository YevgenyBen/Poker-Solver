import { describe, expect, it } from 'vitest';
import { onlyAllInModelled } from './betSizing';

describe('onlyAllInModelled', () => {
  it('is true only when every modelled size is the whole stack', () => {
    expect(onlyAllInModelled({ modelled_bet_sizes: [97.5], max_affordable_bb: 97.5 })).toBe(true);
    expect(onlyAllInModelled({ modelled_bet_sizes: [12.5, 97.5], max_affordable_bb: 97.5 })).toBe(false);
    // Absent or empty data is not evidence of anything.
    expect(onlyAllInModelled({ max_affordable_bb: 97.5 })).toBe(false);
    expect(onlyAllInModelled({ modelled_bet_sizes: [], max_affordable_bb: 97.5 })).toBe(false);
    expect(onlyAllInModelled({ modelled_bet_sizes: [97.5] })).toBe(false);
  });
});
