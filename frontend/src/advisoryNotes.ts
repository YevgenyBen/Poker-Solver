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
  // 'costly-band' was here until M299 (audit R3) re-priced it at the
  // shipped configuration and withdrew it: the 0.55-0.90 strength band
  // measures 1.48x at 0.90 sigma, and on the metric its copy quoted it
  // separates not at all. A withdrawn warning must leave the front end
  // too, or the backend stops sending a note the UI still promises.
};

export function splitNotes(notes: AdvisoryNote[]): { priority: AdvisoryNote[]; other: AdvisoryNote[] } {
  const order = Object.keys(PRIORITY_NOTES);
  const priority = notes
    .filter((n) => n.id in PRIORITY_NOTES)
    .sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
  const other = notes.filter((n) => !(n.id in PRIORITY_NOTES));
  return { priority, other };
}
