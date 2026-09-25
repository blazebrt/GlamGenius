/**
 * Lane C — the offline scan queue, and the read-only refresh.
 *
 * The queue is shared by everything that scans, so it has one coordination
 * authority: every read-modify-write runs in one lane, a flush removes only the
 * ids the server acknowledged from the *latest* queue, and one flush runs at a
 * time. Each race below is built with deferred promises and a transport the
 * test answers by hand. Nothing waits on a timer.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import axios from 'axios';
import { secureSessionStorage } from '../services/secureSessionStorage';

import {
  confirmLabel,
  ensureDevice,
  enqueueScan,
  readQueue,
  refreshBarcodeResult,
  resetPendingScanEvents,
  scanBarcode,
  settleScanEvents,
  syncQueue,
  type QueuedScan,
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

const http = (axios as unknown as { create: () => { get: jest.Mock; post: jest.Mock } }).create() as {
  get: jest.Mock; post: jest.Mock;
};
const store = (AsyncStorage as unknown as { __store: Record<string, string> }).__store;
const realGetItem = (AsyncStorage.getItem as jest.Mock).getMockImplementation() as (k: string) => Promise<string | null>;
const QUEUE_KEY = 'glamgenius_scan_queue_v1';

const BARCODE = '8906000000011';
const OTHER = '8906000000028';

type Deferred<T> = { promise: Promise<T>; resolve: (value: T) => void; reject: (error: unknown) => void };
function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

/** Let every already-queued continuation run. Not a timer: a microtask drain. */
async function settle(): Promise<void> {
  for (let i = 0; i < 20; i += 1) await Promise.resolve();
}

/**
 * The server, answered by hand. Every POST to /scan/events waits on its own
 * deferred; resolving it is the moment the event lands, which the log records.
 */
class Transport {
  pending: { id: string; barcode: string; answer: Deferred<unknown> }[] = [];
  landed: string[] = [];

  install() {
    http.post.mockImplementation((path: string, body: Record<string, unknown>) => {
      if (path === '/api/v2/scan/device') {
        return Promise.resolve({ data: { device_id: 'd1', token: 'device-token' } });
      }
      if (path === '/api/v2/scan/events') {
        const answer = deferred<unknown>();
        const id = String(body.client_scan_id);
        this.pending.push({ id, barcode: String(body.barcode), answer });
        return answer.promise.then((value) => { this.landed.push(`event:${id}`); return value; });
      }
      if (path === '/api/v2/scan/label/confirm') {
        this.landed.push(`confirm:${String(body.client_scan_id)}`);
        return Promise.resolve({ data: { confidence: { level: 'unverified', text: 'Confirmed.' }, confirmations: 0 } });
      }
      return Promise.reject(new Error(`unexpected POST ${path}`));
    });
  }

  /** The request in flight for this client_scan_id. */
  request(id: string) {
    const entry = this.pending.find((p) => p.id === id);
    if (!entry) throw new Error(`no request in flight for ${id}; in flight: ${this.pending.map((p) => p.id).join(',')}`);
    return entry;
  }

  sent(): string[] {
    return this.pending.map((p) => p.id);
  }

  async ack(id: string) {
    this.request(id).answer.resolve({ status: 201, data: { created: true } });
    await settle();
  }

  async fail(id: string) {
    this.request(id).answer.reject(new Error('network'));
    await settle();
  }
}

function entry(id: string, barcode = BARCODE): QueuedScan {
  return { client_scan_id: id, barcode, scanned_at: '2026-09-24T10:00:00.000Z', queued_offline: true };
}

function stored(): string[] {
  const raw = store[QUEUE_KEY];
  return raw ? (JSON.parse(raw) as QueuedScan[]).map((q) => q.client_scan_id) : [];
}

let transport: Transport;

beforeEach(async () => {
  Object.keys(store).forEach((key) => delete store[key]);
  http.get.mockReset();
  http.post.mockReset();
  (AsyncStorage.getItem as jest.Mock).mockImplementation(realGetItem);
  resetPendingScanEvents();
  transport = new Transport();
  transport.install();
  await ensureDevice();
});

describe('a flush never writes back what it read', () => {
  it('A. keeps a scan queued while the flush was waiting, once the old entry lands', async () => {
    await enqueueScan(entry('old-a'));
    const flush = syncQueue();
    await settle();
    expect(transport.sent()).toEqual(['old-a']);

    await enqueueScan(entry('new-b'));
    await transport.ack('old-a');
    await expect(flush).resolves.toEqual({ sent: 1, remaining: 1 });
    expect(stored()).toEqual(['new-b']);
  });

  it('B. keeps both the failed old entry and the new one', async () => {
    await enqueueScan(entry('old-a'));
    const flush = syncQueue();
    await settle();
    await enqueueScan(entry('new-b'));
    await transport.fail('old-a');
    await expect(flush).resolves.toEqual({ sent: 0, remaining: 2 });
    expect(stored()).toEqual(['old-a', 'new-b']);
  });

  it('F. never removes an entry added after the snapshot, even for the same barcode', async () => {
    await enqueueScan(entry('snap-a'));
    await enqueueScan(entry('snap-b'));
    const flush = syncQueue();
    await settle();
    await enqueueScan(entry('late-c'));
    await transport.ack('snap-a');
    await transport.ack('snap-b');
    await flush;
    expect(stored()).toEqual(['late-c']);
    // late-c was never part of that flush's snapshot, so it was never sent.
    expect(transport.sent()).toEqual(['snap-a', 'snap-b']);
  });

  it('G. partial success removes exactly the acknowledged ids', async () => {
    for (const id of ['part-a', 'part-b', 'part-c']) await enqueueScan(entry(id));
    const flush = syncQueue();
    await settle();
    await transport.ack('part-a');
    await transport.fail('part-b');
    await transport.ack('part-c');
    await expect(flush).resolves.toEqual({ sent: 2, remaining: 1 });
    expect(stored()).toEqual(['part-b']);
  });
});

describe('enqueueing', () => {
  it('C. two simultaneous enqueues both survive', async () => {
    await Promise.all([enqueueScan(entry('both-a')), enqueueScan(entry('both-b'))]);
    expect(stored().sort()).toEqual(['both-a', 'both-b']);
  });

  it('D. one client_scan_id is one logical entry, however it arrives', async () => {
    await Promise.all([enqueueScan(entry('same-id')), enqueueScan(entry('same-id'))]);
    await enqueueScan(entry('same-id'));
    expect(stored()).toEqual(['same-id']);
  });

  it('never replaces the queue when the store cannot be read, and the scan still answers', async () => {
    await enqueueScan(entry('kept-a'));
    (AsyncStorage.getItem as jest.Mock).mockImplementationOnce(() => Promise.reject(new Error('io')));
    await expect(enqueueScan(entry('unreadable-b'))).resolves.toBeUndefined();
    expect(stored()).toEqual(['kept-a']);
  });

  it('replaces a queue that is not readable text, since nothing in it could ever be sent', async () => {
    store[QUEUE_KEY] = '{not json';
    await enqueueScan(entry('fresh-a'));
    expect(stored()).toEqual(['fresh-a']);
  });
});

describe('one flush at a time', () => {
  it('E. a second caller waits for the running flush and then sends what is left', async () => {
    await enqueueScan(entry('first-a'));
    const one = syncQueue();
    await settle();
    const two = syncQueue();
    await settle();
    // No competing flush: exactly one request in flight.
    expect(transport.sent()).toEqual(['first-a']);

    await enqueueScan(entry('meanwhile-c'));
    await transport.ack('first-a');
    await expect(one).resolves.toEqual({ sent: 1, remaining: 1 });
    // The follow-up flush starts only now, from the latest queue.
    expect(transport.sent()).toEqual(['first-a', 'meanwhile-c']);
    await transport.ack('meanwhile-c');
    await expect(two).resolves.toEqual({ sent: 1, remaining: 0 });
    expect(stored()).toEqual([]);
  });

  it('shares one follow-up among every caller that arrived during a flush', async () => {
    await enqueueScan(entry('shared-a'));
    const first = syncQueue();
    await settle();
    const second = syncQueue();
    const third = syncQueue();
    expect(second).toBe(third);
    await transport.ack('shared-a');
    await first;
    await expect(second).resolves.toEqual({ sent: 0, remaining: 0 });
  });

  it('never overwrites the queue when the final read fails; the entries stay for the next flush', async () => {
    await enqueueScan(entry('stay-a'));
    const flush = syncQueue();
    await settle();
    (AsyncStorage.getItem as jest.Mock).mockImplementationOnce(() => Promise.reject(new Error('io')));
    await transport.ack('stay-a');
    await expect(flush).rejects.toThrow('io');
    // Not erased by a write computed from nothing. Re-sending is safe: the id is idempotent.
    expect(stored()).toEqual(['stay-a']);
  });
});

describe('settlement over the coordinated queue', () => {
  it('H. does not report a barcode settled while its entry, added during an overlapping flush, is unsent', async () => {
    await enqueueScan(entry('unrelated-u', OTHER));
    const running = syncQueue();
    await settle();
    expect(transport.sent()).toEqual(['unrelated-u']);

    // This barcode's plain event is queued while that flush is waiting.
    await enqueueScan(entry('this-x', BARCODE));
    let answer: boolean | undefined;
    const proving = settleScanEvents(BARCODE).then((value) => { answer = value; });
    await settle();
    await transport.ack('unrelated-u');
    await running;
    // The running flush neither erased this-x nor let settlement pass without it.
    expect(answer).toBeUndefined();
    expect(transport.sent()).toEqual(['unrelated-u', 'this-x']);

    await transport.ack('this-x');
    await proving;
    expect(answer).toBe(true);
    expect(transport.landed).toContain('event:this-x');
    expect(stored()).toEqual([]);
  });

  it('H. refuses when that entry fails to send', async () => {
    await enqueueScan(entry('unrelated-u', OTHER));
    const running = syncQueue();
    await settle();
    await enqueueScan(entry('this-x', BARCODE));
    const proving = settleScanEvents(BARCODE);
    await settle();
    await transport.ack('unrelated-u');
    await running;
    await transport.fail('this-x');
    await expect(proving).resolves.toBe(false);
    expect(stored()).toEqual(['this-x']);
  });

  it('I. a queue that cannot be read proves nothing', async () => {
    (AsyncStorage.getItem as jest.Mock).mockImplementation((key: string) => (
      key === QUEUE_KEY ? Promise.reject(new Error('io')) : realGetItem(key)
    ));
    await expect(settleScanEvents(BARCODE)).resolves.toBe(false);
  });

  it('J. after settlement, the old plain event has landed before the confirmation', async () => {
    http.get.mockResolvedValueOnce({ status: 200, data: { barcode: BARCODE, found: true, outcome: 'found_off',
      confidence: { level: 'unverified', text: 'x' } } });
    await scanBarcode(BARCODE);
    await settle();
    // The lookup's plain event is still in flight, deliberately.
    const [plain] = transport.sent();
    expect(plain).toBeDefined();

    let proven: boolean | undefined;
    const proving = settleScanEvents(BARCODE).then((value) => { proven = value; });
    await settle();
    expect(proven).toBeUndefined();
    await transport.ack(plain);
    await proving;
    expect(proven).toBe(true);

    await confirmLabel(BARCODE, 'food-run', 'draft-key');
    expect(transport.landed).toEqual([`event:${plain}`, 'confirm:draft-key']);
    // Nothing is left to overtake it.
    await syncQueue();
    expect(transport.sent()).toEqual([plain]);
    expect(transport.landed[transport.landed.length - 1]).toBe('confirm:draft-key');
  });
});

describe('the read-only refresh', () => {
  it('does nothing when no device is stored, without registering or writing identity', async () => {
    await secureSessionStorage.removeItem('glamgenius_scan_device_v1');
    delete store.glamgenius_scan_device_v1;
    const before = { ...store };
    const secureWrite = jest.spyOn(secureSessionStorage, 'setItem');
    const secureRemove = jest.spyOn(secureSessionStorage, 'removeItem');
    const random = jest.spyOn(Math, 'random');
    http.post.mockClear();
    http.get.mockClear();
    await expect(refreshBarcodeResult(BARCODE)).resolves.toBeNull();
    expect(http.post).not.toHaveBeenCalled();
    expect(http.get).not.toHaveBeenCalled();
    expect(secureWrite).not.toHaveBeenCalled();
    expect(secureRemove).not.toHaveBeenCalled();
    expect(random).not.toHaveBeenCalled();
    expect(store).toEqual(before);
    secureWrite.mockRestore();
    secureRemove.mockRestore();
    random.mockRestore();
  });

  it('reads the result and refreshes the cache without writing, queueing or minting anything', async () => {
    const random = jest.spyOn(Math, 'random');
    http.get.mockResolvedValueOnce({ status: 200, data: { barcode: BARCODE, found: true, outcome: 'label_captured',
      confidence: { level: 'unverified', text: 'Confirmed.' } } });
    const result = await refreshBarcodeResult(BARCODE);
    await settle();
    expect(result?.outcome).toBe('label_captured');
    expect(http.get).toHaveBeenCalledWith(`/api/v2/scan/lookup/${BARCODE}`, expect.anything());
    // Zero events, zero queue entries, zero client_scan_ids.
    expect(http.post.mock.calls.filter(([path]) => path === '/api/v2/scan/events')).toEqual([]);
    expect(store[QUEUE_KEY]).toBeUndefined();
    expect(random).not.toHaveBeenCalled();
    random.mockRestore();
    // No tracked background event either: this barcode settles at once.
    await expect(settleScanEvents(BARCODE)).resolves.toBe(true);
  });

  it('answers null instead of an offline stand-in, and never re-registers the device', async () => {
    const storedDevice = await secureSessionStorage.getItem('glamgenius_scan_device_v1');
    http.get.mockRejectedValueOnce({ response: { status: 401 } });
    const registrations = http.post.mock.calls.filter(([path]) => path === '/api/v2/scan/device').length;
    await expect(refreshBarcodeResult(BARCODE)).resolves.toBeNull();
    expect(http.post.mock.calls.filter(([path]) => path === '/api/v2/scan/device').length).toBe(registrations);
    expect(await secureSessionStorage.getItem('glamgenius_scan_device_v1')).toBe(storedDevice);
    expect(await readQueue()).toEqual([]);
  });

  it('can read a legacy token without migrating or replacing device identity', async () => {
    await secureSessionStorage.removeItem('glamgenius_scan_device_v1');
    const legacy = JSON.stringify({ token: 'legacy-token', device_key: 'legacy-key' });
    store.glamgenius_scan_device_v1 = legacy;
    http.get.mockResolvedValueOnce({ data: { barcode: BARCODE, found: true } });
    http.post.mockClear();
    await expect(refreshBarcodeResult(BARCODE)).resolves.not.toBeNull();
    expect(http.get).toHaveBeenCalledWith(`/api/v2/scan/lookup/${BARCODE}`, {
      headers: { 'X-Device-Token': 'legacy-token' },
    });
    expect(http.post).not.toHaveBeenCalled();
    expect(await secureSessionStorage.getItem('glamgenius_scan_device_v1')).toBeNull();
    expect(store.glamgenius_scan_device_v1).toBe(legacy);
  });
});
