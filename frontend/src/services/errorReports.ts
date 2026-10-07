/**
 * Report an error, in one tap, from anywhere a number appears.
 *
 * Never an email. An email address is a way of not receiving reports: it asks
 * a person to leave the app, compose a message, describe where they were, and
 * remember what the number said. Nobody does that. This sends structured
 * options plus an optional photo, and the queue survives being offline —
 * the moment somebody is standing in a shop with no signal is exactly when
 * they notice a wrong number.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

import { V2 } from './apiV2';
import { postDeviceForm } from './productScan';
import { authAccountId } from '../store/authGeneration';

const QUEUE_KEY = 'glamgenius_error_reports_v1';

export type ReportReason =
  | 'wrong_number'
  | 'wrong_ingredient'
  | 'wrong_product'
  | 'wrong_grade'
  | 'pack_changed'
  | 'something_else';

export interface ErrorReport {
  client_report_id: string;
  barcode: string | null;
  /** What was on screen when they tapped: "sugar", "grade", an ingredient name. */
  subject: string;
  reason: ReportReason;
  /** A local photo URI, uploaded with the report when there is a connection. */
  photo_uri?: string | null;
  reported_at: string;
  /**
   * The account signed in when the report was made, or null when nobody was;
   * absent on older queued reports, which reads as null. Not a credential.
   * It is sent as that account or not yet, never as anyone else (see
   * ``productScan.sendQueuedScan`` for the same rule on scans).
   */
  owner?: string | null;
}

const newId = (): string =>
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

export const makeReport = (
  fields: Omit<ErrorReport, 'client_report_id' | 'reported_at' | 'owner'>
): ErrorReport => ({
  ...fields,
  client_report_id: newId(),
  reported_at: new Date().toISOString(),
  // Whoever is signed in as the person taps report, taken now.
  owner: authAccountId() || null,
});

/** Whether ``report`` may be sent now: signed out, or as its own account. */
const sendableNow = (report: ErrorReport): boolean =>
  !report.owner || report.owner === authAccountId();

let reportQueueLane: Promise<unknown> = Promise.resolve();

function withReportQueueLane<T>(work: () => Promise<T>): Promise<T> {
  const run = reportQueueLane.then(work, work);
  reportQueueLane = run.then(() => undefined, () => undefined);
  return run;
}

const REASONS: readonly ReportReason[] = [
  'wrong_number', 'wrong_ingredient', 'wrong_product', 'wrong_grade', 'pack_changed', 'something_else',
];

function isReport(value: unknown): value is ErrorReport {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  return typeof row.client_report_id === 'string' && row.client_report_id.trim().length > 0
    && (row.barcode === null || typeof row.barcode === 'string')
    && typeof row.subject === 'string' && row.subject.trim().length > 0
    && REASONS.includes(row.reason as ReportReason)
    && typeof row.reported_at === 'string' && Number.isFinite(Date.parse(row.reported_at))
    && (row.owner === undefined || row.owner === null || (typeof row.owner === 'string' && row.owner.length > 0))
    && (row.photo_uri === undefined || row.photo_uri === null || typeof row.photo_uri === 'string');
}

/** Unknown storage is never empty storage on a read-modify-write path. */
async function readQueueForMutation(): Promise<ErrorReport[]> {
  const raw = await AsyncStorage.getItem(QUEUE_KEY);
  if (raw === null) return [];
  const rows: unknown = JSON.parse(raw);
  if (!Array.isArray(rows) || !rows.every(isReport)) {
    throw new Error('The saved report queue could not be read safely.');
  }
  // Legacy duplicate IDs are one logical report, with the first saved owner
  // and payload intact. Do not use timestamps or today's account as identity.
  const seen = new Set<string>();
  return rows.filter((row) => {
    if (seen.has(row.client_report_id)) return false;
    seen.add(row.client_report_id);
    return true;
  });
}

async function writeQueue(rows: ErrorReport[]): Promise<void> {
  await AsyncStorage.setItem(QUEUE_KEY, JSON.stringify(rows));
}

async function post(report: ErrorReport): Promise<void> {
  const form = new FormData();
  form.append('client_report_id', report.client_report_id);
  form.append('subject', report.subject);
  form.append('reason', report.reason);
  if (report.barcode) form.append('barcode', report.barcode);
  if (report.photo_uri) {
    form.append('photo', {
      uri: report.photo_uri, name: 'pack.jpg', type: 'image/jpeg',
    } as unknown as Blob);
  }
  // Always as this device: the endpoint needs the device token, because the
  // person who notices a wrong number is often not signed in. A report made
  // signed in also carries that same account's session, which is what makes it
  // theirs on the server; one made signed out carries none and stays nobody's,
  // whoever is signed in when it is finally sent.
  await postDeviceForm(`${V2}/reports/label-error`, form, { asAccount: report.owner ?? null });
}

/** True: sent. False: durably queued. Rejection: neither could be proven. */
export async function submitReport(report: ErrorReport): Promise<boolean> {
  if (!isReport(report)) throw new Error('This report could not be saved safely.');
  try {
    if (!sendableNow(report)) throw new Error('made by an account that is not signed in');
    await post(report);
    return true;
  } catch {
    await withReportQueueLane(async () => {
      const queue = await readQueueForMutation();
      if (!queue.some((row) => row.client_report_id === report.client_report_id)) {
        await writeQueue([...queue, report]);
      }
    });
    return false;
  }
}

/** Flush anything held from a previous session. Safe to call on every launch. */
async function flushReportsOnce(): Promise<{ sent: number; remaining: number }> {
  const queue = await withReportQueueLane(readQueueForMutation);
  const acknowledged = new Set<string>();
  for (const report of queue) {
    // A report made as another account waits for that account, intact.
    if (!sendableNow(report)) {
      continue;
    }
    try {
      await post(report);
      acknowledged.add(report.client_report_id);
    } catch {
      // Leave it intact; only a successful send is acknowledgement authority.
    }
  }
  const remaining = await withReportQueueLane(async () => {
    const latest = await readQueueForMutation();
    const left = latest.filter((row) => !acknowledged.has(row.client_report_id));
    if (acknowledged.size > 0) await writeQueue(left);
    return left.length;
  });
  return { sent: acknowledged.size, remaining };
}

let activeFlush: Promise<{ sent: number; remaining: number }> | null = null;

/** Network sends never hold the mutation lane. Overlapping callers share work. */
export function flushReports(): Promise<{ sent: number; remaining: number }> {
  if (activeFlush) return activeFlush;
  const run = flushReportsOnce();
  activeFlush = run;
  const settled = () => { if (activeFlush === run) activeFlush = null; };
  void run.then(settled, settled);
  return run;
}

export const pendingReportCount = async (): Promise<number> => {
  try { return (await withReportQueueLane(readQueueForMutation)).length; }
  catch { return 0; } // Display-only; never used as mutation authority.
};
