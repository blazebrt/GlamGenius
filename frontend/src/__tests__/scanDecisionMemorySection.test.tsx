import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react-native';
import { ScanDecisionMemorySection } from '../components/shopping/ScanDecisionMemorySection';
import { ScanShelfOwnershipSection } from '../components/shopping/ScanShelfOwnershipSection';
import * as apiV2 from '../services/apiV2';
import { S } from '../strings/verdict';
import { PURCHASE_MEMORY } from '../strings/purchaseMemory';

jest.mock('../services/apiV2');

describe('ScanDecisionMemorySection', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders correctly with no memory', () => {
    render(
      <ScanDecisionMemorySection
        barcode="123"
        labelSnapshotId="x"
        labelVersion={1}
        contentFingerprint="fp"
        memory={null}
        onMemoryUpdated={jest.fn()}
      />
    );
    expect(screen.getByText(S.decisionMemory.title)).toBeTruthy();
    expect(screen.getByText(S.decisionMemory.actions.buy)).toBeTruthy();
  });

  it('renders memory and allows reconsidering', async () => {
    const memory = {
      scan_decision_memory_version: 'v1',
      identity: { barcode: '123', label_snapshot_id: 'x', label_version: 1, content_fingerprint: 'fp' },
      decision: { id: 'evt-1', decision: 'BUY', note: null, occurred_at: '2026-01-01T00:00:00Z' },
      history: []
    } as any;

    render(
      <ScanDecisionMemorySection
        barcode="123"
        labelSnapshotId="x"
        labelVersion={1}
        contentFingerprint="fp"
        memory={memory}
        onMemoryUpdated={jest.fn()}
      />
    );
    expect(screen.getByText(PURCHASE_MEMORY.bought)).toBeTruthy();
    
    fireEvent.press(screen.getByText(S.decisionMemory.reconsider));
    expect(screen.getByText(S.decisionMemory.actions.buy)).toBeTruthy();
  });

  it('calls save API when a decision is made', async () => {
    (apiV2.saveScanDecision as jest.Mock).mockResolvedValue({ id: 'evt-2', decision: 'SKIP' });
    const onMemoryUpdated = jest.fn();

    render(
      <ScanDecisionMemorySection
        barcode="123"
        labelSnapshotId="x"
        labelVersion={1}
        contentFingerprint="fp"
        memory={null}
        onMemoryUpdated={onMemoryUpdated}
      />
    );
    
    fireEvent.press(screen.getByText(S.decisionMemory.actions.skip));
    await waitFor(() => {
      expect(apiV2.saveScanDecision).toHaveBeenCalledWith('123', expect.objectContaining({
        decision: 'SKIP',
        label_snapshot_id: 'x',
        label_version: 1,
        content_fingerprint: 'fp',
        idempotency_key: expect.any(String)
      }));
      expect(onMemoryUpdated).toHaveBeenCalled();
    });
  });
});

describe('a decision is never an ownership statement', () => {
  beforeEach(() => jest.clearAllMocks());

  const identity = {
    barcode: '8901234567890',
    labelSnapshotId: 'snapshot-a',
    labelVersion: 1,
    contentFingerprint: 'a'.repeat(64),
  };

  it.each(['buy', 'wait', 'skip'] as const)(
    'recording %s saves a decision and never adds to the shelf',
    async (action) => {
      (apiV2.saveScanDecision as jest.Mock).mockResolvedValue({});
      render(
        <ScanDecisionMemorySection
          barcode={identity.barcode}
          labelSnapshotId={identity.labelSnapshotId}
          labelVersion={identity.labelVersion}
          contentFingerprint={identity.contentFingerprint}
          memory={null}
          onMemoryUpdated={jest.fn()}
        />,
      );

      fireEvent.press(screen.getByText(S.decisionMemory.actions[action]));
      await waitFor(() => expect(apiV2.saveScanDecision).toHaveBeenCalledTimes(1));

      // The decision was recorded against the exact scan...
      expect((apiV2.saveScanDecision as jest.Mock).mock.calls[0][0]).toBe(identity.barcode);
      expect((apiV2.saveScanDecision as jest.Mock).mock.calls[0][1]).toEqual(
        expect.objectContaining({
          decision: action.toUpperCase(),
          label_snapshot_id: identity.labelSnapshotId,
          label_version: identity.labelVersion,
          content_fingerprint: identity.contentFingerprint,
        }),
      );
      // ...and nothing was put on anybody's shelf. Deciding to buy something
      // is not the same as owning it, and a BUY that quietly created shelf
      // ownership would put a product the person only considered into the
      // inventory their routines are built from.
      expect(apiV2.addScanProductToShelf).not.toHaveBeenCalled();
    },
  );

  it('only the explicit Add to shelf control creates ownership', async () => {
    const shelfIdentity = {
      barcode: identity.barcode, label_snapshot_id: identity.labelSnapshotId,
      label_version: identity.labelVersion, content_fingerprint: identity.contentFingerprint,
    };
    (apiV2.readScanShelfStatus as jest.Mock).mockResolvedValue({
      contract_version: 'step-10a-v1', status: 'eligible_not_owned',
      identity: shelfIdentity, inventory_item_id: null,
    });
    (apiV2.addScanProductToShelf as jest.Mock).mockResolvedValue({
      contract_version: 'step-10a-v1', status: 'owned',
      identity: shelfIdentity, inventory_item_id: 'item-a',
    });

    render(<ScanShelfOwnershipSection identity={shelfIdentity} />);
    const add = await screen.findByLabelText('Add to shelf');

    expect(apiV2.addScanProductToShelf).not.toHaveBeenCalled();
    expect(apiV2.saveScanDecision).not.toHaveBeenCalled();

    fireEvent.press(add);
    await waitFor(() => expect(apiV2.addScanProductToShelf).toHaveBeenCalledTimes(1));

    // Ownership did not record a purchase decision on the way past.
    expect(apiV2.saveScanDecision).not.toHaveBeenCalled();
  });
});
