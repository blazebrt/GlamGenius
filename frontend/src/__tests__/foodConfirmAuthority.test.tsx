/**
 * Lane C — a packaged-food confirmation must not destroy its own pack authority.
 *
 * The server reads the device's *newest* scan event as the pack in hand. So,
 * through the real scanner screen:
 *
 * * after a food confirmation the result is read back through the read-only
 *   `refreshBarcodeResult`, never `scanBarcode`, which would record a plain
 *   event that supersedes the confirmation;
 * * this barcode's plain event is settled before the model call and again
 *   before the confirmation is sent, and an unprovable settlement sends
 *   nothing and keeps the draft for a retry;
 * * one draft is confirmed under one `client_scan_id`, whatever happens to the
 *   first response; retaking the photograph mints a new one.
 */
import React from 'react';
import ScanProductScreen from '../../app/scan-product';
import { render, screen, fireEvent, act } from '@testing-library/react-native';

const mockPush = jest.fn();

jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush, back: jest.fn(), replace: jest.fn() }),
  useFocusEffect: (callback: () => void) => {
    const React2 = jest.requireActual<typeof React>('react');
    React2.useEffect(callback, [callback]);
  },
}));

jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0 }),
}));

jest.mock('expo-camera', () => {
  const { View } = jest.requireActual('react-native');
  const ReactActual = jest.requireActual('react');
  return {
    CameraView: ReactActual.forwardRef((props: Record<string, unknown>, ref: unknown) => {
      const setRef = ref as { current?: unknown } | null;
      if (setRef) setRef.current = { takePictureAsync: async () => ({ uri: 'file:///label.jpg' }) };
      return ReactActual.createElement(View, { testID: 'camera', ...props });
    }),
    useCameraPermissions: () => [{ granted: true, canAskAgain: true }, jest.fn()],
  };
});

jest.mock('../store/userStore', () => ({
  useUserStore: () => ({ userId: 'account-1' }),
}));

const mockScanBarcode = jest.fn();
const mockRefresh = jest.fn();
const mockConfirmLabel = jest.fn();
const mockEnsureClaimed = jest.fn();
const mockSettleScanEvents = jest.fn();

jest.mock('../services/productScan', () => {
  const actual = jest.requireActual('../services/productScan');
  return {
    ...actual,
    scanBarcode: (...args: unknown[]) => mockScanBarcode(...args),
    refreshBarcodeResult: (...args: unknown[]) => mockRefresh(...args),
    confirmLabel: (...args: unknown[]) => mockConfirmLabel(...args),
    ensureDeviceClaimed: (...args: unknown[]) => mockEnsureClaimed(...args),
    ensureDevice: jest.fn(async () => ({ device_key: 'd', token: 't' })),
    syncQueue: jest.fn(async () => ({ sent: 0, remaining: 0 })),
    readQueue: jest.fn(async () => []),
    settleScanEvents: (...args: unknown[]) => mockSettleScanEvents(...args),
  };
});

const mockTranscribeFood = jest.fn();
const mockUploadMedia = jest.fn();

jest.mock('../services/apiV2', () => ({
  transcribeProductLabel: (...args: unknown[]) => mockTranscribeFood(...args),
  transcribeSkinCareLabel: jest.fn(),
  uploadMedia: (...args: unknown[]) => mockUploadMedia(...args),
}));

/** Multi-step journeys through a real screen; see step8lScanFlow for why. */
jest.setTimeout(30000);

const BARCODE = '8901030000028';

const found = {
  barcode: BARCODE, found: true, outcome: 'found_off' as const,
  confidence: { level: 'unverified' as const, text: 'From one source.' },
  can_capture_label: true,
};
const refreshed = {
  ...found, outcome: 'label_captured' as const,
  confidence: { level: 'unverified' as const, text: 'Confirmed from your photo.' },
};

let transcriptions = 0;

beforeEach(() => {
  jest.clearAllMocks();
  transcriptions = 0;
  mockScanBarcode.mockResolvedValue(found);
  mockRefresh.mockResolvedValue(refreshed);
  mockSettleScanEvents.mockResolvedValue(true);
  mockEnsureClaimed.mockResolvedValue(true);
  mockUploadMedia.mockResolvedValue({ id: 'asset-1' });
  mockTranscribeFood.mockImplementation(async () => {
    transcriptions += 1;
    return {
      barcode: BARCODE, facts: { product_name: 'Oats' }, fssai_licence: null, stored: false,
      confidence: { level: 'unverified', text: 'x' },
      provenance: { ai_run_id: `food-run-${transcriptions}` },
    };
  });
  mockConfirmLabel.mockResolvedValue({ confidence: { level: 'unverified', text: 'Confirmed.' }, confirmations: 0 });
});

/** Barcode read → packaged food → photograph → the review of one draft. */
async function reachFoodReview() {
  render(<ScanProductScreen />);
  const camera = screen.getByTestId('scan-camera');
  await act(async () => { camera.props.onBarcodeScanned({ data: BARCODE }); });
  await screen.findByLabelText('Photograph the label');
  fireEvent.press(screen.getByLabelText('Photograph the label'));
  await screen.findByTestId('label-kind-packaged-food');
  await act(async () => { fireEvent.press(screen.getByTestId('label-kind-packaged-food')); });
  await screen.findByLabelText('Take the label photo');
  await act(async () => { fireEvent.press(screen.getByLabelText('Take the label photo')); });
  await screen.findByLabelText('Confirm this label');
}

async function pressConfirm() {
  await act(async () => { fireEvent.press(screen.getByLabelText('Confirm this label')); });
}

describe('the confirmed pack survives its own confirmation', () => {
  it('reads the result back read-only and never scans the barcode again', async () => {
    await reachFoodReview();
    await pressConfirm();
    await screen.findByText('Saved. Confirmed.');
    // Exactly one scan: the original barcode read. A second would be a new
    // plain event, newer than the confirmation.
    expect(mockScanBarcode).toHaveBeenCalledTimes(1);
    expect(mockRefresh).toHaveBeenCalledWith(BARCODE);
    const confirmOrder = mockConfirmLabel.mock.invocationCallOrder[0];
    expect(mockRefresh.mock.invocationCallOrder[0]).toBeGreaterThan(confirmOrder);
  });

  it('keeps the result it has when the read-only refresh cannot answer', async () => {
    mockRefresh.mockResolvedValueOnce(null);
    await reachFoodReview();
    await pressConfirm();
    await screen.findByText('Saved. Confirmed.');
    expect(mockScanBarcode).toHaveBeenCalledTimes(1);
  });
});

describe('the plain event is settled before the confirmation is sent', () => {
  it('settles this barcode before the model call and again before confirming', async () => {
    await reachFoodReview();
    expect(mockSettleScanEvents).toHaveBeenCalledTimes(1);
    expect(mockSettleScanEvents.mock.invocationCallOrder[0])
      .toBeLessThan(mockTranscribeFood.mock.invocationCallOrder[0]);
    await pressConfirm();
    expect(mockSettleScanEvents).toHaveBeenCalledTimes(2);
    expect(mockSettleScanEvents).toHaveBeenLastCalledWith(BARCODE);
    expect(mockSettleScanEvents.mock.invocationCallOrder[1])
      .toBeLessThan(mockConfirmLabel.mock.invocationCallOrder[0]);
  });

  it('never spends the photograph when the first settlement fails', async () => {
    mockSettleScanEvents.mockResolvedValueOnce(false);
    render(<ScanProductScreen />);
    const camera = screen.getByTestId('scan-camera');
    await act(async () => { camera.props.onBarcodeScanned({ data: BARCODE }); });
    await screen.findByLabelText('Photograph the label');
    fireEvent.press(screen.getByLabelText('Photograph the label'));
    await screen.findByTestId('label-kind-packaged-food');
    await act(async () => { fireEvent.press(screen.getByTestId('label-kind-packaged-food')); });
    expect(await screen.findByTestId('scan-settlement-failed')).toBeTruthy();
    expect(mockTranscribeFood).not.toHaveBeenCalled();
    expect(mockUploadMedia).not.toHaveBeenCalled();
  });

  it('sends nothing when settlement cannot be proven at confirmation, and keeps the draft', async () => {
    await reachFoodReview();
    mockSettleScanEvents.mockResolvedValueOnce(false);
    await pressConfirm();
    expect(await screen.findByTestId('scan-settlement-failed')).toBeTruthy();
    expect(mockConfirmLabel).not.toHaveBeenCalled();
    expect(mockRefresh).not.toHaveBeenCalled();
    // The draft is still on screen; a retry once the ledger settles confirms it.
    await pressConfirm();
    await screen.findByText('Saved. Confirmed.');
    expect(mockConfirmLabel).toHaveBeenCalledTimes(1);
  });
});

describe('one draft, one client_scan_id', () => {
  it('confirms a retried draft under the same key after a lost response', async () => {
    await reachFoodReview();
    // The server accepted, the response never arrived.
    mockConfirmLabel.mockRejectedValueOnce(new Error('network'));
    await pressConfirm();
    expect(await screen.findByTestId('label-confirm-error')).toBeTruthy();
    await pressConfirm();
    await screen.findByText('Saved. Confirmed.');

    expect(mockConfirmLabel).toHaveBeenCalledTimes(2);
    const [first, second] = mockConfirmLabel.mock.calls;
    expect(first[0]).toBe(BARCODE);
    expect(first[1]).toBe('food-run-1');
    expect(typeof first[2]).toBe('string');
    expect(first[2].length).toBeGreaterThan(0);
    expect(second).toEqual(first);
  });

  it('mints a new key when the label is photographed again', async () => {
    await reachFoodReview();
    mockConfirmLabel.mockRejectedValueOnce(new Error('network'));
    await pressConfirm();
    await screen.findByTestId('label-confirm-error');
    const firstKey = mockConfirmLabel.mock.calls[0][2];

    await act(async () => { fireEvent.press(screen.getByLabelText('Retake the label photo')); });
    await screen.findByLabelText('Take the label photo');
    await act(async () => { fireEvent.press(screen.getByLabelText('Take the label photo')); });
    await screen.findByLabelText('Confirm this label');
    await pressConfirm();
    await screen.findByText('Saved. Confirmed.');

    const retaken = mockConfirmLabel.mock.calls[1];
    expect(retaken[1]).toBe('food-run-2');
    expect(retaken[2]).not.toBe(firstKey);
  });
});
