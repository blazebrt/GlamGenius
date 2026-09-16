import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import { ScanShelfOwnershipSection } from '../components/shopping/ScanShelfOwnershipSection';
import * as apiV2 from '../services/apiV2';

jest.mock('../services/apiV2');

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

  it('ignores stale A reads and writes after the exact identity becomes B', async () => {
    const readA = deferred<apiV2.ScanShelfStatus>();
    const writeA = deferred<apiV2.ScanShelfStatus>();
    (apiV2.readScanShelfStatus as jest.Mock)
      .mockImplementationOnce(() => readA.promise)
      .mockImplementationOnce(() => Promise.resolve(status(identityB)));
    (apiV2.addScanProductToShelf as jest.Mock).mockImplementationOnce(() => writeA.promise);
    const rendered = render(<ScanShelfOwnershipSection identity={identityA} />);
    readA.resolve(status(identityA));
    await screen.findByLabelText('Add to shelf');
    fireEvent.press(screen.getByLabelText('Add to shelf'));
    rendered.rerender(<ScanShelfOwnershipSection identity={identityB} />);
    await screen.findByLabelText('Add to shelf');
    writeA.resolve(status(identityA, { status: 'owned', inventory_item_id: 'old-item' }));
    await waitFor(() => expect(screen.getByLabelText('Add to shelf')).toBeTruthy());
    expect(screen.queryByText('ON YOUR SHELF')).toBeNull();
  });

  it('fails closed when an owned response has no usable inventory item id', async () => {
    (apiV2.readScanShelfStatus as jest.Mock).mockResolvedValue(status(identityA, {
      status: 'owned', inventory_item_id: null,
    }));
    render(<ScanShelfOwnershipSection identity={identityA} />);
    await waitFor(() => expect(apiV2.readScanShelfStatus).toHaveBeenCalled());
    expect(screen.queryByText('ON YOUR SHELF')).toBeNull();
  });
});
