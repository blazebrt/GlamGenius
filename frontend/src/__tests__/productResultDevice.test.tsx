/**
 * The Product Result is read as this phone.
 *
 * The route requires ``X-Device-Token`` and has no device-less form, so the
 * screen must send the device it already stored, never register a new one just
 * because a result was opened, and never ask at all without a credential.
 *
 * These tests follow the real seam — verdict.tsx → verdictClient → productScan
 * — and stop only at the shared HTTP client, so a reader that dropped the
 * header, or went back to an uncredentialed path, fails here.
 */
import React from 'react';
import * as fs from 'fs';
import * as path from 'path';
import { act, render, screen, waitFor } from '@testing-library/react-native';

import VerdictScreen from '../../app/verdict';
import { REFERENCE_ALTERNATIVE } from '../components/verdict/BetterOption';
import { S } from '../strings/verdict';

let mockParams: Record<string, string> = { barcode: '8901058000191' };
jest.mock('expo-router', () => ({
  useLocalSearchParams: () => mockParams,
  useRouter: () => ({ push: jest.fn(), replace: jest.fn(), back: jest.fn() }),
}));
jest.mock('expo-image-picker', () => ({
  requestCameraPermissionsAsync: jest.fn(async () => ({ granted: true })),
  launchCameraAsync: jest.fn(async () => ({ canceled: true, assets: [] })),
}));
jest.mock('../services/speech', () => ({
  isSpeechAvailable: () => false, speak: jest.fn(), stopSpeaking: jest.fn(),
}));
jest.mock('../services/errorReports', () => ({
  flushReports: jest.fn(async () => undefined), makeReport: jest.fn(), submitReport: jest.fn(),
}));
jest.mock('../store/userStore', () => ({
  useUserStore: (selector: (state: { registrationState: string }) => unknown) =>
    selector({ registrationState: 'anonymous' }),
}));
// Everything else the screen reads that is not the Product Result.
jest.mock('../services/apiV2', () => ({
  readScanMemory: jest.fn(async () => null),
  saveScanDecision: jest.fn(),
  readScanShelfStatus: jest.fn(async () => ({ status: 'not_eligible' })),
  addScanProductToShelf: jest.fn(),
  readCommunityPackContext: jest.fn(async () => null),
  readOwnCommunityReports: jest.fn(async () => []),
  submitCommunityObservation: jest.fn(),
  withdrawCommunityObservation: jest.fn(),
  uploadMedia: jest.fn(),
}));

// The shared HTTP client: the one place the requests are observed.
const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock('../services/api', () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: (...args: unknown[]) => mockApiPost(...args),
    put: jest.fn(), delete: jest.fn(),
  },
  errorMessage: jest.fn(),
}));
// The device-registration client inside productScan.
const mockRegister = jest.fn();
jest.mock('axios', () => {
  const instance = { post: (...args: unknown[]) => mockRegister(...args), get: jest.fn() };
  return { __esModule: true, default: { create: () => instance }, create: () => instance };
});
let mockStoredDevice: string | null = null;
jest.mock('../services/secureSessionStorage', () => ({
  secureSessionStorage: {
    getItem: jest.fn(async () => mockStoredDevice),
    setItem: jest.fn(async () => undefined),
    removeItem: jest.fn(async () => undefined),
  },
}));
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: { getItem: jest.fn(async () => null), setItem: jest.fn(), removeItem: jest.fn() },
}));
jest.mock('../services/deviceIdentity', () => ({ getInstallationId: jest.fn(async () => 'install-1') }));

const BARCODE = '8901058000191';
const TOKEN = 'stored-device-token';
const wire = {
  outcome: 'graded', grade: 'C', band: 'yellow', product_name: 'Northstar Corn Flakes',
  taxonomy: { domain: 'consumed', category: 'packaged_food', subcategory: 'cereal' },
  decision: { action: 'wait', reason_key: 'processing' },
  nutrition: { total_sugar_g: 8, salt_g: 0.5, total_fat_g: null, protein_g: 7 },
  components: [], ingredients: [], negatives: [], positives: [],
  quantity_guidance: null, purity_note: null, missing: [],
  confidence: { level: 'unverified', text: 'Unverified' },
  attribution: null, pack_size_g: 200, basis: 'solid', result_contract_version: 'v1',
  physical_pack_context: true,
};

const verdictCalls = () => mockApiGet.mock.calls.filter(([url]) => /\/api\/v2\/scan\/verdict\/[^/?]+(\?|$)/.test(String(url)));
const purchaseCalls = () => mockApiGet.mock.calls.filter(([url]) => String(url).includes('/purchase-check'));

beforeEach(() => {
  jest.clearAllMocks();
  mockParams = { barcode: BARCODE };
  mockStoredDevice = JSON.stringify({ device_key: 'key-1', token: TOKEN });
  mockApiGet.mockImplementation(async (url: string) => (
    String(url).includes('/purchase-check') ? { data: null } : { data: wire }
  ));
});

async function renderScreen() {
  render(<VerdictScreen />);
  await act(async () => { await Promise.resolve(); });
}

describe('The Product Result is read with the stored device credential', () => {
  it('sends the stored X-Device-Token on the physical Product Result read', async () => {
    await renderScreen();
    await waitFor(() => expect(screen.getByText('Northstar Corn Flakes')).toBeTruthy());
    expect(verdictCalls()).toHaveLength(1);
    const [url, config] = verdictCalls()[0];
    expect(url).toBe(`/api/v2/scan/verdict/${BARCODE}`);
    expect(config).toEqual(expect.objectContaining({ headers: { 'X-Device-Token': TOKEN } }));
  });

  it('sends the device credential and the reference ceiling for a Better Option view', async () => {
    mockParams = { barcode: BARCODE, reference: REFERENCE_ALTERNATIVE };
    await renderScreen();
    await waitFor(() => expect(screen.getByText('Northstar Corn Flakes')).toBeTruthy());
    const [url, config] = verdictCalls()[0];
    expect(url).toBe(`/api/v2/scan/verdict/${BARCODE}?physical_pack_context=false`);
    expect(config).toEqual(expect.objectContaining({ headers: { 'X-Device-Token': TOKEN } }));
  });

  it('sends no Product Result request without a device credential, and shows the ordinary failure', async () => {
    mockStoredDevice = null;
    await renderScreen();
    await waitFor(() => expect(screen.getByText(S.loading.failedTitle)).toBeTruthy());
    expect(verdictCalls()).toHaveLength(0);
    expect(mockApiGet).not.toHaveBeenCalled();
  });

  it('never registers a device just because a result was opened', async () => {
    mockStoredDevice = null;
    await renderScreen();
    await waitFor(() => expect(screen.getByText(S.loading.failedTitle)).toBeTruthy());
    expect(mockRegister).not.toHaveBeenCalled();
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('keeps the purchase check on the same stored device identity', async () => {
    await renderScreen();
    await waitFor(() => expect(purchaseCalls()).toHaveLength(1));
    const [url, config] = purchaseCalls()[0];
    expect(url).toBe(`/api/v2/scan/verdict/${BARCODE}/purchase-check`);
    expect(config).toEqual(expect.objectContaining({ headers: { 'X-Device-Token': TOKEN } }));
    expect(mockRegister).not.toHaveBeenCalled();
  });

  it('leaves no uncredentialed Product Result reader to fall back to', () => {
    const root = path.resolve(__dirname, '..');
    const apiV2 = fs.readFileSync(path.join(root, 'services', 'apiV2.ts'), 'utf8');
    const client = fs.readFileSync(path.join(root, 'services', 'verdictClient.ts'), 'utf8');
    expect(apiV2).not.toMatch(/export const readProductVerdict\b/);
    expect(client).toContain("import { readDeviceProductVerdict } from './productScan';");
    expect(client).not.toMatch(/readProductVerdict\(/);
  });
});
