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

import { api } from './api';
import { V2 } from './apiV2';

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
  note?: string;
  /** A local photo URI, uploaded with the report when there is a connection. */
  photo_uri?: string | null;
  reported_at: string;
}

const newId = (): string =>
  `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

export const makeReport = (
  fields: Omit<ErrorReport, 'client_report_id' | 'reported_at'>
): ErrorReport => ({
  ...fields,
  client_report_id: newId(),
  reported_at: new Date().toISOString(),
});

async function readQueue(): Promise<ErrorReport[]> {
  try {
    const raw = await AsyncStorage.getItem(QUEUE_KEY);
    return raw ? (JSON.parse(raw) as ErrorReport[]) : [];
  } catch {
    return [];
  }
}

async function writeQueue(rows: ErrorReport[]): Promise<void> {
  try {
    await AsyncStorage.setItem(QUEUE_KEY, JSON.stringify(rows));
  } catch {
    // A full store must not lose the report the person is looking at; the
    // send below is still attempted.
  }
}

async function post(report: ErrorReport): Promise<void> {
  const form = new FormData();
  form.append('client_report_id', report.client_report_id);
  form.append('subject', report.subject);
  form.append('reason', report.reason);
  if (report.barcode) form.append('barcode', report.barcode);
  if (report.note) form.append('note', report.note);
  if (report.photo_uri) {
    form.append('photo', {
      uri: report.photo_uri, name: 'pack.jpg', type: 'image/jpeg',
    } as unknown as Blob);
  }
  await api.post(`${V2}/reports/label-error`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
}

/** Send now if we can, keep it if we cannot. Returns true when it went. */
export async function submitReport(report: ErrorReport): Promise<boolean> {
  try {
    await post(report);
    return true;
  } catch {
    const queue = await readQueue();
    if (!queue.some((row) => row.client_report_id === report.client_report_id)) {
      queue.push(report);
      await writeQueue(queue);
    }
    return false;
  }
}

/** Flush anything held from a previous session. Safe to call on every launch. */
export async function flushReports(): Promise<{ sent: number; remaining: number }> {
  const queue = await readQueue();
  if (queue.length === 0) return { sent: 0, remaining: 0 };
  const left: ErrorReport[] = [];
  let sent = 0;
  for (const report of queue) {
    try {
      await post(report);
      sent += 1;
    } catch {
      left.push(report);
    }
  }
  await writeQueue(left);
  return { sent, remaining: left.length };
}

export const pendingReportCount = async (): Promise<number> => (await readQueue()).length;
