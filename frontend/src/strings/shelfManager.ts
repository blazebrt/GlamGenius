/**
 * The only words the manager card writes for itself.
 *
 * Everything else a person reads on that card — the decision, the reason, the
 * button, the override — is reviewed copy that arrives from the server with the
 * rule it came from. Nothing here characterises anybody's shelf, names a
 * condition, or suggests buying anything; there is nothing here that could.
 *
 * Checked against the six rules in LEGAL_RULES.md, the same as every other
 * string file.
 */
export const S = {
  /** Over an ordinary decision. */
  heading: 'YOUR MANAGER',
  /** Over an offer to undo a restriction the manager applied. */
  headingGiveBack: 'BACK IN YOUR ROUTINE',
  /** How much is behind the one on screen. Counted, never scored. */
  remaining: (count: number): string =>
    `${count} more thing${count === 1 ? '' : 's'} after this`,
};
