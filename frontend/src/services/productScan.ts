/**
 * Scanning a packaged product, from the phone's side.
 *
 * Three things make this different from the rest of the client:
 *
 * 1. **No account.** The camera opens on first launch, so the phone registers
 *    itself and carries a device token. It reaches product data and nothing
 *    else. A device request must never bounce anyone to the sign-in screen,
 *    which is why this uses its own axios instance rather than the shared one.
 * 2. **Offline is normal, not an error.** Every answer is cached, every scan is
 *    queued, and the queue is safe to replay — the server recognises a repeated
 *    `client_scan_id` instead of counting it twice.
 * 3. **Every answer carries a confidence level.** If one ever came back without
 *    one, this fills in "not enough information" rather than showing a bare
 *    result.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import axios from 'axios';

import { getInstallationId } from './deviceIdentity';
import { api } from './api';

const BACKEND_URL = process.env.EXPO_PUBLIC_BACKEND_URL || '';

const DEVICE_KEY = 'glamgenius_scan_device_v1';
const CACHE_KEY = 'glamgenius_scan_cache_v1';
const QUEUE_KEY = 'glamgenius_scan_queue_v1';

/** Kept short on purpose: a known barcode has to answer in under three seconds. */
/**
 * How long a device request may take.
 *
 * This has to sit above the server's own Open Food Facts budget
 * (OFF_TIMEOUT_SECONDS, 4s) plus its overhead, or a lookup that is working
 * perfectly well gets abandoned here, shown as an offline answer, and queued
 * for a sync that has nothing to send. Uncached products are the ones that
 * take longest, so they were the ones most likely to fail.
 */
const LOOKUP_TIMEOUT_MS = 6000;

/** Uploading a photo of a pack is not a lookup and needs longer. */
const UPLOAD_TIMEOUT_MS = 30000;
/** How many products the phone keeps for offline use. */
export const CACHE_LIMIT = 400;

export type ConfidenceLevel =
  | 'verified'
  | 'community'
  | 'unverified'
  | 'not_enough_information';

export interface Confidence {
  level: ConfidenceLevel;
  text: string;
}

export interface OpenFoodFactsHalf {
  product_name?: string | null;
  brands?: string | null;
  ingredients_text?: string | null;
  nutriments?: Record<string, unknown> | null;
  categories?: string | null;
  image_url?: string | null;
  quantity?: string | null;
  countries?: string | null;
}

export interface ScanResult {
  barcode: string;
  found: boolean;
  outcome: 'found_local' | 'found_off' | 'not_found' | 'label_captured';
  confidence: Confidence;
  message?: string | null;
  can_capture_label?: boolean;
  open_food_facts?: OpenFoodFactsHalf | null;
  attribution?: { text: string; source_url?: string; license_url?: string } | null;
  glamgenius?: { confidence?: string; fssai_licence?: string | null; origin?: string } | null;
  official_records?: {
    authority: string;
    record_type: 'food_recall';
    source_url: string;
    records: {
      id: string; recall_id: string; brand_name?: string | null; product_name?: string | null;
      batch_lot?: string | null; licence?: string | null; reason?: string | null;
      recall_status?: string | null; recall_start_date?: string | null;
      recall_termination_date?: string | null; nature_of_recall?: string | null;
      source_url: string; last_seen_at?: string | null; match_state: 'matched';
    }[];
  } | null;
  /** True when this came off the phone rather than the server. */
  from_cache?: boolean;
  /** True when the phone could not reach the server at all. */
  offline?: boolean;
}

export interface QueuedScan {
  client_scan_id: string;
  barcode: string;
  scanned_at: string;
  queued_offline: boolean;
}

// A device request must not trigger the shared client's 401 sign-out.
// eslint-disable-next-line import/no-named-as-default-member
const scanApi = axios.create({
  baseURL: BACKEND_URL ? BACKEND_URL.replace(/\/$/, '') : undefined,
  timeout: LOOKUP_TIMEOUT_MS,
  headers: { 'Content-Type': 'application/json' },
});

/**
 * POST a multipart form as this device.
 *
 * Device-authenticated, like every other scan call: these endpoints take an
 * X-Device-Token and nothing else, and sending them through the account client
 * both fails for want of the header and trips its 401 sign-out on the way.
 */
export async function postDeviceForm(path: string, form: FormData): Promise<void> {
  const headers = await deviceHeaders();
  if (!headers['X-Device-Token']) throw new Error('no device token');
  await scanApi.post(path, form, {
    headers: { ...headers, 'Content-Type': 'multipart/form-data' },
    timeout: UPLOAD_TIMEOUT_MS,
  });
}

const UNKNOWN_CONFIDENCE: Confidence = {
  level: 'not_enough_information',
  text: 'Not enough information about this one yet.',
};

/** Never let a result reach a screen without a confidence level. */
export function withConfidence(result: Partial<ScanResult> & { barcode: string }): ScanResult {
  const confidence =
    result.confidence && typeof result.confidence.level === 'string'
      ? result.confidence
      : UNKNOWN_CONFIDENCE;
  return {
    found: false,
    outcome: 'not_found',
    ...result,
    confidence,
  } as ScanResult;
}

export function newScanId(): string {
  const random = Math.random().toString(36).slice(2, 10);
  return `${Date.now().toString(36)}-${random}`;
}

async function readJson<T>(key: string, fallback: T): Promise<T> {
  try {
    const raw = await AsyncStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

async function writeJson(key: string, value: unknown): Promise<void> {
  try {
    await AsyncStorage.setItem(key, JSON.stringify(value));
  } catch {
    // A full or unavailable store must not break a scan.
  }
}

// --- Device -----------------------------------------------------------------

interface StoredDevice {
  device_key: string;
  token: string;
  /** The account this phone's scans have already been handed to, if any. */
  claimed_for?: string;
}

let devicePromise: Promise<StoredDevice | null> | null = null;

async function registerDevice(): Promise<StoredDevice | null> {
  const deviceKey = (await getInstallationId()).replace(/-/g, '');
  try {
    const response = await scanApi.post('/api/v2/scan/device', {
      device_key: deviceKey,
      platform: 'mobile',
    });
    const stored: StoredDevice = { device_key: deviceKey, token: response.data.token };
    await writeJson(DEVICE_KEY, stored);
    return stored;
  } catch {
    // Offline on first launch. The cache and queue still work.
    return null;
  }
}

/** Register once, then reuse. Called on launch, before anything is scanned. */
export async function ensureDevice(): Promise<StoredDevice | null> {
  const existing = await readJson<StoredDevice | null>(DEVICE_KEY, null);
  if (existing?.token) return existing;
  if (!devicePromise) devicePromise = registerDevice().finally(() => { devicePromise = null; });
  return devicePromise;
}

async function deviceHeaders(): Promise<Record<string, string>> {
  const device = await ensureDevice();
  return device?.token ? { 'X-Device-Token': device.token } : {};
}

/**
 * The device token to claim with, or null if there is nothing to do.
 *
 * Returns null once this phone has been handed to this account, so signing in
 * again on the same phone does not repeat the call on every launch.
 */
export async function tokenToClaimFor(accountId: string): Promise<string | null> {
  const device = await readJson<StoredDevice | null>(DEVICE_KEY, null);
  if (!device?.token || device.claimed_for === accountId) return null;
  return device.token;
}

/** Remember that this phone now belongs to that account. */
export async function markDeviceClaimed(accountId: string): Promise<void> {
  const device = await readJson<StoredDevice | null>(DEVICE_KEY, null);
  if (!device) return;
  await writeJson(DEVICE_KEY, { ...device, claimed_for: accountId });
}

/** Forget the device — used when the server no longer recognises the token. */
async function forgetDevice(): Promise<void> {
  await AsyncStorage.removeItem(DEVICE_KEY).catch(() => {});
}

// --- The offline cache ------------------------------------------------------
//
// This holds the joined response — our confidence and licence beside Open Food
// Facts' product fields. On one person's own phone that is ordinary use, not a
// derived database: it is never published and the attribution renders with it.
// Writing the same pairing on a server would be the breach. See
// docs/architecture/ODBL_DATA_WALL.md.

type Cache = Record<string, { result: ScanResult; cached_at: number }>;

export async function readCached(barcode: string): Promise<ScanResult | null> {
  const cache = await readJson<Cache>(CACHE_KEY, {});
  const entry = cache[barcode];
  if (!entry) return null;
  return { ...entry.result, from_cache: true };
}

export async function cacheResult(result: ScanResult): Promise<void> {
  const cache = await readJson<Cache>(CACHE_KEY, {});
  cache[result.barcode] = { result: { ...result, from_cache: false }, cached_at: Date.now() };
  const barcodes = Object.keys(cache);
  if (barcodes.length > CACHE_LIMIT) {
    // Oldest out first. A phone should not grow without limit.
    barcodes
      .sort((a, b) => cache[a].cached_at - cache[b].cached_at)
      .slice(0, barcodes.length - CACHE_LIMIT)
      .forEach((old) => delete cache[old]);
  }
  await writeJson(CACHE_KEY, cache);
}

// --- The offline queue ------------------------------------------------------

export async function readQueue(): Promise<QueuedScan[]> {
  return readJson<QueuedScan[]>(QUEUE_KEY, []);
}

export async function enqueueScan(entry: QueuedScan): Promise<void> {
  const queue = await readQueue();
  if (queue.some((q) => q.client_scan_id === entry.client_scan_id)) return;
  queue.push(entry);
  await writeJson(QUEUE_KEY, queue);
}

/**
 * Send everything the phone has been holding.
 *
 * An entry is only dropped once the server has accepted it. A replay is safe:
 * the same `client_scan_id` is recognised rather than counted twice.
 */
export async function syncQueue(): Promise<{ sent: number; remaining: number }> {
  const queue = await readQueue();
  if (queue.length === 0) return { sent: 0, remaining: 0 };
  const headers = await deviceHeaders();
  if (!headers['X-Device-Token']) return { sent: 0, remaining: queue.length };

  const left: QueuedScan[] = [];
  let sent = 0;
  for (const entry of queue) {
    try {
      await scanApi.post('/api/v2/scan/events', {
        barcode: entry.barcode,
        client_scan_id: entry.client_scan_id,
        scanned_at: entry.scanned_at,
        queued_offline: entry.queued_offline,
      }, { headers });
      sent += 1;
    } catch {
      left.push(entry);
    }
  }
  await writeJson(QUEUE_KEY, left);
  return { sent, remaining: left.length };
}

// --- Looking one barcode up -------------------------------------------------

function offlineAnswer(barcode: string, cached: ScanResult | null): ScanResult {
  if (cached) return { ...cached, offline: true };
  return withConfidence({
    barcode,
    found: false,
    outcome: 'not_found',
    offline: true,
    can_capture_label: true,
    message: 'You are offline. This scan is saved and will be looked up when you are back.',
  });
}

/**
 * The whole scan, in the order the product asks for: ours, then Open Food
 * Facts, then an honest "not found" — and the cache in front of all three so a
 * phone with no signal still answers.
 */
export async function scanBarcode(barcode: string): Promise<ScanResult> {
  const clean = (barcode || '').trim();
  const cached = await readCached(clean);
  const scanId = newScanId();
  const scannedAt = new Date().toISOString();

  let headers = await deviceHeaders();
  if (!headers['X-Device-Token']) {
    await enqueueScan({ client_scan_id: scanId, barcode: clean, scanned_at: scannedAt, queued_offline: true });
    return offlineAnswer(clean, cached);
  }

  try {
    let response = await scanApi.get(`/api/v2/scan/lookup/${encodeURIComponent(clean)}`, { headers });
    if (response.status === 401) throw new Error('device unknown');
    const result = withConfidence({ ...response.data, barcode: clean });
    await cacheResult(result);
    void enqueueScan({ client_scan_id: scanId, barcode: clean, scanned_at: scannedAt, queued_offline: false })
      .then(() => syncQueue())
      .catch(() => undefined);
    return result;
  } catch (err) {
    const status = (err as { response?: { status?: number } })?.response?.status;
    if (status === 401) {
      // The token stopped working. Re-register once and try again.
      await forgetDevice();
      headers = await deviceHeaders();
      if (headers['X-Device-Token']) {
        try {
          const retry = await scanApi.get(`/api/v2/scan/lookup/${encodeURIComponent(clean)}`, { headers });
          const result = withConfidence({ ...retry.data, barcode: clean });
          await cacheResult(result);
          return result;
        } catch {
          // Fall through to the offline answer.
        }
      }
    }
    await enqueueScan({ client_scan_id: scanId, barcode: clean, scanned_at: scannedAt, queued_offline: true });
    return offlineAnswer(clean, cached);
  }
}

// --- Reading a label --------------------------------------------------------

/** Confirm a label a person has checked. The VC-07 shape: one tap, then it counts. */
export async function confirmLabel(
  barcode: string,
  aiRunId: string,
): Promise<{ confidence: Confidence; fssai_licence?: string | null; confirmations: number } | null> {
  if (typeof aiRunId !== 'string' || !aiRunId.trim()) {
    throw new Error('The label read is missing its confirmation reference. Please try again.');
  }
  const headers = await deviceHeaders();
  if (!headers['X-Device-Token']) return null;
  const clientScanId = newScanId();
  const body = {
    barcode,
    ai_run_id: aiRunId,
    client_scan_id: clientScanId,
  };
  try {
    const response = await api.post('/api/v2/scan/label/confirm', body, { headers });
    return response.data;
  } catch (error) {
    const code = (error as { response?: { data?: { detail?: { code?: string } } } })
      ?.response?.data?.detail?.code;
    if (code !== 'DEVICE_UNKNOWN') throw error;

    // The account is still valid; only the scan-device credential needs
    // replacement. Retry once with the same idempotency key and run reference.
    await forgetDevice();
    const refreshedHeaders = await deviceHeaders();
    if (!refreshedHeaders['X-Device-Token']) throw error;
    const retry = await api.post('/api/v2/scan/label/confirm', body, { headers: refreshedHeaders });
    return retry.data;
  }
}
