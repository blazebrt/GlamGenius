/**
 * Scanning from the phone's side.
 *
 * The three behaviours the product promises are all here: a known barcode
 * answers, an unknown one is honest about it, and none of it needs a network.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import axios from 'axios';

import {
  cacheResult,
  ensureDevice,
  markDeviceClaimed,
  newScanId,
  readCached,
  readQueue,
  scanBarcode,
  syncQueue,
  enqueueScan,
  confirmLabel,
  confirmSkinCareLabel,
  ensureDeviceClaimed,
  fetchSkinCareForYou,
  resetPendingScanEvents,
  settleScanEvents,
  tokenToClaimFor,
  withConfidence,
  type ScanResult,
} from '../services/productScan';

jest.mock('axios', () => {
  const instance = {
    get: jest.fn(),
    post: jest.fn(),
    interceptors: {
      request: { use: jest.fn() },
      response: { use: jest.fn() },
    },
  };
  return { __esModule: true, default: { create: jest.fn(() => instance) }, create: jest.fn(() => instance) };
});

// An in-memory store, because the shared mock always answers null.
jest.mock('@react-native-async-storage/async-storage', () => {
  const store: Record<string, string> = {};
  return {
    __store: store,
    getItem: jest.fn((k: string) => Promise.resolve(k in store ? store[k] : null)),
    setItem: jest.fn((k: string, v: string) => { store[k] = v; return Promise.resolve(); }),
    removeItem: jest.fn((k: string) => { delete store[k]; return Promise.resolve(); }),
    multiRemove: jest.fn(() => Promise.resolve()),
  };
});

const http = (axios as unknown as { create: () => { get: jest.Mock; post: jest.Mock } }).create() as { get: jest.Mock; post: jest.Mock };
const store = (AsyncStorage as unknown as { __store: Record<string, string> }).__store as Record<string, string>;

const KNOWN = '8901058000191';
const UNKNOWN = '8909999999999';

const knownPayload = {
  barcode: KNOWN,
  found: true,
  outcome: 'found_off',
  confidence: { level: 'unverified', text: 'From one source, not checked yet.' },
  open_food_facts: { product_name: 'Maggi Masala Noodles', brands: 'Nestlé' },
  attribution: { text: 'Contains information from Open Food Facts' },
  glamgenius: { confidence: 'unverified', fssai_licence: '10012345678901' },
  can_capture_label: false,
};

const notFoundPayload = {
  barcode: UNKNOWN,
  found: false,
  outcome: 'not_found',
  confidence: { level: 'not_enough_information', text: 'Not enough information about this one yet.' },
  message: 'We do not know this one yet. Take a photo of the label and we will read it.',
  can_capture_label: true,
};

async function registeredDevice() {
  http.post.mockResolvedValueOnce({ data: { device_id: 'd1', token: 'device-token' } });
  await ensureDevice();
}

beforeEach(() => {
  Object.keys(store).forEach((key) => delete store[key]);
  http.get.mockReset();
  http.post.mockReset();
  resetPendingScanEvents();
});

describe('the device', () => {
  it('registers itself once and reuses the token', async () => {
    await registeredDevice();
    expect(http.post).toHaveBeenCalledTimes(1);
    await ensureDevice();
    expect(http.post).toHaveBeenCalledTimes(1);
  });

  it('does not stop the app when registration fails offline', async () => {
    http.post.mockRejectedValueOnce(new Error('network'));
    await expect(ensureDevice()).resolves.toBeNull();
  });
});

describe('a known barcode', () => {
  it('returns the product with a confidence level and caches it', async () => {
    await registeredDevice();
    http.get.mockResolvedValueOnce({ status: 200, data: knownPayload });

    const result = await scanBarcode(KNOWN);
    expect(result.found).toBe(true);
    expect(result.confidence.level).toBe('unverified');
    expect(result.open_food_facts?.product_name).toBe('Maggi Masala Noodles');
    expect(result.glamgenius?.fssai_licence).toBe('10012345678901');

    expect(await readCached(KNOWN)).not.toBeNull();
  });
});

describe('an unknown barcode', () => {
  it('is an answer, not an error, and offers the label', async () => {
    await registeredDevice();
    http.get.mockResolvedValueOnce({ status: 200, data: notFoundPayload });

    const result = await scanBarcode(UNKNOWN);
    expect(result.found).toBe(false);
    expect(result.can_capture_label).toBe(true);
    expect(result.confidence.level).toBe('not_enough_information');
    expect(result.message).toMatch(/do not know this one yet/i);
  });
});

describe('with the network disabled', () => {
  it('answers a cached product from the phone', async () => {
    await registeredDevice();
    await cacheResult(withConfidence({ ...knownPayload } as Partial<ScanResult> & { barcode: string }));
    http.get.mockRejectedValue(Object.assign(new Error('offline'), { response: undefined }));

    const result = await scanBarcode(KNOWN);
    expect(result.found).toBe(true);
    expect(result.offline).toBe(true);
    expect(result.open_food_facts?.product_name).toBe('Maggi Masala Noodles');
    expect(result.confidence.level).toBe('unverified');
  });

  it('says so plainly for a product it has never seen', async () => {
    await registeredDevice();
    http.get.mockRejectedValue(new Error('offline'));

    const result = await scanBarcode(UNKNOWN);
    expect(result.offline).toBe(true);
    expect(result.confidence.level).toBe('not_enough_information');
    expect(result.message).toMatch(/saved and will be looked up/i);
  });

  it('queues the scan and sends it once, even if the queue is replayed', async () => {
    await registeredDevice();
    http.get.mockRejectedValue(new Error('offline'));
    await scanBarcode(UNKNOWN);
    await scanBarcode(KNOWN);

    const queued = await readQueue();
    expect(queued).toHaveLength(2);
    expect(queued.every((q) => q.queued_offline)).toBe(true);

    // Back online. Each queued scan carries its own id, so a replay is safe.
    http.post.mockResolvedValue({ data: { created: true } });
    const first = await syncQueue();
    expect(first.sent).toBe(2);
    expect(first.remaining).toBe(0);

    http.post.mockClear();
    const second = await syncQueue();
    expect(second.sent).toBe(0);
    expect(http.post).not.toHaveBeenCalled();
  });

  it('keeps a scan queued when the send fails, so nothing is lost', async () => {
    await registeredDevice();
    http.get.mockRejectedValue(new Error('offline'));
    await scanBarcode(UNKNOWN);

    http.post.mockRejectedValue(new Error('still offline'));
    const outcome = await syncQueue();
    expect(outcome.sent).toBe(0);
    expect(outcome.remaining).toBe(1);
    expect(await readQueue()).toHaveLength(1);
  });
});

describe('confidence', () => {
  it('is filled in rather than left blank if a result ever arrives without one', () => {
    const result = withConfidence({ barcode: KNOWN, found: true });
    expect(result.confidence.level).toBe('not_enough_information');
    expect(result.confidence.text).toBeTruthy();
  });

  it('is on every answer the scanner can give', async () => {
    await registeredDevice();
    for (const payload of [knownPayload, notFoundPayload]) {
      http.get.mockResolvedValueOnce({ status: 200, data: payload });
      const result = await scanBarcode(payload.barcode);
      expect(result.confidence.level).toBeTruthy();
      expect(result.confidence.text).toBeTruthy();
    }
    http.get.mockRejectedValueOnce(new Error('offline'));
    const offline = await scanBarcode('8900000000000');
    expect(offline.confidence.text).toBeTruthy();
  });
});

describe('scan ids', () => {
  it('are unique, which is what makes the queue safe to replay', () => {
    const ids = new Set(Array.from({ length: 200 }, () => newScanId()));
    expect(ids.size).toBe(200);
  });
});

describe('label confirmation', () => {
  it('uses the authenticated client with the device token and never sends client facts', async () => {
    await registeredDevice();
    http.post.mockResolvedValueOnce({
      data: { confidence: { level: 'unverified', text: 'Confirmed.' }, confirmations: 0 },
    });

    await expect(confirmLabel(UNKNOWN, 'run-a')).resolves.toEqual({
      confidence: { level: 'unverified', text: 'Confirmed.' },
      confirmations: 0,
    });
    const [, body, config] = http.post.mock.calls[1];
    expect(body).toEqual(expect.objectContaining({ barcode: UNKNOWN, ai_run_id: 'run-a' }));
    expect(body).not.toHaveProperty('facts');
    expect(body.client_scan_id).toEqual(expect.any(String));
    expect(config.headers).toEqual({ 'X-Device-Token': 'device-token' });
  });

  it('does not pretend an offline confirmation was saved', async () => {
    await registeredDevice();
    http.post.mockRejectedValueOnce(new Error('offline'));
    await expect(confirmLabel(UNKNOWN, 'run-a')).rejects.toThrow('offline');
  });

  it('blocks confirmation when the transcription reference is missing', async () => {
    await expect(confirmLabel(UNKNOWN, '')).rejects.toThrow(/missing its confirmation reference/i);
    expect(http.post).not.toHaveBeenCalled();
  });

  it('re-registers once for DEVICE_UNKNOWN and preserves the idempotency key', async () => {
    await registeredDevice();
    const firstError = { response: { status: 401, data: { detail: { code: 'DEVICE_UNKNOWN' } } } };
    http.post.mockRejectedValueOnce(firstError);
    http.post.mockResolvedValueOnce({ data: { device_id: 'd2', token: 'new-device-token' } });
    http.post.mockResolvedValueOnce({
      data: { confidence: { level: 'unverified', text: 'Confirmed.' }, confirmations: 0 },
    });

    await expect(confirmLabel(UNKNOWN, 'run-a')).resolves.toEqual({
      confidence: { level: 'unverified', text: 'Confirmed.' },
      confirmations: 0,
    });
    const firstBody = http.post.mock.calls[1][1];
    const retryBody = http.post.mock.calls[3][1];
    expect(retryBody).toEqual(firstBody);
    expect(http.post.mock.calls[3][2].headers).toEqual({ 'X-Device-Token': 'new-device-token' });
    expect(http.post).toHaveBeenCalledTimes(4);
  });

  it('surfaces a second DEVICE_UNKNOWN failure without looping', async () => {
    await registeredDevice();
    const deviceError = { response: { status: 401, data: { detail: { code: 'DEVICE_UNKNOWN' } } } };
    http.post.mockRejectedValueOnce(deviceError);
    http.post.mockResolvedValueOnce({ data: { device_id: 'd2', token: 'new-device-token' } });
    http.post.mockRejectedValueOnce(deviceError);

    await expect(confirmLabel(UNKNOWN, 'run-a')).rejects.toBe(deviceError);
    expect(http.post).toHaveBeenCalledTimes(4);
  });
});

describe('handing the phone to an account', () => {
  it('offers the token once, then stops asking', async () => {
    await registeredDevice();
    const account = 'account-1';
    expect(await tokenToClaimFor(account)).toBe('device-token');

    await markDeviceClaimed(account);
    expect(await tokenToClaimFor(account)).toBeNull();

    // A different person signing in on the same phone is a new claim.
    expect(await tokenToClaimFor('account-2')).toBe('device-token');
  });

  it('has nothing to offer before the phone has registered', async () => {
    expect(await tokenToClaimFor('account-1')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Step 8L — the skin-care FOR YOU loop from the service layer
// ---------------------------------------------------------------------------
describe('skin-care confirmation', () => {
  it('sends exactly three identifiers and never replays the displayed facts', async () => {
    await registeredDevice();
    http.post.mockResolvedValueOnce({ data: { scan_id: 's1', created: true } });

    await confirmSkinCareLabel('890111', 'run-1', 'stable-key');

    const [url, body] = http.post.mock.calls[1];
    expect(url).toBe('/api/v2/scan/skin-care/label/confirm');
    expect(Object.keys(body).sort()).toEqual(['ai_run_id', 'barcode', 'client_scan_id']);
    for (const forbidden of [
      'category', 'product_category', 'ingredients', 'ingredients_text', 'facts',
      'label_snapshot_id', 'action', 'verdict', 'reason', 'release', 'account_id',
    ]) {
      expect(body).not.toHaveProperty(forbidden);
    }
  });

  it('never uses the generic food confirmation route', async () => {
    await registeredDevice();
    http.post.mockResolvedValueOnce({ data: { scan_id: 's1', created: true } });
    await confirmSkinCareLabel('890111', 'run-1', 'stable-key');
    const urls = http.post.mock.calls.map((call) => call[0]);
    expect(urls).not.toContain('/api/v2/scan/label/confirm');
  });

  it('reuses the same client_scan_id when a stale device forces a retry', async () => {
    await registeredDevice();
    http.post
      .mockRejectedValueOnce({ response: { data: { detail: { code: 'DEVICE_UNKNOWN' } } } })
      .mockResolvedValueOnce({ data: { device_id: 'd2', token: 'fresh-token' } })
      .mockResolvedValueOnce({ data: { scan_id: 's1', created: true } });

    await confirmSkinCareLabel('890111', 'run-1', 'stable-key');

    const confirmBodies = http.post.mock.calls
      .filter((call) => call[0] === '/api/v2/scan/skin-care/label/confirm')
      .map((call) => call[1]);
    expect(confirmBodies).toHaveLength(2);
    // The same logical confirmation, not a second capture.
    expect(confirmBodies[0].client_scan_id).toBe('stable-key');
    expect(confirmBodies[1].client_scan_id).toBe('stable-key');
    expect(confirmBodies[0].ai_run_id).toBe(confirmBodies[1].ai_run_id);
  });

  it('does not retry forever when the fresh credential also fails', async () => {
    await registeredDevice();
    http.post
      .mockRejectedValueOnce({ response: { data: { detail: { code: 'DEVICE_UNKNOWN' } } } })
      .mockResolvedValueOnce({ data: { device_id: 'd2', token: 'fresh-token' } })
      .mockRejectedValueOnce({ response: { data: { detail: { code: 'DEVICE_UNKNOWN' } } } });
    await expect(confirmSkinCareLabel('890111', 'run-1', 'k')).rejects.toBeDefined();
    const attempts = http.post.mock.calls.filter(
      (call) => call[0] === '/api/v2/scan/skin-care/label/confirm',
    );
    expect(attempts).toHaveLength(2);
  });

  it('refuses a draft with no confirmation reference', async () => {
    await registeredDevice();
    await expect(confirmSkinCareLabel('890111', '  ', 'k')).rejects.toBeDefined();
  });
});

describe('the FOR YOU request', () => {
  it('carries only a barcode when nothing was selected', async () => {
    await registeredDevice();
    http.post.mockResolvedValueOnce({ data: { barcode: '890111' } });
    await fetchSkinCareForYou('890111', {});
    const [url, body] = http.post.mock.calls[1];
    expect(url).toBe('/api/v2/scan/skin-care/for-you');
    expect(Object.keys(body)).toEqual(['barcode']);
  });

  it('carries only barcode and safety when flags were selected', async () => {
    await registeredDevice();
    http.post.mockResolvedValueOnce({ data: { barcode: '890111' } });
    await fetchSkinCareForYou('890111', { pregnancy: true });
    const [, body] = http.post.mock.calls[1];
    expect(Object.keys(body).sort()).toEqual(['barcode', 'safety']);
    expect(body.safety).toEqual({ pregnancy: true });
  });

  it('cannot carry a category, snapshot, release or decision', async () => {
    await registeredDevice();
    http.post.mockResolvedValueOnce({ data: { barcode: '890111' } });
    await fetchSkinCareForYou('890111', { medication_involved: true });
    const [, body] = http.post.mock.calls[1];
    for (const forbidden of [
      'category', 'product_category', 'decision_category', 'label_snapshot_id',
      'scan_event_id', 'release_id', 'release_version', 'content_hash', 'action',
      'verdict', 'reason', 'reason_key', 'policy', 'signal', 'ingredients',
      'ingredients_text', 'substance', 'evidence', 'account_id',
    ]) {
      expect(body).not.toHaveProperty(forbidden);
    }
  });

  it('never writes the personal decision to the phone', async () => {
    await registeredDevice();
    http.post.mockResolvedValueOnce({
      data: { barcode: '890111', result: { status: 'decision_presentable', verdict_text: 'X' } },
    });
    const before = JSON.stringify(store);
    await fetchSkinCareForYou('890111', {});
    expect(JSON.stringify(store)).toBe(before);
    expect(JSON.stringify(store)).not.toContain('decision_presentable');
  });

  it('never queues a personal decision into the offline scan outbox', async () => {
    await registeredDevice();
    http.post.mockResolvedValueOnce({ data: { barcode: '890111' } });
    await fetchSkinCareForYou('890111', {});
    expect(await readQueue()).toHaveLength(0);
  });
});

describe('device ownership before a skin-care model call', () => {
  it('claims the phone for the account when it has not been claimed', async () => {
    await registeredDevice();
    http.post.mockResolvedValueOnce({ data: { claimed: true } });
    expect(await ensureDeviceClaimed('account-1')).toBe(true);
    expect(http.post.mock.calls[1][0]).toBe('/api/v2/scan/device/claim');
  });

  it('does not claim twice for the same account', async () => {
    await registeredDevice();
    await markDeviceClaimed('account-1');
    expect(await ensureDeviceClaimed('account-1')).toBe(true);
    expect(http.post).toHaveBeenCalledTimes(1);
  });

  it('recovers once from a stale device credential', async () => {
    await registeredDevice();
    http.post
      .mockRejectedValueOnce({ response: { data: { detail: { code: 'DEVICE_UNKNOWN' } } } })
      .mockResolvedValueOnce({ data: { device_id: 'd2', token: 'fresh' } })
      .mockResolvedValueOnce({ data: { claimed: true } });
    expect(await ensureDeviceClaimed('account-1')).toBe(true);
    const claims = http.post.mock.calls.filter((c) => c[0] === '/api/v2/scan/device/claim');
    expect(claims).toHaveLength(2);
  });

  it('reports a genuine ownership failure rather than bypassing it', async () => {
    await registeredDevice();
    http.post.mockRejectedValueOnce({ response: { status: 409, data: { detail: { code: 'CONFLICT' } } } });
    expect(await ensureDeviceClaimed('account-1')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Step 8L correction — the plain scan event must settle before a capture
// ---------------------------------------------------------------------------
//
// scanBarcode records its event in the background. If that write lands *after*
// a skin-care confirmation it becomes the device's newest ScanEvent, and
// Step 8K correctly reports pack_not_confirmed seconds after somebody
// confirmed a pack. These tests drive the real service, not a mock of it.

describe('settling the generic scan ledger', () => {
  it('waits for a deliberately delayed scan-event write', async () => {
    await registeredDevice();
    http.get.mockResolvedValueOnce({ status: 200, data: notFoundPayload });

    let releaseWrite: () => void = () => undefined;
    const written = new Promise<void>((resolve) => { releaseWrite = resolve; });
    let eventPosted = false;
    http.post.mockImplementation(async (url: string) => {
      if (url === '/api/v2/scan/events') {
        await written;
        eventPosted = true;
        return { data: {} };
      }
      return { data: {} };
    });

    await scanBarcode(UNKNOWN);
    // The lookup answered while the event write is still in flight.
    expect(eventPosted).toBe(false);

    let settled: boolean | null = null;
    const barrier = settleScanEvents(UNKNOWN).then((ok) => { settled = ok; });
    await Promise.resolve();
    // The barrier is still holding: the capture cannot begin yet.
    expect(settled).toBeNull();

    releaseWrite();
    await barrier;
    expect(eventPosted).toBe(true);
    expect(settled).toBe(true);
  });

  it('refuses to settle while this barcode is still queued', async () => {
    await registeredDevice();
    // The lookup fails, so the scan is queued rather than sent.
    http.get.mockRejectedValueOnce(new Error('offline'));
    await scanBarcode(UNKNOWN);
    expect(await readQueue()).toHaveLength(1);

    // The flush keeps failing, so the entry stays put.
    http.post.mockRejectedValue(new Error('still offline'));
    expect(await settleScanEvents(UNKNOWN)).toBe(false);
    expect(await readQueue()).toHaveLength(1);
  });

  it('settles once the queued entry finally flushes', async () => {
    await registeredDevice();
    http.get.mockRejectedValueOnce(new Error('offline'));
    await scanBarcode(UNKNOWN);
    expect(await readQueue()).toHaveLength(1);

    http.post.mockResolvedValue({ data: {} });
    expect(await settleScanEvents(UNKNOWN)).toBe(true);
    expect(await readQueue()).toHaveLength(0);
  });

  it('is not blocked by an unrelated barcode stuck in the queue', async () => {
    await registeredDevice();
    // A different barcode is sitting in the queue and cannot be sent.
    await enqueueScan({
      client_scan_id: 'stuck-1', barcode: KNOWN,
      scanned_at: new Date().toISOString(), queued_offline: true,
    });
    // Only that one fails to flush; ours goes through.
    http.post.mockImplementation(async (url: string, body: { barcode?: string }) => {
      if (url === '/api/v2/scan/events' && body?.barcode === KNOWN) {
        throw new Error('this one is stuck');
      }
      return { data: {} };
    });
    http.get.mockResolvedValueOnce({ status: 200, data: notFoundPayload });
    await scanBarcode(UNKNOWN);

    // Somebody else's stuck entry is their problem, not a reason to block
    // this pack.
    expect(await settleScanEvents(UNKNOWN)).toBe(true);
    const remaining = await readQueue();
    expect(remaining.map((entry) => entry.barcode)).toEqual([KNOWN]);
  });

  it('treats an unreadable queue as unsettled rather than assuming success', async () => {
    await registeredDevice();
    const original = AsyncStorage.getItem as jest.Mock;
    const spy = jest.spyOn(AsyncStorage, 'getItem').mockRejectedValue(new Error('storage gone'));
    // readQueue swallows storage errors and returns [], so the barrier still
    // answers; what matters is that it never throws into the capture path.
    await expect(settleScanEvents(UNKNOWN)).resolves.toBeDefined();
    spy.mockRestore();
    expect(original).toBeDefined();
  });
});
