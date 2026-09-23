/**
 * Step 15 — "Scan another product" lands on a fresh scanner, and only then.
 *
 * Going back from a Product Result still shows the last scan, as it always
 * did. A fresh-scan request resets the scanner to the camera once.
 */
import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react-native';

import ScanProductScreen from '../../app/scan-product';
import { consumeFreshScanRequest, requestFreshScan } from '../services/scanSession';

const mockFocusCallbacks = new Set<() => void>();
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: jest.fn(), back: jest.fn(), replace: jest.fn() }),
  useFocusEffect: (callback: () => void) => {
    const ReactActual = jest.requireActual<typeof React>('react');
    ReactActual.useEffect(() => {
      mockFocusCallbacks.add(callback);
      callback();
      return () => { mockFocusCallbacks.delete(callback); };
    }, [callback]);
  },
}));
jest.mock('expo-camera', () => {
  const { View } = jest.requireActual('react-native');
  const ReactActual = jest.requireActual('react');
  return {
    CameraView: ReactActual.forwardRef((props: Record<string, unknown>) =>
      ReactActual.createElement(View, { testID: 'camera', ...props })),
    useCameraPermissions: () => [{ granted: true, canAskAgain: true }, jest.fn()],
  };
});
jest.mock('../store/userStore', () => ({ useUserStore: () => ({ userId: null }) }));
const mockScanBarcode = jest.fn();
jest.mock('../services/productScan', () => ({
  ...jest.requireActual('../services/productScan'),
  scanBarcode: (...args: unknown[]) => mockScanBarcode(...args),
  ensureDevice: jest.fn(async () => ({ device_key: 'd', token: 't' })),
  syncQueue: jest.fn(async () => ({ sent: 0, remaining: 0 })),
  readQueue: jest.fn(async () => []),
}));
jest.mock('../services/apiV2', () => ({}));

const BARCODE = '8901030000011';

async function scanOnce() {
  render(<ScanProductScreen />);
  const camera = await screen.findByTestId('scan-camera');
  await act(async () => {
    await (camera.props.onBarcodeScanned as (event: { data: string }) => Promise<void>)({ data: BARCODE });
  });
  await waitFor(() => expect(screen.queryByTestId('scan-camera')).toBeNull());
}

function focus() {
  act(() => { mockFocusCallbacks.forEach((callback) => callback()); });
}

beforeEach(() => {
  jest.clearAllMocks();
  consumeFreshScanRequest();
  mockScanBarcode.mockResolvedValue({
    barcode: BARCODE, found: false, outcome: 'not_found',
    confidence: { level: 'not_enough_information', text: 'Not enough information yet.' },
    can_capture_label: true,
  });
});

it('keeps the last result when the person simply comes back', async () => {
  await scanOnce();
  focus();
  expect(screen.queryByTestId('scan-camera')).toBeNull();
});

it('returns to the camera once when a fresh scan was asked for', async () => {
  await scanOnce();
  requestFreshScan();
  focus();
  expect(await screen.findByTestId('scan-camera')).toBeTruthy();
  expect(consumeFreshScanRequest()).toBe(false);
});
