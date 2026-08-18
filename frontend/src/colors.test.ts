import { describe, expect, it } from 'vitest';
import { colorForAction, gradientFor, sortedEntries } from './colors';

describe('colorForAction', () => {
  it('maps each action prefix to its own color', () => {
    expect(colorForAction('fold')).toBe('var(--fold)');
    expect(colorForAction('call_or_check')).toBe('var(--call)');
    expect(colorForAction('raise:2.50')).toBe('var(--raise)');
    expect(colorForAction('all_in:100.00')).toBe('var(--allin)');
  });

  it('falls back to a neutral color for an unknown action', () => {
    expect(colorForAction('mystery')).toBe('#999');
  });
});

describe('sortedEntries', () => {
  it('orders entries fold, call, raise, all-in regardless of input order', () => {
    const freqs = { 'all_in:100.00': 0.1, fold: 0.2, 'raise:2.50': 0.3, call_or_check: 0.4 };
    expect(sortedEntries(freqs).map(([action]) => action)).toEqual([
      'fold',
      'call_or_check',
      'raise:2.50',
      'all_in:100.00',
    ]);
  });
});

describe('gradientFor', () => {
  it('builds a gradient with cumulative percentage stops', () => {
    const gradient = gradientFor({ fold: 0.25, call_or_check: 0.75 });
    expect(gradient).toBe('linear-gradient(to right, var(--fold) 0% 25%, var(--call) 25% 100%)');
  });

  it('skips actions with zero frequency', () => {
    const gradient = gradientFor({ fold: 0, call_or_check: 1 });
    expect(gradient).toBe('linear-gradient(to right, var(--call) 0% 100%)');
  });

  it('falls back to a neutral color when every frequency is zero', () => {
    expect(gradientFor({ fold: 0, call_or_check: 0 })).toBe('#999');
  });
});
