import { describe, expect, it } from 'vitest';
import { PRIORITY_NOTES, splitNotes } from './advisoryNotes';

const notes = [
  { id: 'street-unmeasured', text: 'Accuracy here is unmeasured.' },
  { id: 'standing-aggression-caveat', text: 'How often to bet is a rough hint.' },
  { id: 'facing-a-bet-cost', text: 'Facing a bet is where the cost lives.' },
  { id: 'turn-shove', text: 'Shoving here cost at least 2.4 big blinds.' },
  { id: 'multiway-bet', text: 'This engine bets three times as often.' },
  { id: 'flop-under-fold', text: 'It folds 14 points less than the reference here.' },
];

describe('splitNotes', () => {
  it('puts the warnings that change the play first, in a fixed order', () => {
    const { priority, other } = splitNotes(notes);
    expect(priority.map((n) => n.id)).toEqual(['multiway-bet', 'turn-shove', 'flop-under-fold']);
    expect(other.map((n) => n.id)).toEqual([
      'street-unmeasured',
      'standing-aggression-caveat',
      'facing-a-bet-cost',
    ]);
  });


  it('gives every priority note a title with no figures in it', () => {
    for (const title of Object.values(PRIORITY_NOTES)) {
      expect(title).not.toMatch(/[0-9]/);
    }
  });
});
