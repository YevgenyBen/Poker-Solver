/** M148: what the solver's tree could actually offer at a node.
 *
 * Lives outside the component file so the component module exports only
 * components (fast refresh), matching how `hands.ts` and `colors.ts` are
 * split out.
 */

/** True when the only way to put money in at this node is all-in.
 *
 * M144/F40: `FLOP_TO_RIVER_RAISE_SIZES` is empty at production settings,
 * so a river node's actions are check/call and all-in and nothing else. A
 * low all-in frequency there is NOT a smaller bet being considered and
 * rejected — a smaller bet was never a legal action, so nothing was
 * compared.
 *
 * Read off the response's own `modelled_bet_sizes` rather than off the
 * street, for the same reason the backend derives that field from the
 * strategy rows instead of from the config constants: the river is the
 * case today, and the claim should stay true if the sizing constants move.
 *
 * Absent or empty data is not evidence of anything, and returns false.
 */
export function onlyAllInModelled(result: {
  modelled_bet_sizes?: number[];
  max_affordable_bb?: number;
}): boolean {
  const sizes = result.modelled_bet_sizes;
  const bound = result.max_affordable_bb;
  if (!sizes || sizes.length === 0 || bound === undefined) return false;
  return sizes.every((size) => size >= bound - 1e-9);
}
