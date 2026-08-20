// c->d->h->s->c — one specific element of the same 24-permutation suit
// group canonicalize_board (poker_solver/canonicalize.py) searches, so
// relabeling a board's suits under this fixed rotation is guaranteed to
// preserve canonical form by construction, and stays safe under repeated
// applications (composing bijections is still a bijection).
const SUIT_ROTATION: Record<string, string> = { c: 'd', d: 'h', h: 's', s: 'c' };
const BOARD_PATTERN = /^([2-9TJQKA][cdhs]){3}$/i;

/** Rotates every card's suit in `board` by SUIT_ROTATION, keeping ranks
 * untouched. A safe no-op on anything that isn't a well-formed 3-card
 * board (e.g. mid-edit text) — never throws, never corrupts the input. */
export function shuffleSuits(board: string): string {
  if (!BOARD_PATTERN.test(board)) return board;
  let shuffled = '';
  for (let i = 0; i < board.length; i += 2) {
    const rank = board[i];
    const suit = board[i + 1].toLowerCase();
    shuffled += rank + (SUIT_ROTATION[suit] ?? suit);
  }
  return shuffled;
}
