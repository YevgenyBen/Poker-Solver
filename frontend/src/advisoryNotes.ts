import type { AdvisoryNote } from './types';

/**
 * A2: the caveats that change what a player should DO, shown first and on
 * their own. Before this, every note was joined into one paragraph of up
 * to ~3,200 characters, and the most useful ones (turn-shove,
 * river-under-fold) were its last sentences.
 *
 * Titles are qualitative on purpose: every measured figure stays in the
 * backend's own text, so a re-measurement never leaves stale numbers here.
 */
export const PRIORITY_NOTES: Record<string, string> = {
  'multiway-bet': 'Multiway pot: this engine bets and raises far more than a strong player does',
  'turn-shove': 'Turn: shoving here is costly — calling is the serious alternative',
  'river-under-fold': 'River, close decision: folding is the serious alternative',
  'flop-under-fold': 'Flop, small bet: this engine calls too often — folding is the serious alternative',
  'costly-band': 'An expensive kind of decision — take extra care',
};

export function splitNotes(notes: AdvisoryNote[]): { priority: AdvisoryNote[]; other: AdvisoryNote[] } {
  const order = Object.keys(PRIORITY_NOTES);
  const priority = notes
    .filter((n) => n.id in PRIORITY_NOTES)
    .sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
  const other = notes.filter((n) => !(n.id in PRIORITY_NOTES));
  return { priority, other };
}
