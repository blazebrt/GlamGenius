/**
 * Audit lane 1, F03, the phone's half: a queued scan or report keeps the
 * identity it was made under.
 *
 * The server now attributes a scan or report to the account whose bearer
 * token it carries, and to nobody when it carries only the device token. So
 * what the queue sends with decides whose it is, and a queue outlives the
 * session that filled it. These tests drive the real auth generation through
 * signed out, A, signed out, B and A again, and check, for every entry, which
 * client it went out on and as which account.
 *
 * - Made signed out: the device token alone, whoever is signed in later.
 * - Made as A: only as A (``expectedAccountId``), never anonymously and never
 *   as B. While A is away it waits, intact.
 * - No token is stored anywhere in the queue.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import axios from 'axios';

import { api, AccountMismatchError } from '../services/api';
import { makeReport, flushReports, submitReport } from '../services/errorReports';
import {
  ensureDevice,
  enqueueScan,
  resetPendingScanEvents,
  scanBarcode,
  syncQueue,
  type QueuedScan,
} from '../services/productScan';
import { openAuthGeneration } from '../store/authGeneration';

jest.mock('axios', () => {
  const instance = {
    get: jest.fn(),
    post: jest.fn(),
    interceptors: { request: { use: jest.fn() }, response: { use: jest.fn() } },
  };
  return { __esModule: true, default: { create: jest.fn(() => instance) }, create: jest.fn(() => instance) };
});

jest.mock('../services/api', () => {
  class MockAccountMismatchError extends Error {
    readonly code = 'ACCOUNT_MISMATCH';
  }
  return { api: { post: jest.fn(), get: jest.fn() }, AccountMismatchError: MockAccountMismatchError };
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

const device = (axios as unknown as { create: () => { get: jest.Mock; post: jest.Mock } }).create();
const account = api as unknown as { post: jest.Mock };
const store = (AsyncStorage as unknown as { __store: Record<string, string> }).__store;
const QUEUE_KEY = 'glamgenius_scan_queue_v1';
const REPORT_QUEUE_KEY = 'glamgenius_error_reports_v1';

const A = 'account-a';
const B = 'account-b';

function entry(id: string, owner?: string | null): QueuedScan {
  const base: QueuedScan = {
    client_scan_id: id, barcode: '8906000000011', scanned_at: '2026-10-01T10:00:00.000Z', queued_offline: true,
  };
  return owner === undefined ? base : { ...base, owner };
}

const queued = (): string[] => {
  const raw = store[QUEUE_KEY];
  return raw ? (JSON.parse(raw) as QueuedScan[]).map((q) => q.client_scan_id) : [];
};

/** client_scan_ids sent through the device-only client. */
const sentAnonymously = (): string[] => device.post.mock.calls
  .filter(([path]) => path === '/api/v2/scan/events')
  .map(([, body]) => String((body as { client_scan_id: string }).client_scan_id));

/** [client_scan_id, expectedAccountId] sent through the account client. */
const sentAsAccount = (): [string, string | undefined][] => account.post.mock.calls
  .filter(([path]) => path === '/api/v2/scan/events')
  .map(([, body, config]) => [
    String((body as { client_scan_id: string }).client_scan_id),
    (config as { expectedAccountId?: string }).expectedAccountId,
  ]);

beforeEach(async () => {
  Object.keys(store).forEach((key) => delete store[key]);
  jest.clearAllMocks();
  resetPendingScanEvents();
  device.post.mockImplementation(async (path: string) => {
    if (path === '/api/v2/scan/device') return { data: { device_id: 'd1', token: 'device-token' } };
    return { status: 201, data: { created: true } };
  });
  account.post.mockResolvedValue({ status: 201, data: { created: true } });
  openAuthGeneration('');
  await ensureDevice();
});

describe('a queued scan is sent as the identity it was made under', () => {
  it('A → logout → B → A: anonymous goes out anonymously, A only as A, B only as B', async () => {
    // Signed out, and an older entry with no owner field at all.
    await enqueueScan(entry('anon-1', null));
    await enqueueScan(entry('legacy-1'));
    openAuthGeneration(A);
    await enqueueScan(entry('a-1', A));
    openAuthGeneration(''); // A logs out with a-1 still unsent
    await enqueueScan(entry('anon-2', null));

    await syncQueue();
    expect(sentAnonymously().sort()).toEqual(['anon-1', 'anon-2', 'legacy-1']);
    expect(sentAsAccount()).toEqual([]);
    expect(queued()).toEqual(['a-1']);

    openAuthGeneration(B);
    await enqueueScan(entry('b-1', B));
    await syncQueue();
    expect(sentAsAccount()).toEqual([['b-1', B]]);
    expect(queued()).toEqual(['a-1']); // never sent as B

    openAuthGeneration(A);
    await syncQueue();
    expect(sentAsAccount()).toEqual([['b-1', B], ['a-1', A]]);
    expect(queued()).toEqual([]);
    // Neither account's scan ever went out with the device token alone.
    expect(sentAnonymously()).not.toEqual(expect.arrayContaining(['a-1']));
    expect(sentAnonymously()).not.toEqual(expect.arrayContaining(['b-1']));
  });

  it('a scan made signed out stays anonymous when it is flushed after A signs in', async () => {
    await enqueueScan(entry('made-signed-out', null));
    openAuthGeneration(A);
    await syncQueue();
    expect(sentAnonymously()).toEqual(['made-signed-out']);
    expect(sentAsAccount()).toEqual([]);
    const [, , config] = device.post.mock.calls.find(([path]) => path === '/api/v2/scan/events')!;
    expect(Object.keys((config as { headers: Record<string, string> }).headers)).toEqual(['X-Device-Token']);
  });

  it('an account change between the check and the send leaves the entry queued, not misfiled', async () => {
    openAuthGeneration(A);
    await enqueueScan(entry('a-raced', A));
    account.post.mockRejectedValueOnce(new AccountMismatchError());
    await syncQueue();
    expect(queued()).toEqual(['a-raced']);
    expect(sentAnonymously()).toEqual([]);
  });

  it('scanBarcode takes the owner when the scan is made, not when it is stored', async () => {
    openAuthGeneration(A);
    let answer!: (value: unknown) => void;
    device.get.mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));
    const scanning = scanBarcode('8906000000028');
    openAuthGeneration(''); // signed out while the lookup is still in flight
    answer({ status: 200, data: { barcode: '8906000000028', found: false, outcome: 'not_found',
      confidence: { level: 'not_enough_information', text: 'x' } } });
    await scanning;
    // The background event either waits in the queue or has gone out; either
    // way it went out, or will, as A, and never anonymously.
    for (let i = 0; i < 50; i += 1) await Promise.resolve();
    const stored = store[QUEUE_KEY] ? (JSON.parse(store[QUEUE_KEY]) as QueuedScan[]) : [];
    expect(stored.map((q) => q.owner)).toEqual([A]);
    expect(sentAnonymously()).toEqual([]);
  });

  it('stores no credential: an entry names its account and nothing else', async () => {
    openAuthGeneration(A);
    await enqueueScan(entry('a-only', A));
    const raw = store[QUEUE_KEY];
    expect(raw).not.toContain('device-token');
    expect(Object.keys(JSON.parse(raw)[0]).sort()).toEqual(
      ['barcode', 'client_scan_id', 'owner', 'queued_offline', 'scanned_at'],
    );
  });
});

describe('a label-error report follows the same rule', () => {
  const reportPath = '/api/v2/reports/label-error';
  const sentReportsAsAccount = () => account.post.mock.calls
    .filter(([path]) => path === reportPath)
    .map(([, , config]) => (config as { expectedAccountId?: string }).expectedAccountId);
  const sentReportsAnonymously = () => device.post.mock.calls.filter(([path]) => path === reportPath).length;

  it('made signed out: the device token alone, even with an account signed in by then', async () => {
    const report = makeReport({ barcode: '8906000000011', subject: 'sugar', reason: 'wrong_number' });
    expect(report.owner).toBeNull();
    openAuthGeneration(A);
    expect(await submitReport(report)).toBe(true);
    expect(sentReportsAnonymously()).toBe(1);
    expect(sentReportsAsAccount()).toEqual([]);
  });

  it('made as A: sent only as A, held while A is away, never as B', async () => {
    openAuthGeneration(A);
    const report = makeReport({ barcode: '8906000000011', subject: 'sugar', reason: 'wrong_number' });
    expect(report.owner).toBe(A);
    openAuthGeneration('');
    expect(await submitReport(report)).toBe(false); // queued
    expect(JSON.parse(store[REPORT_QUEUE_KEY]).map((r: { owner: string }) => r.owner)).toEqual([A]);

    openAuthGeneration(B);
    expect(await flushReports()).toEqual({ sent: 0, remaining: 1 });
    openAuthGeneration(A);
    expect(await flushReports()).toEqual({ sent: 1, remaining: 0 });
    expect(sentReportsAsAccount()).toEqual([A]);
    expect(sentReportsAnonymously()).toBe(0);
  });
});
