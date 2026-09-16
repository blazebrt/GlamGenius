/**
 * The source contracts, held at compile time.
 *
 * Step 10A introduced a third inventory source, `explicit_scan`, for an item a
 * person put on their shelf from a scanned pack. Two things have to stay true
 * and neither is visible at runtime:
 *
 *  1. `InventoryItem.source` and `InventoryAttribute.source` accept it.
 *  2. `SupplementLabelFact.source` does *not*. Supplements are not part of
 *     scan-to-shelf in Step 10A, and the backend CHECK constraint on that
 *     column is deliberately narrower. Widening the TypeScript union would let
 *     a caller send a value the database will refuse, and the mismatch would
 *     only surface as a 500 in production.
 *
 * These are type-level assertions: they cost nothing at runtime and fail
 * `tsc --noEmit`, which CI runs, if either contract drifts.
 */
import type {
  InventoryAttribute,
  InventoryItem,
  InventorySource,
  SupplementLabelFact,
} from '../services/apiV2';

/** Fails to compile unless T is exactly `true`. */
type Expect<T extends true> = T;

/** Exact type equality, not mutual assignability. */
type Equals<A, B> =
  (<G>() => G extends A ? 1 : 2) extends (<G>() => G extends B ? 1 : 2) ? true : false;

// --- Inventory accepts exactly three sources, no more and no fewer ----------
type _InventorySourceIsExactlyThese = Expect<
  Equals<InventorySource, 'user_declared' | 'photo_extracted' | 'explicit_scan'>
>;

type _InventoryItemUsesThatUnion = Expect<Equals<InventoryItem['source'], InventorySource>>;
type _InventoryAttributeUsesThatUnion = Expect<Equals<InventoryAttribute['source'], InventorySource>>;

// --- Supplement label facts stay narrower ----------------------------------
type _SupplementSourceIsNarrow = Expect<
  Equals<SupplementLabelFact['source'], 'user_declared' | 'photo_extracted'>
>;

type _SupplementRejectsExplicitScan = Expect<
  Equals<'explicit_scan' extends SupplementLabelFact['source'] ? true : false, false>
>;

// Referenced so the aliases are not reported as unused; the assertions above
// have already been checked by the compiler by the time this runs.
export type SourceContractGuards = [
  _InventorySourceIsExactlyThese,
  _InventoryItemUsesThatUnion,
  _InventoryAttributeUsesThatUnion,
  _SupplementSourceIsNarrow,
  _SupplementRejectsExplicitScan,
];

describe('inventory source contracts', () => {
  it('are enforced by the type checker, not at runtime', () => {
    // The proof is `tsc --noEmit`. This keeps the file a valid Jest suite and
    // records, in the run output, that the contract was checked at all.
    const inventorySources: InventorySource[] = ['user_declared', 'photo_extracted', 'explicit_scan'];
    const supplementSources: SupplementLabelFact['source'][] = ['user_declared', 'photo_extracted'];
    expect(inventorySources).toHaveLength(3);
    expect(supplementSources).toHaveLength(2);
    expect(supplementSources).not.toContain('explicit_scan');
  });
});
