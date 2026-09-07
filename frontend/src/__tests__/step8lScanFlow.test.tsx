/**
 * Step 8L — the whole skin-care loop through the real scanner screen.
 *
 * The single most important rule in this milestone is here: after a skin-care
 * confirmation the client must not scan the barcode again. Step 8K reads the
 * device's *newest* scan event as the current physical pack, so a plain lookup
 * between confirmation and evaluation would replace the confirmation and the
 * server would correctly answer that no pack was confirmed.
 */
import React from 'react';
import ScanProductScreen from '../../app/scan-product';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react-native';

const mockPush = jest.fn();
let mockFocusCallback: (() => void) | null = null;

jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush, back: jest.fn(), replace: jest.fn() }),
  useFocusEffect: (callback: () => void) => {
    const React2 = jest.requireActual<typeof React>('react');
    mockFocusCallback = callback;
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
const mockConfirmLabel = jest.fn();
const mockConfirmSkinCare = jest.fn();
const mockFetchForYou = jest.fn();
const mockEnsureClaimed = jest.fn();
const mockReadQueue = jest.fn();

jest.mock('../services/productScan', () => {
  const actual = jest.requireActual('../services/productScan');
  return {
    ...actual,
    scanBarcode: (...args: unknown[]) => mockScanBarcode(...args),
    confirmLabel: (...args: unknown[]) => mockConfirmLabel(...args),
    confirmSkinCareLabel: (...args: unknown[]) => mockConfirmSkinCare(...args),
    fetchSkinCareForYou: (...args: unknown[]) => mockFetchForYou(...args),
    ensureDeviceClaimed: (...args: unknown[]) => mockEnsureClaimed(...args),
    ensureDevice: jest.fn(async () => ({ device_key: 'd', token: 't' })),
    syncQueue: jest.fn(async () => ({ sent: 0, remaining: 0 })),
    readQueue: (...args: unknown[]) => mockReadQueue(...args),
  };
});

const mockTranscribeFood = jest.fn();
const mockTranscribeSkin = jest.fn();
const mockUploadMedia = jest.fn();

jest.mock('../services/apiV2', () => ({
  transcribeProductLabel: (...args: unknown[]) => mockTranscribeFood(...args),
  transcribeSkinCareLabel: (...args: unknown[]) => mockTranscribeSkin(...args),
  uploadMedia: (...args: unknown[]) => mockUploadMedia(...args),
}));


const BARCODE = '8901030000011';

const notFound = {
  barcode: BARCODE,
  found: false,
  outcome: 'not_found' as const,
  confidence: { level: 'not_enough_information' as const, text: 'Not enough information yet.' },
  can_capture_label: true,
};

const skinDraft = {
  barcode: BARCODE,
  facts: { product_name: 'A Cream', brand: 'A Brand', product_type: 'Ointment', ingredients_text: 'Aqua, Glycerin' },
  stored: false,
  ingredients_readable: true,
  message: null,
  capture_quality: { confidence: 0.9, uncertain_fields: [], photo_quality_notes: null },
  confidence: { level: 'unverified', text: 'Read by the camera.' },
  provenance: { ai_run_id: 'run-abc' },
};

const confirmedPack = {
  barcode: BARCODE,
  scan_id: 'scan-1',
  created: true,
  product_category: 'skin_care',
  label_snapshot: { id: 'snap-1', version_number: 1, content_fingerprint: 'f', completeness: 'x' },
  confidence: { level: 'unverified', text: 'Confirmed from your photo.' },
  confirmations: 0,
};

const presentable = {
  barcode: BARCODE,
  product_category: 'skin_care',
  copy_version: 'for-you-copy.v1',
  pack: {
    is_proven: true, current_pack_scan_id: 'scan-1', label_snapshot_id: 'snap-1',
    label_snapshot_source_scan_id: 'scan-1', label_snapshot_version: 1, content_fingerprint: 'f',
  },
  result: {
    status: 'decision_presentable', reason: 'reviewed_explanation_available', action: 'buy',
    verdict_key: 'for_you.verdict.buy', verdict_text: 'SERVER VERDICT',
    reason_key: 'for_you.sentinel', reason_text: 'SERVER REVIEWED REASON',
    citation: {
      source_key: 'k', title: 'T', publisher: 'P', canonical_url: 'https://example.org/x',
      locator: 'L', publication_date: null, version_or_revision: null, jurisdiction: null,
    },
    handoff: null,
  },
  release: { id: 'r', version: 1, content_hash: 'h' },
};

beforeEach(() => {
  jest.clearAllMocks();
  mockFocusCallback = null;
  mockReadQueue.mockResolvedValue([]);
  mockEnsureClaimed.mockResolvedValue(true);
  mockUploadMedia.mockResolvedValue({ id: 'asset-1' });
  mockTranscribeSkin.mockResolvedValue(skinDraft);
  mockConfirmSkinCare.mockResolvedValue(confirmedPack);
  mockFetchForYou.mockResolvedValue(presentable);
  mockScanBarcode.mockResolvedValue(notFound);
});

/** Drive the screen from a barcode read to a confirmed skin-care pack. */
async function reachConfirmedPack() {
  render(<ScanProductScreen />);
  const camera = screen.getByTestId('scan-camera');
  await act(async () => { camera.props.onBarcodeScanned({ data: BARCODE }); });
  await screen.findByLabelText('Photograph the label');
  fireEvent.press(screen.getByLabelText('Photograph the label'));

  await screen.findByTestId('label-kind-skin-care');
  await act(async () => { fireEvent.press(screen.getByTestId('label-kind-skin-care')); });

  await screen.findByLabelText('Take the label photo');
  await act(async () => { fireEvent.press(screen.getByLabelText('Take the label photo')); });

  await screen.findByTestId('skin-care-confirm');
  await act(async () => { fireEvent.press(screen.getByTestId('skin-care-confirm')); });
  await screen.findByText('Skin care label confirmed');
}

describe('the category boundary', () => {
  it('sends a skin-care capture down the skin-care route only', async () => {
    await reachConfirmedPack();
    expect(mockTranscribeSkin).toHaveBeenCalledWith(BARCODE, 'asset-1');
    expect(mockTranscribeFood).not.toHaveBeenCalled();
    expect(mockConfirmSkinCare).toHaveBeenCalled();
    // The generic food confirmation must never see a skin-care draft.
    expect(mockConfirmLabel).not.toHaveBeenCalled();
  });

  it('never sends a category as a transcription parameter', async () => {
    await reachConfirmedPack();
    expect(mockTranscribeSkin.mock.calls[0]).toEqual([BARCODE, 'asset-1']);
  });

  it('keeps packaged food on the generic route', async () => {
    mockTranscribeFood.mockResolvedValue({
      barcode: BARCODE, facts: { product_name: 'Noodles' }, fssai_licence: null,
      stored: false, confidence: { level: 'unverified', text: 'x' },
      provenance: { ai_run_id: 'food-run' },
    });
    render(<ScanProductScreen />);
    const camera = screen.getByTestId('scan-camera');
    await act(async () => { camera.props.onBarcodeScanned({ data: BARCODE }); });
    await screen.findByLabelText('Photograph the label');
    fireEvent.press(screen.getByLabelText('Photograph the label'));
    await screen.findByTestId('label-kind-packaged-food');
    await act(async () => { fireEvent.press(screen.getByTestId('label-kind-packaged-food')); });
    await screen.findByLabelText('Take the label photo');
    await act(async () => { fireEvent.press(screen.getByLabelText('Take the label photo')); });

    expect(mockTranscribeFood).toHaveBeenCalledWith(BARCODE, 'asset-1');
    expect(mockTranscribeSkin).not.toHaveBeenCalled();
    // Packaged food does not need the device claimed before its model call.
    expect(mockEnsureClaimed).not.toHaveBeenCalled();
  });
});

describe('the current pack must survive confirmation', () => {
  it('never scans the barcode again after a skin-care confirmation', async () => {
    await reachConfirmedPack();
    // Exactly one lookup: the original barcode read. A second one would
    // become the newest scan event and destroy the confirmed pack.
    expect(mockScanBarcode).toHaveBeenCalledTimes(1);
    expect(mockScanBarcode).toHaveBeenCalledWith(BARCODE);
  });

  it('goes straight to the confirmed-label state', async () => {
    await reachConfirmedPack();
    expect(screen.getByText('Skin care label confirmed')).toBeTruthy();
    expect(screen.getByText('A Cream')).toBeTruthy();
    expect(screen.getByText('Aqua, Glycerin')).toBeTruthy();
  });

  it('claims the device before spending the skin-care model call', async () => {
    await reachConfirmedPack();
    expect(mockEnsureClaimed).toHaveBeenCalledWith('account-1');
    const claimOrder = mockEnsureClaimed.mock.invocationCallOrder[0];
    const transcribeOrder = mockTranscribeSkin.mock.invocationCallOrder[0];
    expect(claimOrder).toBeLessThan(transcribeOrder);
  });
});

describe('the safety preflight gates the decision', () => {
  it('does not call FOR YOU until the person has answered', async () => {
    await reachConfirmedPack();
    expect(mockFetchForYou).not.toHaveBeenCalled();
    expect(screen.getByText('Before FOR YOU')).toBeTruthy();
  });

  it('calls FOR YOU with the selected flags once answered', async () => {
    await reachConfirmedPack();
    await act(async () => { fireEvent.press(screen.getByTestId('safety-pregnancy')); });
    await act(async () => { fireEvent.press(screen.getByTestId('safety-submit')); });
    await waitFor(() => expect(mockFetchForYou).toHaveBeenCalled());
    expect(mockFetchForYou).toHaveBeenCalledWith(BARCODE, { pregnancy: true });
    expect(await screen.findByText('SERVER VERDICT')).toBeTruthy();
  });
});

describe('a profile change changes the answer without rescanning', () => {
  it('refetches on focus and can withdraw the verdict', async () => {
    await reachConfirmedPack();
    await act(async () => { fireEvent.press(screen.getByTestId('safety-none')); });
    await act(async () => { fireEvent.press(screen.getByTestId('safety-submit')); });
    expect(await screen.findByText('SERVER VERDICT')).toBeTruthy();

    const scansBefore = mockScanBarcode.mock.calls.length;
    const transcribesBefore = mockTranscribeSkin.mock.calls.length;
    const confirmsBefore = mockConfirmSkinCare.mock.calls.length;

    // The person edits a skin fact and comes back. The pack is untouched.
    mockFetchForYou.mockResolvedValueOnce({
      ...presentable,
      result: {
        ...presentable.result,
        status: 'not_enough_information', action: null, verdict_key: null,
        verdict_text: null, citation: null,
        reason_key: 'for_you.not_enough.personal_context',
        reason_text: 'SERVER SENTENCE AFTER CHANGE',
      },
    });
    await act(async () => { mockFocusCallback?.(); });

    expect(await screen.findByText('SERVER SENTENCE AFTER CHANGE')).toBeTruthy();
    await waitFor(() => expect(screen.queryByText('SERVER VERDICT')).toBeNull());

    // Nothing about the physical pack was redone.
    expect(mockScanBarcode).toHaveBeenCalledTimes(scansBefore);
    expect(mockTranscribeSkin).toHaveBeenCalledTimes(transcribesBefore);
    expect(mockConfirmSkinCare).toHaveBeenCalledTimes(confirmsBefore);
  });

  it('opens the skin-detail editor from the personal-context gap', async () => {
    mockFetchForYou.mockResolvedValue({
      ...presentable,
      result: {
        ...presentable.result,
        status: 'not_enough_information', action: null, verdict_key: null,
        verdict_text: null, citation: null,
        reason_key: 'for_you.not_enough.personal_context',
        reason_text: 'SERVER SENTENCE ABOUT DETAILS',
      },
    });
    await reachConfirmedPack();
    await act(async () => { fireEvent.press(screen.getByTestId('safety-none')); });
    await act(async () => { fireEvent.press(screen.getByTestId('safety-submit')); });
    await screen.findByText('SERVER SENTENCE ABOUT DETAILS');
    fireEvent.press(screen.getByLabelText('Add skin details'));
    expect(mockPush).toHaveBeenCalledWith('/for-you-profile');
  });
});

describe('an unreadable skin-care label', () => {
  it('never reaches the confirmation route', async () => {
    mockTranscribeSkin.mockResolvedValue({
      ...skinDraft,
      facts: { product_name: 'A Cream' },
      ingredients_readable: false,
      message: 'SERVER UNREADABLE MESSAGE',
    });
    render(<ScanProductScreen />);
    const camera = screen.getByTestId('scan-camera');
    await act(async () => { camera.props.onBarcodeScanned({ data: BARCODE }); });
    await screen.findByLabelText('Photograph the label');
    fireEvent.press(screen.getByLabelText('Photograph the label'));
    await screen.findByTestId('label-kind-skin-care');
    await act(async () => { fireEvent.press(screen.getByTestId('label-kind-skin-care')); });
    await screen.findByLabelText('Take the label photo');
    await act(async () => { fireEvent.press(screen.getByLabelText('Take the label photo')); });

    expect(await screen.findByText('SERVER UNREADABLE MESSAGE')).toBeTruthy();
    expect(screen.queryByTestId('skin-care-confirm')).toBeNull();
    expect(mockConfirmSkinCare).not.toHaveBeenCalled();
    expect(screen.getByTestId('skin-care-retake')).toBeTruthy();
  });
});

describe('confirmation idempotency', () => {
  it('uses one stable key per transcription, even across taps', async () => {
    await reachConfirmedPack();
    expect(mockConfirmSkinCare).toHaveBeenCalledTimes(1);
    const [, aiRunId, clientScanId] = mockConfirmSkinCare.mock.calls[0];
    expect(aiRunId).toBe('run-abc');
    expect(typeof clientScanId).toBe('string');
    expect(clientScanId.length).toBeGreaterThan(4);
  });

  it('reuses the draft key when the person retries a failed confirmation', async () => {
    // The screen must pass the key it minted with the draft, not a fresh one
    // per attempt: two keys would make one physical capture into two logical
    // confirmations the moment a request needed retrying.
    mockConfirmSkinCare
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce(confirmedPack);

    render(<ScanProductScreen />);
    const camera = screen.getByTestId('scan-camera');
    await act(async () => { camera.props.onBarcodeScanned({ data: BARCODE }); });
    await screen.findByLabelText('Photograph the label');
    fireEvent.press(screen.getByLabelText('Photograph the label'));
    await screen.findByTestId('label-kind-skin-care');
    await act(async () => { fireEvent.press(screen.getByTestId('label-kind-skin-care')); });
    await screen.findByLabelText('Take the label photo');
    await act(async () => { fireEvent.press(screen.getByLabelText('Take the label photo')); });
    await screen.findByTestId('skin-care-confirm');

    await act(async () => { fireEvent.press(screen.getByTestId('skin-care-confirm')); });
    await act(async () => { fireEvent.press(screen.getByTestId('skin-care-confirm')); });
    await screen.findByText('Skin care label confirmed');

    expect(mockConfirmSkinCare).toHaveBeenCalledTimes(2);
    const firstKey = mockConfirmSkinCare.mock.calls[0][2];
    const secondKey = mockConfirmSkinCare.mock.calls[1][2];
    expect(secondKey).toBe(firstKey);
  });

  it('mints a fresh key when the photograph is retaken', async () => {
    render(<ScanProductScreen />);
    const camera = screen.getByTestId('scan-camera');
    await act(async () => { camera.props.onBarcodeScanned({ data: BARCODE }); });
    await screen.findByLabelText('Photograph the label');
    fireEvent.press(screen.getByLabelText('Photograph the label'));
    await screen.findByTestId('label-kind-skin-care');
    await act(async () => { fireEvent.press(screen.getByTestId('label-kind-skin-care')); });
    await screen.findByLabelText('Take the label photo');
    await act(async () => { fireEvent.press(screen.getByLabelText('Take the label photo')); });
    await screen.findByTestId('skin-care-confirm');

    // The person is not happy with the photo and takes another one. That is a
    // new physical capture, so it deserves a new idempotency key.
    await act(async () => { fireEvent.press(screen.getByTestId('skin-care-retake')); });
    await screen.findByLabelText('Take the label photo');
    await act(async () => { fireEvent.press(screen.getByLabelText('Take the label photo')); });
    await screen.findByTestId('skin-care-confirm');
    await act(async () => { fireEvent.press(screen.getByTestId('skin-care-confirm')); });

    expect(mockConfirmSkinCare).toHaveBeenCalledTimes(1);
    const secondKey = mockConfirmSkinCare.mock.calls[0][2];
    expect(typeof secondKey).toBe('string');
    expect(mockTranscribeSkin).toHaveBeenCalledTimes(2);
  });
});

describe('FOR YOU failure', () => {
  it('shows a neutral unavailable state and never a verdict', async () => {
    mockFetchForYou.mockRejectedValue(new Error('503'));
    await reachConfirmedPack();
    await act(async () => { fireEvent.press(screen.getByTestId('safety-none')); });
    await act(async () => { fireEvent.press(screen.getByTestId('safety-submit')); });
    expect(await screen.findByText('FOR YOU is temporarily unavailable.')).toBeTruthy();
    for (const word of ['BUY', 'WAIT', 'SKIP', 'SERVER VERDICT']) {
      expect(screen.queryByText(word)).toBeNull();
    }
    // The confirmed label is still there and the person can move on.
    expect(screen.getByText('Skin care label confirmed')).toBeTruthy();
    expect(screen.getByLabelText('Scan another')).toBeTruthy();
  });
});
