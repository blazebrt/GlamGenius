import React, { Profiler } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import { ScanShelfOwnershipSection } from '../components/shopping/ScanShelfOwnershipSection';
import * as apiV2 from '../services/apiV2';

jest.mock('../services/apiV2');

// The project-wide mock hands back a fresh `push` spy on every `useRouter()`
// call, which cannot be asserted on. Navigation to another pack's inventory
// item is precisely what must never happen here, so this file keeps one
// stable spy.
const mockRouterPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({
    push: mockRouterPush, replace: jest.fn(), back: jest.fn(), canGoBack: () => true,
  }),
}));

const identityA = {
  barcode: '8901234567890', label_snapshot_id: 'snapshot-a', label_version: 1,
  content_fingerprint: 'a'.repeat(64),
};
const identityB = {
  barcode: '8901234567891', label_snapshot_id: 'snapshot-b', label_version: 2,
  content_fingerprint: 'b'.repeat(64),
};

const status = (identity = identityA, values: Partial<apiV2.ScanShelfStatus> = {}): apiV2.ScanShelfStatus => ({
  contract_version: 'step-10a-v1', status: 'eligible_not_owned', identity,
  inventory_item_id: null, ...values,
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

/**
 * Render the section and record the tree as committed on every commit after
 * the first.
 *
 * `rerender` runs inside `act()`, which flushes passive effects before
 * returning, so an assertion made afterwards can only ever see the tree the
 * identity-reset effect left behind. That is exactly the frame the production
 * bug lived in: React renders with B's props while the state is still A's,
 * commits that, and only then runs the effect. A `Profiler`'s `onRender` fires
 * during the commit phase, before passive effects, so it sees that frame.
 *
 * Verified against a deliberately broken replica of the old component: the
 * recorder captured A's committed content under B's identity, and captures
 * nothing but `null` against the current implementation.
 */
function renderWithCommits(identity: apiV2.ScanShelfIdentity) {
  const commits: string[] = [];
  const holder: { current: null | { toJSON: () => unknown } } = { current: null };
  const onRender = () => { if (holder.current) commits.push(JSON.stringify(holder.current.toJSON())); };
  const tree = (value: apiV2.ScanShelfIdentity) => (
    <Profiler id="scan-shelf-boundary" onRender={onRender}>
      <ScanShelfOwnershipSection identity={value} />
    </Profiler>
  );
  const rendered = render(tree(identity));
  holder.current = rendered;
  return { commits, rendered, tree };
}

describe('ScanShelfOwnershipSection exact identity lifecycle', () => {
  beforeEach(() => jest.clearAllMocks());

  it('fails closed on malformed or mismatched private status without hiding Product Truth', async () => {
    (apiV2.readScanShelfStatus as jest.Mock).mockResolvedValue(status(identityB));
    render(<ScanShelfOwnershipSection identity={identityA} />);
    await waitFor(() => expect(apiV2.readScanShelfStatus).toHaveBeenCalledWith(identityA));
    expect(screen.queryByLabelText('Add to shelf')).toBeNull();
  });

  it('binds a retry to one identity-bound mutation key and changes it for a new exact pack', async () => {
    (apiV2.readScanShelfStatus as jest.Mock).mockImplementation((identity) => Promise.resolve(status(identity)));
    (apiV2.addScanProductToShelf as jest.Mock).mockImplementation((value) => Promise.resolve(status(value, {
      status: 'eligible_not_owned', inventory_item_id: null,
    })));
    const rendered = render(<ScanShelfOwnershipSection identity={identityA} />);
    await screen.findByLabelText('Add to shelf');
    fireEvent.press(screen.getByLabelText('Add to shelf'));
    await waitFor(() => expect(apiV2.addScanProductToShelf).toHaveBeenCalledTimes(1));
    const firstKey = (apiV2.addScanProductToShelf as jest.Mock).mock.calls[0][0].client_mutation_id;
    fireEvent.press(screen.getByLabelText('Add to shelf'));
    await waitFor(() => expect(apiV2.addScanProductToShelf).toHaveBeenCalledTimes(2));
    expect((apiV2.addScanProductToShelf as jest.Mock).mock.calls[1][0].client_mutation_id).toBe(firstKey);
    rendered.rerender(<ScanShelfOwnershipSection identity={identityB} />);
    await screen.findByLabelText('Add to shelf');
    fireEvent.press(screen.getByLabelText('Add to shelf'));
    await waitFor(() => expect(apiV2.addScanProductToShelf).toHaveBeenCalledTimes(3));
    expect((apiV2.addScanProductToShelf as jest.Mock).mock.calls[2][0]).toEqual(expect.objectContaining({
      ...identityB, client_mutation_id: expect.any(String),
    }));
    expect((apiV2.addScanProductToShelf as jest.Mock).mock.calls[2][0].client_mutation_id).not.toBe(firstKey);
  });

  it('fails closed when an owned response has no usable inventory item id', async () => {
    (apiV2.readScanShelfStatus as jest.Mock).mockResolvedValue(status(identityA, {
      status: 'owned', inventory_item_id: null,
    }));
    render(<ScanShelfOwnershipSection identity={identityA} />);
    await waitFor(() => expect(apiV2.readScanShelfStatus).toHaveBeenCalled());
    expect(screen.queryByText('ON YOUR SHELF')).toBeNull();
  });

  // -------------------------------------------------------------------------
  // The render boundary itself
  // -------------------------------------------------------------------------
  //
  // Asserting on the tree after `rerender` returns is not enough: `act()` has
  // already flushed the effect by then, so such a test passes even when the
  // component shows one pack's private ownership under another pack's
  // identity for a frame. These two read the commits as they happen.

  describe('the A -> B render boundary', () => {
    it('never commits A ownership under identity B, not even before B resolves', async () => {
      const readB = deferred<apiV2.ScanShelfStatus>();
      (apiV2.readScanShelfStatus as jest.Mock)
        .mockImplementationOnce(() => Promise.resolve(status(identityA, {
          status: 'owned', inventory_item_id: 'item-a',
        })))
        .mockImplementationOnce(() => readB.promise);

      const { commits, rendered, tree } = renderWithCommits(identityA);

      // A is genuinely owned and its shelf affordance is on screen.
      await screen.findByText('ON YOUR SHELF');
      expect(screen.getByText('View on your shelf')).toBeTruthy();

      commits.length = 0;
      rendered.rerender(tree(identityB));

      // B's status request is still in flight. Every frame committed under B's
      // identity must already be free of A.
      expect(commits.length).toBeGreaterThan(0);
      const committedUnderB = commits.join('\n');
      expect(committedUnderB).not.toContain('ON YOUR SHELF');
      expect(committedUnderB).not.toContain('View on your shelf');
      expect(committedUnderB).not.toContain('item-a');

      // And the settled tree agrees, with no route to A's item from B.
      expect(screen.queryByText('ON YOUR SHELF')).toBeNull();
      expect(screen.queryByText('View on your shelf')).toBeNull();
      expect(mockRouterPush).not.toHaveBeenCalled();

      // Only B's own answer may appear once it arrives.
      readB.resolve(status(identityB));
      await screen.findByLabelText('Add to shelf');
      expect(screen.queryByText('ON YOUR SHELF')).toBeNull();
      expect(mockRouterPush).not.toHaveBeenCalled();
    });

    it('never commits A busy state under identity B while A write is in flight', async () => {
      const writeA = deferred<apiV2.ScanShelfStatus>();
      const readB = deferred<apiV2.ScanShelfStatus>();
      (apiV2.readScanShelfStatus as jest.Mock)
        .mockImplementationOnce(() => Promise.resolve(status(identityA)))
        .mockImplementationOnce(() => readB.promise);
      (apiV2.addScanProductToShelf as jest.Mock).mockImplementationOnce(() => writeA.promise);

      const { commits, rendered, tree } = renderWithCommits(identityA);
      await screen.findByLabelText('Add to shelf');

      // A is mid-write: its button is disabled and showing a spinner.
      fireEvent.press(screen.getByLabelText('Add to shelf'));
      await waitFor(() => expect(apiV2.addScanProductToShelf).toHaveBeenCalledTimes(1));
      expect(screen.getByLabelText('Add to shelf').props.accessibilityState.disabled).toBe(true);

      commits.length = 0;
      rendered.rerender(tree(identityB));

      // B must not inherit that. A disabled control or a spinner in any frame
      // committed under B would be A's busy state bleeding through.
      expect(commits.length).toBeGreaterThan(0);
      const committedUnderB = commits.join('\n');
      expect(committedUnderB).not.toContain('ActivityIndicator');
      expect(committedUnderB).not.toContain('"disabled":true');

      // B establishes its own interaction state from its own response.
      readB.resolve(status(identityB));
      const button = await screen.findByLabelText('Add to shelf');
      expect(button.props.accessibilityState.disabled).toBe(false);
      expect(screen.getByText('Add to shelf')).toBeTruthy();

      // A's write landing late must not disturb B either.
      writeA.resolve(status(identityA, { status: 'owned', inventory_item_id: 'item-a' }));
      await waitFor(() => expect(screen.queryByText('ON YOUR SHELF')).toBeNull());
      expect(screen.getByLabelText('Add to shelf').props.accessibilityState.disabled).toBe(false);
    });
  });

  // -------------------------------------------------------------------------
  // Late responses from the previous identity
  // -------------------------------------------------------------------------

  it('ignores a stale A read that only resolves after the exact identity became B', async () => {
    // The A read stays pending across the identity change, which is the whole
    // point: a test that resolves it before the rerender proves only that a
    // settled value is replaced, never that an in-flight one is discarded.
    const readA = deferred<apiV2.ScanShelfStatus>();
    const readB = deferred<apiV2.ScanShelfStatus>();
    (apiV2.readScanShelfStatus as jest.Mock)
      .mockImplementationOnce(() => readA.promise)
      .mockImplementationOnce(() => readB.promise);

    const rendered = render(<ScanShelfOwnershipSection identity={identityA} />);
    await waitFor(() => expect(apiV2.readScanShelfStatus).toHaveBeenCalledTimes(1));

    rendered.rerender(<ScanShelfOwnershipSection identity={identityB} />);
    await waitFor(() => expect(apiV2.readScanShelfStatus).toHaveBeenCalledTimes(2));
    expect((apiV2.readScanShelfStatus as jest.Mock).mock.calls[1][0]).toEqual(identityB);

    // B answers first.
    readB.resolve(status(identityB));
    await screen.findByLabelText('Add to shelf');

    // A's read lands afterwards, claiming ownership. It is not B's.
    readA.resolve(status(identityA, { status: 'owned', inventory_item_id: 'item-a' }));
    await waitFor(() => expect(apiV2.readScanShelfStatus).toHaveBeenCalledTimes(2));

    expect(screen.queryByText('ON YOUR SHELF')).toBeNull();
    expect(screen.queryByText('View on your shelf')).toBeNull();
    expect(screen.getByLabelText('Add to shelf')).toBeTruthy();
    expect(mockRouterPush).not.toHaveBeenCalled();
  });

  it('ignores a stale A write that only resolves after the exact identity became B', async () => {
    const writeA = deferred<apiV2.ScanShelfStatus>();
    (apiV2.readScanShelfStatus as jest.Mock)
      .mockImplementationOnce(() => Promise.resolve(status(identityA)))
      .mockImplementationOnce(() => Promise.resolve(status(identityB)));
    (apiV2.addScanProductToShelf as jest.Mock).mockImplementationOnce(() => writeA.promise);

    const rendered = render(<ScanShelfOwnershipSection identity={identityA} />);
    await screen.findByLabelText('Add to shelf');
    fireEvent.press(screen.getByLabelText('Add to shelf'));
    await waitFor(() => expect(apiV2.addScanProductToShelf).toHaveBeenCalledTimes(1));

    rendered.rerender(<ScanShelfOwnershipSection identity={identityB} />);
    await screen.findByLabelText('Add to shelf');

    writeA.resolve(status(identityA, { status: 'owned', inventory_item_id: 'item-a' }));
    await waitFor(() => expect(screen.getByLabelText('Add to shelf')).toBeTruthy());
    expect(screen.queryByText('ON YOUR SHELF')).toBeNull();
    expect(mockRouterPush).not.toHaveBeenCalled();
  });

  // -------------------------------------------------------------------------
  // One field at a time
  // -------------------------------------------------------------------------
  //
  // Comparing against a wholly different identity cannot show that each field
  // is checked; three of the four could be ignored and the test would still
  // pass. These vary exactly one field per case.

  describe('a response envelope differing in a single identity field is refused', () => {
    const mismatches: readonly [string, apiV2.ScanShelfIdentity][] = [
      ['barcode', { ...identityA, barcode: '8901234567899' }],
      ['label_snapshot_id', { ...identityA, label_snapshot_id: 'snapshot-other' }],
      ['label_version', { ...identityA, label_version: identityA.label_version + 1 }],
      ['content_fingerprint', { ...identityA, content_fingerprint: 'c'.repeat(64) }],
    ];

    it.each(mismatches)('refuses an owned status whose %s does not match the request', async (_field, envelope) => {
      (apiV2.readScanShelfStatus as jest.Mock).mockResolvedValue(status(envelope, {
        status: 'owned', inventory_item_id: 'item-from-elsewhere',
      }));

      const rendered = render(<ScanShelfOwnershipSection identity={identityA} />);
      await waitFor(() => expect(apiV2.readScanShelfStatus).toHaveBeenCalledWith(identityA));

      // Nothing is derived from an envelope we cannot vouch for: no ownership
      // claim, and no Add control either.
      expect(screen.queryByText('ON YOUR SHELF')).toBeNull();
      expect(screen.queryByText('View on your shelf')).toBeNull();
      expect(screen.queryByLabelText('Add to shelf')).toBeNull();
      expect(mockRouterPush).not.toHaveBeenCalled();
      // Product Truth is rendered by the surrounding screen; this private
      // section simply contributes nothing rather than failing.
      expect(rendered.toJSON()).toBeNull();
    });

    it.each(mismatches)('refuses an add response whose %s does not match the request', async (_field, envelope) => {
      (apiV2.readScanShelfStatus as jest.Mock).mockResolvedValue(status(identityA));
      (apiV2.addScanProductToShelf as jest.Mock).mockResolvedValue(status(envelope, {
        status: 'owned', inventory_item_id: 'item-from-elsewhere',
      }));

      render(<ScanShelfOwnershipSection identity={identityA} />);
      await screen.findByLabelText('Add to shelf');
      fireEvent.press(screen.getByLabelText('Add to shelf'));
      await waitFor(() => expect(apiV2.addScanProductToShelf).toHaveBeenCalledTimes(1));

      // The write is not accepted as proof of ownership, and the control comes
      // back ready rather than stuck.
      await waitFor(() => expect(
        screen.getByLabelText('Add to shelf').props.accessibilityState.disabled,
      ).toBe(false));
      expect(screen.queryByText('ON YOUR SHELF')).toBeNull();
      expect(screen.queryByText('View on your shelf')).toBeNull();
      expect(mockRouterPush).not.toHaveBeenCalled();
    });
  });
});
