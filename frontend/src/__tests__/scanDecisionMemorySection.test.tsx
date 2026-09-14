import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react-native';
import { ScanDecisionMemorySection } from '../components/shopping/ScanDecisionMemorySection';
import * as apiV2 from '../services/apiV2';

jest.mock('../services/apiV2');

describe('ScanDecisionMemorySection', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders correctly with no memory', () => {
    render(
      <ScanDecisionMemorySection
        barcode="123"
        labelVersion={1}
        contentFingerprint="fp"
        memory={null}
        onMemoryUpdated={jest.fn()}
      />
    );
    expect(screen.getByText('Would you buy this?')).toBeTruthy();
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
        labelVersion={1}
        contentFingerprint="fp"
        memory={memory}
        onMemoryUpdated={jest.fn()}
      />
    );
    expect(screen.getByText('You decided to BUY this')).toBeTruthy();
    
    fireEvent.press(screen.getByText('Change Decision'));
    expect(screen.getByText('Would you buy this?')).toBeTruthy();
  });

  it('calls save API when a decision is made', async () => {
    (apiV2.saveScanDecision as jest.Mock).mockResolvedValue({ id: 'evt-2', decision: 'SKIP' });
    const onMemoryUpdated = jest.fn();

    render(
      <ScanDecisionMemorySection
        barcode="123"
        labelVersion={1}
        contentFingerprint="fp"
        memory={null}
        onMemoryUpdated={onMemoryUpdated}
      />
    );
    
    fireEvent.press(screen.getByText('SKIP'));
    await waitFor(() => {
      expect(apiV2.saveScanDecision).toHaveBeenCalledWith('123', expect.objectContaining({
        decision: 'SKIP',
        label_version: 1,
        content_fingerprint: 'fp',
        idempotency_key: expect.any(String)
      }));
      expect(onMemoryUpdated).toHaveBeenCalled();
    });
  });
});
