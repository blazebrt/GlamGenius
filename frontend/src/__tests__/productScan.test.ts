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
  confirmLabel,
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
