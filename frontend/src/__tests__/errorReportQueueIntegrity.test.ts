import AsyncStorage from '@react-native-async-storage/async-storage';
import { flushReports, makeReport, submitReport, type ErrorReport } from '../services/errorReports';
import { postDeviceForm } from '../services/productScan';

let mockAccount: string | null = null;
jest.mock('../store/authGeneration', () => ({ authAccountId: () => mockAccount }));
jest.mock('../services/productScan', () => ({ postDeviceForm: jest.fn() }));
jest.mock('../services/apiV2', () => ({ V2: '/api/v2' }));
jest.mock('@react-native-async-storage/async-storage', () => {
  const store: Record<string, string> = {};
  return {
    __store: store,
    getItem: jest.fn(async (key: string) => store[key] ?? null),
    setItem: jest.fn(async (key: string, value: string) => { store[key] = value; }),
  };
});

const KEY = 'glamgenius_error_reports_v1';
const store = (AsyncStorage as unknown as { __store: Record<string, string> }).__store;
const send = postDeviceForm as jest.Mock;
const read = AsyncStorage.getItem as jest.Mock;
const write = AsyncStorage.setItem as jest.Mock;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const entry = (id: string, owner: string | null = null): ErrorReport => ({
  client_report_id: id, barcode: null, subject: 'sugar', reason: 'wrong_number',
  reported_at: '2026-10-07T10:00:00.000Z', owner,
});
const queued = (): ErrorReport[] => JSON.parse(store[KEY] ?? '[]');

beforeEach(() => {
  Object.keys(store).forEach((key) => delete store[key]);
  mockAccount = null;
  send.mockReset().mockRejectedValue(new Error('offline'));
  read.mockReset().mockImplementation(async (key: string) => store[key] ?? null);
  write.mockReset().mockImplementation(async (key: string, value: string) => { store[key] = value; });
});

it.each([true, false])('preserves B enqueued while flush A waits (A succeeds=%s)', async (success) => {
  store[KEY] = JSON.stringify([entry('A')]);
  const began = deferred<void>();
  const response = deferred<void>();
  send.mockImplementationOnce(() => { began.resolve(); return response.promise; });
  const flushing = flushReports();
  await began.promise;
  expect(await submitReport(entry('B'))).toBe(false);
  expect(queued().map((row) => row.client_report_id)).toEqual(['A', 'B']);
  if (success) response.resolve(); else response.reject(new Error('offline'));
  expect(await flushing).toEqual({ sent: success ? 1 : 0, remaining: success ? 1 : 2 });
  expect(queued().map((row) => row.client_report_id)).toEqual(success ? ['B'] : ['A', 'B']);
});

it('serializes two failed submits without losing either independently made report', async () => {
  const firstRead = deferred<void>();
  const release = deferred<void>();
  read.mockImplementationOnce(async (key: string) => {
    const snapshot = store[key] ?? null;
    firstRead.resolve();
    await release.promise;
    return snapshot;
  });
  const first = submitReport(entry('A'));
  await firstRead.promise;
  const second = submitReport(entry('B'));
  release.resolve();
  expect(await Promise.all([first, second])).toEqual([false, false]);
  expect(queued().map((row) => row.client_report_id).sort()).toEqual(['A', 'B']);
});

it('coalesces overlapping flushes into one network flight and one acknowledgement write', async () => {
  store[KEY] = JSON.stringify([entry('A')]);
  const began = deferred<void>();
  const response = deferred<void>();
  send.mockImplementation(() => { began.resolve(); return response.promise; });
  const first = flushReports();
  await began.promise;
  const second = flushReports();
  response.resolve();
  expect(await Promise.all([first, second])).toEqual([
    { sent: 1, remaining: 0 }, { sent: 1, remaining: 0 },
  ]);
  expect(send).toHaveBeenCalledTimes(1);
  expect(write).toHaveBeenCalledTimes(1);
});

it('stores one logical report for duplicate concurrent submissions', async () => {
  await Promise.all([submitReport(entry('A')), submitReport(entry('A'))]);
  expect(queued()).toEqual([entry('A')]);
});

it('propagates mutation read failure without overwriting the unknown queue', async () => {
  const original = JSON.stringify([entry('A')]);
  store[KEY] = original;
  read.mockRejectedValueOnce(new Error('storage unreadable'));
  await expect(submitReport(entry('B'))).rejects.toThrow('storage unreadable');
  expect(write).not.toHaveBeenCalled();
  expect(store[KEY]).toBe(original);
});

it.each(['{', '{}', '[{"client_report_id":"A"}]'])('never overwrites malformed storage: %s', async (raw) => {
  store[KEY] = raw;
  await expect(submitReport(entry('B'))).rejects.toThrow();
  expect(write).not.toHaveBeenCalled();
  expect(store[KEY]).toBe(raw);
});

it('does not claim durable enqueue success if the storage write fails', async () => {
  write.mockRejectedValueOnce(new Error('store full'));
  await expect(submitReport(entry('A'))).rejects.toThrow('store full');
  expect(store[KEY]).toBeUndefined();
});

it('keeps acknowledged reports recoverable if acknowledgement persistence fails', async () => {
  store[KEY] = JSON.stringify([entry('A')]);
  send.mockResolvedValue(undefined);
  write.mockRejectedValueOnce(new Error('store full'));
  await expect(flushReports()).rejects.toThrow('store full');
  expect(queued()).toEqual([entry('A')]);
  expect(await flushReports()).toEqual({ sent: 1, remaining: 0 });
});

it('preserves the creator account and never promotes anonymous or legacy reports to a new account', async () => {
  mockAccount = 'account-A';
  const made = makeReport({ barcode: null, subject: 'sugar', reason: 'wrong_number' });
  mockAccount = 'account-B';
  expect(await submitReport(made)).toBe(false);
  expect(send).not.toHaveBeenCalled();
  expect(await flushReports()).toEqual({ sent: 0, remaining: 1 });
  const legacy = entry('legacy');
  delete legacy.owner;
  store[KEY] = JSON.stringify([made, entry('anonymous'), legacy]);
  send.mockResolvedValue(undefined);
  expect(await flushReports()).toEqual({ sent: 2, remaining: 1 });
  expect(send.mock.calls.map((call) => call[2])).toEqual([{ asAccount: null }, { asAccount: null }]);
  mockAccount = null;
  expect(await flushReports()).toEqual({ sent: 0, remaining: 1 });
  mockAccount = 'account-A';
  expect(await flushReports()).toEqual({ sent: 1, remaining: 0 });
  expect(send.mock.calls[2][2]).toEqual({ asAccount: 'account-A' });
});
