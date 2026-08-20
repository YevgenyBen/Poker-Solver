import { describe, expect, it } from 'vitest';
import { shuffleSuits } from './boardSuits';

describe('shuffleSuits', () => {
  it("rotates each card's suit by c->d->h->s->c, keeping ranks unchanged", () => {
    expect(shuffleSuits('Jh7d2c')).toBe('Js7h2d');
  });

  it('composes safely under repeated calls (still a valid, well-formed board)', () => {
    const once = shuffleSuits('Jh7d2c');
    const twice = shuffleSuits(once);
    const thrice = shuffleSuits(twice);
    const back = shuffleSuits(thrice);
    expect(back).toBe('Jh7d2c'); // 4 applications of a 4-cycle is the identity
  });

  it('is a pure, safe no-op on text that is not a well-formed 3-card board', () => {
    expect(shuffleSuits('')).toBe('');
    expect(shuffleSuits('Jh7d')).toBe('Jh7d');
    expect(shuffleSuits('not a board')).toBe('not a board');
  });
});
