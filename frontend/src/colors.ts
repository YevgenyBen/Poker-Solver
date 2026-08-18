import type { ActionFrequencies } from './types';

// Fixed left-to-right stacking order for the in-cell gradient and the
// detail panel's bars, regardless of the order keys happen to arrive in
// from the API.
export const ACTION_ORDER = ['fold', 'call_or_check', 'raise', 'all_in'] as const;

export function colorForAction(actionLabel: string): string {
  if (actionLabel.startsWith('fold')) return 'var(--fold)';
  if (actionLabel.startsWith('call_or_check')) return 'var(--call)';
  if (actionLabel.startsWith('raise')) return 'var(--raise)';
  if (actionLabel.startsWith('all_in')) return 'var(--allin)';
  return '#999';
}

function actionRank(label: string): number {
  const index = ACTION_ORDER.findIndex((prefix) => label.startsWith(prefix));
  return index === -1 ? ACTION_ORDER.length : index;
}

export function sortedEntries(freqs: ActionFrequencies): [string, number][] {
  return Object.entries(freqs).sort((a, b) => actionRank(a[0]) - actionRank(b[0]));
}

/** CSS gradient string that stacks each action's color proportional to
 * its frequency, left to right in ACTION_ORDER. */
export function gradientFor(freqs: ActionFrequencies): string {
  const entries = sortedEntries(freqs);
  let cursor = 0;
  const stops: string[] = [];
  for (const [action, freq] of entries) {
    if (freq <= 0) continue;
    const start = cursor;
    cursor += freq * 100;
    stops.push(`${colorForAction(action)} ${start}% ${cursor}%`);
  }
  if (stops.length === 0) return '#999';
  return `linear-gradient(to right, ${stops.join(', ')})`;
}
