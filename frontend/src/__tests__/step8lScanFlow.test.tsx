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
import { openAuthGeneration } from '../store/authGeneration';

const mockPush = jest.fn();
let mockUserId = 'account-1';
let mockFocusCallback: (() => void | (() => void)) | null = null;
let mockBlurCallback: (() => void) | null = null;

jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush, back: jest.fn(), replace: jest.fn() }),
  useFocusEffect: (callback: () => void | (() => void)) => {
    const React2 = jest.requireActual<typeof React>('react');
    mockFocusCallback = callback;
    React2.useEffect(() => {
      const cleanup = callback();
      if (typeof cleanup === 'function') mockBlurCallback = cleanup;
      return cleanup;
    }, [callback]);
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
  useUserStore: () => ({ userId: mockUserId }),
}));

const mockScanBarcode = jest.fn();
const mockConfirmLabel = jest.fn();
const mockConfirmSkinCare = jest.fn();
const mockFetchForYou = jest.fn();
const mockEnsureClaimed = jest.fn();
const mockReadQueue = jest.fn();
const mockSettleScanEvents = jest.fn();

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
    settleScanEvents: (...args: unknown[]) => mockSettleScanEvents(...args),
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


/**
 * These are multi-step journeys through a real screen — barcode read, category
 * choice, ledger settlement, photograph, transcription, confirmation — and the
 * first one in the file also pays the module-transform cost. Jest's 5s default
 * is comfortable locally and marginal on a shared CI runner, where the first
 * test timed out at 5.0s while doing nothing wrong. The limit is raised for
 * this file only; nothing here waits on a real timer, so a genuine hang still
 * fails rather than hiding.
 */
jest.setTimeout(30000);

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
  label_snapshot: {
    id: 'snap-1', source_scan_id: 'scan-1', version_number: 1,
    content_fingerprint: 'f', completeness: 'x',
  },
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
  mockUserId = 'account-1';
  openAuthGeneration('account-1');
  jest.clearAllMocks();
  mockFocusCallback = null;
  mockBlurCallback = null;
  mockReadQueue.mockResolvedValue([]);
  mockEnsureClaimed.mockResolvedValue(true);
  mockSettleScanEvents.mockResolvedValue(true);
  mockUploadMedia.mockResolvedValue({ id: 'asset-1' });
  mockTranscribeSkin.mockResolvedValue(skinDraft);
  mockConfirmSkinCare.mockResolvedValue(confirmedPack);
  mockFetchForYou.mockResolvedValue(presentable);
  mockScanBarcode.mockResolvedValue(notFound);
});

/** Drive the screen from a barcode read to a confirmed skin-care pack. */
async function confirmFromCamera(barcode = BARCODE) {
  const camera = screen.getByTestId('scan-camera');
  await act(async () => { camera.props.onBarcodeScanned({ data: barcode }); });
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

async function reachConfirmedPack() {
  render(<ScanProductScreen />);
  await confirmFromCamera();
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function requestForYou() {
  await act(async () => { fireEvent.press(screen.getByTestId('safety-none')); });
  await act(async () => { fireEvent.press(screen.getByTestId('safety-submit')); });
}

async function confirmOtherPack(barcode: string) {
  fireEvent.press(screen.getByLabelText('Scan another'));
  await confirmFromCamera(barcode);
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

describe('the plain scan must settle before a capture', () => {
  it('settles this barcode before spending the model call', async () => {
    await reachConfirmedPack();
    expect(mockSettleScanEvents).toHaveBeenCalledWith(BARCODE);
    const settleOrder = mockSettleScanEvents.mock.invocationCallOrder[0];
    const transcribeOrder = mockTranscribeSkin.mock.invocationCallOrder[0];
    expect(settleOrder).toBeLessThan(transcribeOrder);
  });

  it('never starts the model call when the scan cannot be settled', async () => {
    mockSettleScanEvents.mockResolvedValue(false);
    render(<ScanProductScreen />);
    const camera = screen.getByTestId('scan-camera');
    await act(async () => { camera.props.onBarcodeScanned({ data: BARCODE }); });
    await screen.findByLabelText('Photograph the label');
    fireEvent.press(screen.getByLabelText('Photograph the label'));
    await screen.findByTestId('label-kind-skin-care');
    await act(async () => { fireEvent.press(screen.getByTestId('label-kind-skin-care')); });

    // No photograph, no model call, no confirmation — and a retryable state.
    expect(mockTranscribeSkin).not.toHaveBeenCalled();
    expect(mockConfirmSkinCare).not.toHaveBeenCalled();
    expect(screen.queryByLabelText('Take the label photo')).toBeNull();
    expect(await screen.findByText(
      'We could not finish saving this scan. Check your connection and try again.',
    )).toBeTruthy();
    // Never a verdict.
    for (const word of ['BUY', 'WAIT', 'SKIP']) {
      expect(screen.queryByText(word)).toBeNull();
    }
    // Still retryable: the choice is still on screen.
    expect(screen.getByTestId('label-kind-skin-care')).toBeTruthy();
  });

  it('gates the packaged-food path too, before its model call', async () => {
    // Lane C. This used to assert the opposite — that food was never settled —
    // which is exactly the race: a food lookup's plain event still in flight
    // lands after the confirmation, becomes the newest event, and withdraws
    // the pack the person just confirmed. Food needs the same proof as skin
    // care, and gets it before the photograph is spent.
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

    expect(mockSettleScanEvents).toHaveBeenCalledWith(BARCODE);
    expect(mockTranscribeFood).toHaveBeenCalled();
    const settleOrder = mockSettleScanEvents.mock.invocationCallOrder[0];
    const transcribeOrder = mockTranscribeFood.mock.invocationCallOrder[0];
    expect(settleOrder).toBeLessThan(transcribeOrder);
    // Food still claims nothing: settling is not ownership.
    expect(mockEnsureClaimed).not.toHaveBeenCalled();
  });

  it('never turns a settlement failure into a confirmed pack, even on retry', async () => {
    // The barrier refuses, the person tries the same choice again, and it
    // refuses again. A retry must reach the same refusal, not manufacture a
    // confirmation out of a second attempt.
    mockSettleScanEvents.mockResolvedValue(false);
    render(<ScanProductScreen />);
    const camera = screen.getByTestId('scan-camera');
    await act(async () => { camera.props.onBarcodeScanned({ data: BARCODE }); });
    await screen.findByLabelText('Photograph the label');
    fireEvent.press(screen.getByLabelText('Photograph the label'));
    await screen.findByTestId('label-kind-skin-care');

    await act(async () => { fireEvent.press(screen.getByTestId('label-kind-skin-care')); });
    expect(await screen.findByTestId('scan-settlement-failed')).toBeTruthy();

    await act(async () => { fireEvent.press(screen.getByTestId('label-kind-skin-care')); });
    expect(await screen.findByTestId('scan-settlement-failed')).toBeTruthy();

    expect(mockSettleScanEvents).toHaveBeenCalledTimes(2);
    expect(mockTranscribeSkin).not.toHaveBeenCalled();
    expect(mockConfirmSkinCare).not.toHaveBeenCalled();
    expect(mockFetchForYou).not.toHaveBeenCalled();
    expect(screen.queryByText('Skin care label confirmed')).toBeNull();
    expect(screen.queryByTestId('skin-care-confirmed-card')).toBeNull();
    expect(screen.queryByTestId('for-you-card')).toBeNull();

    // And once the ledger does settle, the same choice proceeds normally.
    mockSettleScanEvents.mockResolvedValue(true);
    await act(async () => { fireEvent.press(screen.getByTestId('label-kind-skin-care')); });
    await screen.findByTestId('label-capture-take-photo');
    await act(async () => { fireEvent.press(screen.getByTestId('label-capture-take-photo')); });
    await screen.findByTestId('skin-care-confirm');
    expect(mockTranscribeSkin).toHaveBeenCalledTimes(1);
  });

  it('leaves the confirmation as the last event-producing operation', async () => {
    await reachConfirmedPack();
    const settleOrder = mockSettleScanEvents.mock.invocationCallOrder[0];
    const confirmOrder = mockConfirmSkinCare.mock.invocationCallOrder[0];
    expect(settleOrder).toBeLessThan(confirmOrder);
    expect(mockScanBarcode).toHaveBeenCalledTimes(1);
  });
});

describe('the safety preflight gates the decision', () => {
  it('does not call FOR YOU until the person has answered', async () => {
    await reachConfirmedPack();
    expect(mockFetchForYou).not.toHaveBeenCalled();
    expect(screen.getByText('Before FOR YOU')).toBeTruthy();
  });

  it('makes exactly one request for one deliberate answer', async () => {
    await reachConfirmedPack();
    expect(mockFetchForYou).toHaveBeenCalledTimes(0);

    await act(async () => { fireEvent.press(screen.getByTestId('safety-pregnancy')); });
    await act(async () => { fireEvent.press(screen.getByTestId('safety-submit')); });
    await waitFor(() => expect(mockFetchForYou).toHaveBeenCalled());

    // One answer, one governed evaluation. Two would race, and the loser's
    // response could overwrite the winner's.
    expect(mockFetchForYou).toHaveBeenCalledTimes(1);
    expect(mockFetchForYou).toHaveBeenCalledWith(BARCODE, { pregnancy: true });
    expect(await screen.findByText('SERVER VERDICT')).toBeTruthy();
  });

  it('spends no scan, transcription or confirmation on the evaluation', async () => {
    await reachConfirmedPack();
    const before = {
      scan: mockScanBarcode.mock.calls.length,
      transcribe: mockTranscribeSkin.mock.calls.length,
      confirm: mockConfirmSkinCare.mock.calls.length,
    };
    await act(async () => { fireEvent.press(screen.getByTestId('safety-none')); });
    await act(async () => { fireEvent.press(screen.getByTestId('safety-submit')); });
    await waitFor(() => expect(mockFetchForYou).toHaveBeenCalledTimes(1));
    expect(mockScanBarcode).toHaveBeenCalledTimes(before.scan);
    expect(mockTranscribeSkin).toHaveBeenCalledTimes(before.transcribe);
    expect(mockConfirmSkinCare).toHaveBeenCalledTimes(before.confirm);
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

    // Exactly two evaluations in total: one for the answer, one for the
    // return. Not three, which is what a direct call plus a focus effect gave.
    expect(mockFetchForYou).toHaveBeenCalledTimes(2);
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

describe('FOR YOU request and pack authority', () => {
  const otherBarcode = '8901030000028';
  const answer = (barcode: string, verdict: string) => ({
    ...presentable,
    barcode,
    pack: {
      ...presentable.pack,
      current_pack_scan_id: barcode === BARCODE ? 'scan-1' : `scan-${barcode}`,
      label_snapshot_id: barcode === BARCODE ? 'snap-1' : `snapshot-${barcode}`,
      label_snapshot_source_scan_id: barcode === BARCODE ? 'scan-1' : `scan-${barcode}`,
    },
    result: { ...presentable.result, verdict_text: verdict },
  });

  function serveTwoPacks() {
    mockScanBarcode.mockImplementation(async (barcode: string) => ({ ...notFound, barcode }));
    mockTranscribeSkin.mockImplementation(async (barcode: string) => ({ ...skinDraft, barcode }));
    mockConfirmSkinCare.mockImplementation(async (barcode: string) => ({
      ...confirmedPack, barcode, scan_id: `scan-${barcode}`,
      label_snapshot: {
        ...confirmedPack.label_snapshot, id: `snapshot-${barcode}`,
        source_scan_id: `scan-${barcode}`,
      },
    }));
  }

  it('discards A success after B succeeds, without replacing B or showing A', async () => {
    serveTwoPacks();
    const a = deferred<typeof presentable>();
    const b = deferred<typeof presentable>();
    mockFetchForYou.mockImplementationOnce(() => a.promise).mockImplementationOnce(() => b.promise);
    await reachConfirmedPack();
    await requestForYou();
    await waitFor(() => expect(mockFetchForYou).toHaveBeenCalledWith(BARCODE, {}));
    await confirmOtherPack(otherBarcode);
    await requestForYou();
    await waitFor(() => expect(mockFetchForYou).toHaveBeenCalledWith(otherBarcode, {}));

    await act(async () => { b.resolve(answer(otherBarcode, 'B VERDICT')); });
    expect(await screen.findByText('B VERDICT')).toBeTruthy();
    await act(async () => { a.resolve(answer(BARCODE, 'A VERDICT')); });
    expect(screen.getByText('B VERDICT')).toBeTruthy();
    expect(screen.queryByText('A VERDICT')).toBeNull();
  });

  it('discards A failure after B succeeds, including A retry/unavailable state', async () => {
    serveTwoPacks();
    const a = deferred<typeof presentable>();
    const b = deferred<typeof presentable>();
    mockFetchForYou.mockImplementationOnce(() => a.promise).mockImplementationOnce(() => b.promise);
    await reachConfirmedPack();
    await requestForYou();
    await confirmOtherPack(otherBarcode);
    await requestForYou();
    await act(async () => { b.resolve(answer(otherBarcode, 'B VERDICT')); });
    await screen.findByText('B VERDICT');
    await act(async () => { a.reject(new Error('A failed')); });
    expect(screen.getByText('B VERDICT')).toBeTruthy();
    expect(screen.queryByTestId('for-you-technical-unavailable')).toBeNull();
    expect(screen.queryByTestId('for-you-retry')).toBeNull();
  });

  it('does not show A unavailable/retry when its empty response follows B', async () => {
    serveTwoPacks();
    const a = deferred<typeof presentable | null>();
    const b = deferred<typeof presentable>();
    mockFetchForYou.mockImplementationOnce(() => a.promise).mockImplementationOnce(() => b.promise);
    await reachConfirmedPack();
    await requestForYou();
    await confirmOtherPack(otherBarcode);
    await requestForYou();
    await act(async () => { b.resolve(answer(otherBarcode, 'B VERDICT')); });
    await screen.findByText('B VERDICT');
    await act(async () => { a.resolve(null); });
    expect(screen.getByText('B VERDICT')).toBeTruthy();
    expect(screen.queryByTestId('for-you-retry')).toBeNull();
  });

  it('cannot finish B loading when stale A completes first', async () => {
    serveTwoPacks();
    const a = deferred<typeof presentable>();
    const b = deferred<typeof presentable>();
    mockFetchForYou.mockImplementationOnce(() => a.promise).mockImplementationOnce(() => b.promise);
    await reachConfirmedPack();
    await requestForYou();
    await confirmOtherPack(otherBarcode);
    await requestForYou();
    await act(async () => { a.resolve(answer(BARCODE, 'A VERDICT')); });
    expect(screen.getByTestId('for-you-loading')).toBeTruthy();
    expect(screen.queryByText('A VERDICT')).toBeNull();
    await act(async () => { b.resolve(answer(otherBarcode, 'B VERDICT')); });
    expect(await screen.findByText('B VERDICT')).toBeTruthy();
  });

  it('keeps the newer request for the same barcode authoritative', async () => {
    const first = deferred<typeof presentable>();
    const second = deferred<typeof presentable>();
    mockFetchForYou.mockImplementationOnce(() => first.promise).mockImplementationOnce(() => second.promise);
    await reachConfirmedPack();
    await requestForYou();
    await act(async () => { mockFocusCallback?.(); });
    await waitFor(() => expect(mockFetchForYou).toHaveBeenCalledTimes(2));
    await act(async () => { second.resolve(answer(BARCODE, 'NEWER VERDICT')); });
    await screen.findByText('NEWER VERDICT');
    await act(async () => { first.resolve(answer(BARCODE, 'OLDER VERDICT')); });
    expect(screen.getByText('NEWER VERDICT')).toBeTruthy();
    expect(screen.queryByText('OLDER VERDICT')).toBeNull();
  });

  it('discards a completion after auth generation changes', async () => {
    const pending = deferred<typeof presentable>();
    mockFetchForYou.mockImplementationOnce(() => pending.promise);
    await reachConfirmedPack();
    await requestForYou();
    openAuthGeneration('another-account');
    try {
      await act(async () => { pending.resolve(answer(BARCODE, 'OLD ACCOUNT VERDICT')); });
      expect(screen.queryByText('OLD ACCOUNT VERDICT')).toBeNull();
    } finally {
      openAuthGeneration('account-1');
    }
  });

  it('discards a completion after navigation blur while the screen remains mounted', async () => {
    const pending = deferred<typeof presentable>();
    mockFetchForYou.mockImplementationOnce(() => pending.promise);
    await reachConfirmedPack();
    await requestForYou();
    expect(mockBlurCallback).not.toBeNull();
    mockBlurCallback?.();
    await act(async () => { pending.resolve(answer(BARCODE, 'BLURRED VERDICT')); });
    expect(screen.queryByText('BLURRED VERDICT')).toBeNull();
    expect(screen.queryByTestId('for-you-technical-unavailable')).toBeNull();
    expect(screen.queryByTestId('for-you-retry')).toBeNull();
    expect(screen.getByTestId('for-you-loading')).toBeTruthy();
  });

  it('renders an exact full response-pack match', async () => {
    await reachConfirmedPack();
    await requestForYou();
    expect(await screen.findByText('SERVER VERDICT')).toBeTruthy();
    expect(screen.queryByTestId('for-you-technical-unavailable')).toBeNull();
  });

  it('accepts a deduplicated label whose source scan differs from the current pack scan', async () => {
    mockConfirmSkinCare.mockResolvedValue({
      ...confirmedPack,
      label_snapshot: { ...confirmedPack.label_snapshot, source_scan_id: 'earlier-capture' },
    });
    mockFetchForYou.mockResolvedValue({
      ...presentable,
      pack: { ...presentable.pack, label_snapshot_source_scan_id: 'earlier-capture' },
    });
    await reachConfirmedPack();
    await requestForYou();
    expect(await screen.findByText('SERVER VERDICT')).toBeTruthy();
  });

  it('fails closed if confirmation lacks the snapshot source-scan authority', async () => {
    mockConfirmSkinCare.mockResolvedValue({
      ...confirmedPack,
      label_snapshot: { ...confirmedPack.label_snapshot, source_scan_id: undefined },
    });
    await reachConfirmedPack();
    await requestForYou();
    expect(await screen.findByTestId('for-you-technical-unavailable')).toBeTruthy();
    expect(screen.queryByText('SERVER VERDICT')).toBeNull();
  });

  it('same barcode is not sufficient physical-pack authority', async () => {
    const pending = deferred<typeof presentable>();
    mockFetchForYou.mockImplementationOnce(() => pending.promise);
    await reachConfirmedPack();
    await requestForYou();
    await act(async () => {
      pending.resolve({
        ...presentable,
        pack: { ...presentable.pack, current_pack_scan_id: 'newer-pack-same-barcode' },
      });
    });
    expect(await screen.findByTestId('for-you-technical-unavailable')).toBeTruthy();
    expect(screen.getByTestId('for-you-retry')).toBeTruthy();
    expect(screen.queryByText('SERVER VERDICT')).toBeNull();
    expect(screen.queryByText('SERVER REVIEWED REASON')).toBeNull();
  });

  it.each([
    ['unproven pack', { is_proven: false }],
    ['different snapshot ID', { label_snapshot_id: 'another-snapshot' }],
    ['different source scan ID', { label_snapshot_source_scan_id: 'another-source' }],
    ['different snapshot version', { label_snapshot_version: 2 }],
    ['different fingerprint', { content_fingerprint: 'another-formula' }],
    ['missing source scan ID', { label_snapshot_source_scan_id: null }],
  ] as [string, Partial<typeof presentable.pack>][])('rejects %s without a personalized claim', async (_case, changedPack) => {
    mockFetchForYou.mockResolvedValue({ ...presentable, pack: { ...presentable.pack, ...changedPack } });
    await reachConfirmedPack();
    await requestForYou();
    expect(await screen.findByTestId('for-you-technical-unavailable')).toBeTruthy();
    expect(screen.getByTestId('for-you-retry')).toBeTruthy();
    expect(screen.queryByText('SERVER VERDICT')).toBeNull();
    expect(screen.queryByText('SERVER REVIEWED REASON')).toBeNull();
  });

  it('rejects a response naming a different barcode', async () => {
    mockFetchForYou.mockResolvedValue({ ...presentable, barcode: otherBarcode });
    await reachConfirmedPack();
    await requestForYou();
    expect(await screen.findByTestId('for-you-technical-unavailable')).toBeTruthy();
    expect(screen.queryByText('SERVER VERDICT')).toBeNull();
  });

  it('same-account auth replacement ends only its own stale spinner', async () => {
    const pending = deferred<typeof presentable>();
    mockFetchForYou.mockImplementationOnce(() => pending.promise);
    await reachConfirmedPack();
    await requestForYou();
    expect(screen.getByTestId('for-you-loading')).toBeTruthy();
    openAuthGeneration('account-1');
    await act(async () => { pending.resolve(answer(BARCODE, 'OLD SESSION VERDICT')); });
    expect(screen.queryByText('OLD SESSION VERDICT')).toBeNull();
    expect(screen.queryByTestId('for-you-loading')).toBeNull();
    expect(screen.getByTestId('for-you-technical-unavailable')).toBeTruthy();
    expect(screen.getByTestId('for-you-retry')).toBeTruthy();
  });

  it('retries under the new same-account auth generation', async () => {
    const pending = deferred<typeof presentable>();
    mockFetchForYou.mockImplementationOnce(() => pending.promise)
      .mockResolvedValueOnce(answer(BARCODE, 'NEW SESSION VERDICT'));
    await reachConfirmedPack();
    await requestForYou();
    openAuthGeneration('account-1');
    await act(async () => { pending.resolve(answer(BARCODE, 'OLD SESSION VERDICT')); });
    fireEvent.press(await screen.findByTestId('for-you-retry'));
    expect(await screen.findByText('NEW SESSION VERDICT')).toBeTruthy();
    expect(screen.queryByText('OLD SESSION VERDICT')).toBeNull();
    expect(mockFetchForYou).toHaveBeenCalledTimes(2);
  });

  it('does not let an auth-stale A completion touch account B loading', async () => {
    serveTwoPacks();
    const first = deferred<typeof presentable>();
    const second = deferred<typeof presentable>();
    mockFetchForYou.mockImplementationOnce(() => first.promise).mockImplementationOnce(() => second.promise);
    const view = render(<ScanProductScreen />);
    await confirmFromCamera();
    await requestForYou();
    mockUserId = 'account-2';
    openAuthGeneration('account-2');
    view.rerender(<ScanProductScreen />);
    await confirmOtherPack(otherBarcode);
    await requestForYou();
    expect(screen.getByTestId('for-you-loading')).toBeTruthy();
    await act(async () => { first.resolve(answer(BARCODE, 'ACCOUNT A VERDICT')); });
    expect(screen.getByTestId('for-you-loading')).toBeTruthy();
    expect(screen.queryByTestId('for-you-retry')).toBeNull();
    expect(screen.queryByText('ACCOUNT A VERDICT')).toBeNull();
    await act(async () => { second.resolve(answer(otherBarcode, 'ACCOUNT B VERDICT')); });
    expect(await screen.findByText('ACCOUNT B VERDICT')).toBeTruthy();
  });

  it('does not let an auth-stale earlier request clear a newer request spinner', async () => {
    const first = deferred<typeof presentable>();
    const second = deferred<typeof presentable>();
    mockFetchForYou.mockImplementationOnce(() => first.promise).mockImplementationOnce(() => second.promise);
    await reachConfirmedPack();
    await requestForYou();
    openAuthGeneration('account-1');
    await act(async () => { mockFocusCallback?.(); });
    await waitFor(() => expect(mockFetchForYou).toHaveBeenCalledTimes(2));
    await act(async () => { first.resolve(answer(BARCODE, 'OLD SESSION VERDICT')); });
    expect(screen.getByTestId('for-you-loading')).toBeTruthy();
    expect(screen.queryByTestId('for-you-retry')).toBeNull();
    await act(async () => { second.resolve(answer(BARCODE, 'NEW SESSION VERDICT')); });
    expect(await screen.findByText('NEW SESSION VERDICT')).toBeTruthy();
  });
});
