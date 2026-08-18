// Standard 13x13 starting-hand grid layout: pairs on the diagonal,
// suited hands upper-right, offsuit hands lower-left.

export const RANKS = ['A', 'K', 'Q', 'J', 'T', '9', '8', '7', '6', '5', '4', '3', '2'] as const;

// Matches api/main.py's PREWARM_STACK_DEPTHS — picking one of these is
// (after the server's first startup) always an instant response.
export const PRESET_STACKS = [20, 40, 50, 75, 100, 150, 200] as const;

// Matches api/main.py's MULTIWAY_TABLE_CONFIGS (each multiway table
// size's acting order) — hardcoded here rather than derived from a
// solve response so the position selector can render before the first
// response for that table size arrives.
export const MULTIWAY_TABLE_SIZES = [3, 6, 9] as const;
export type MultiwayTableSize = (typeof MULTIWAY_TABLE_SIZES)[number];

export const MULTIWAY_POSITIONS: Record<MultiwayTableSize, readonly string[]> = {
  3: ['BTN', 'SB', 'BB'],
  6: ['UTG', 'MP', 'CO', 'BTN', 'SB', 'BB'],
  9: ['UTG', 'UTG1', 'MP1', 'MP2', 'MP3', 'CO', 'BTN', 'SB', 'BB'],
};

/** Hand label ("AKs", "72o", "TT") for grid position (row, col), both
 * 0-indexed over RANKS (high to low). */
export function handLabelAt(row: number, col: number): string {
  const high = RANKS[Math.min(row, col)];
  const low = RANKS[Math.max(row, col)];
  if (row === col) return high + low;
  return row < col ? `${high}${low}s` : `${high}${low}o`;
}

export interface GridCell {
  row: number;
  col: number;
  hand: string;
}

/** All 169 grid cells, row-major. */
export function gridCells(): GridCell[] {
  const cells: GridCell[] = [];
  for (let row = 0; row < RANKS.length; row++) {
    for (let col = 0; col < RANKS.length; col++) {
      cells.push({ row, col, hand: handLabelAt(row, col) });
    }
  }
  return cells;
}
