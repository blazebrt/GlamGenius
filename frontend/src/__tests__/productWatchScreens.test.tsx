/**
 * Step 12C on the real screens: where the Watch control appears, and where it
 * never does; and the Product watch setting on the Notifications screen.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

import VerdictScreen from '../../app/verdict';
import NotificationsScreen from '../../app/notifications';
import { S as V } from '../strings/verdict';
import { S } from '../strings/productWatch';

const mockParams: { barcode?: string; reference?: string } = { barcode: '8901058000191' };
jest.mock('expo-router', () => ({
  useLocalSearchParams: () => mockParams,
  useRouter: () => ({ push: jest.fn(), replace: jest.fn(), back: jest.fn() }),
}));
jest.mock('expo-image-picker', () => ({
  requestCameraPermissionsAsync: jest.fn(), launchCameraAsync: jest.fn(),
}));
jest.mock('expo-notifications', () => ({
  __esModule: true, AndroidImportance: { DEFAULT: 3 }, setNotificationChannelAsync: jest.fn(),
  getPermissionsAsync: jest.fn(), requestPermissionsAsync: jest.fn(), getExpoPushTokenAsync: jest.fn(),
}));
jest.mock('expo-constants', () => ({ __esModule: true, default: { expoConfig: { extra: { eas: { projectId: 'project-1' } } } } }));
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(), setItem: jest.fn(), removeItem: jest.fn(),
}));
jest.mock('../services/speech', () => ({
  isSpeechAvailable: () => false, speak: jest.fn(), stopSpeaking: jest.fn(),
}));
jest.mock('../services/errorReports', () => ({
  flushReports: jest.fn(async () => undefined), makeReport: jest.fn(), submitReport: jest.fn(),
}));

const mockGetProductVerdict = jest.fn();
jest.mock('../services/verdictClient', () => ({
  getProductVerdict: (...args: unknown[]) => mockGetProductVerdict(...args),
}));

const mockReadWatch = jest.fn();
jest.mock('../services/productScan', () => ({
  readProductWatch: (...args: unknown[]) => mockReadWatch(...args),
  watchProduct: jest.fn(),
  unwatchProduct: jest.fn(),
}));

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
  getNotificationPreferences: jest.fn(),
  patchNotificationPreferences: jest.fn(),
  registerNotificationDevice: jest.fn(),
  unregisterNotificationDevice: jest.fn(),
}));

let mockRegistrationState = 'registered';
jest.mock('../store/userStore', () => ({
  useUserStore: (selector: (state: { registrationState: string }) => unknown) =>
    selector({ registrationState: mockRegistrationState }),
}));

const apiV2 = jest.requireMock('../services/apiV2') as Record<string, jest.Mock>;
const mockNotifications = jest.requireMock('expo-notifications') as { requestPermissionsAsync: jest.Mock };

const verdictSource = (overrides: Record<string, unknown> = {}) => ({
  outcome: 'graded', grade: 'C', productName: 'Oat Cereal',
  totalSugarG: 12, saltG: 0.8, totalFatG: 4, proteinG: 6, packSizeG: 180,
  negatives: [], positives: [], components: [], ingredients: [],
  officialRecords: null, communityObservations: null,
  physicalPackContext: true,
  labelVersion: {
    id: 'snapshot-1', versionNumber: 1, contentFingerprint: 'f'.repeat(64),
    observedAt: '2026-09-01T00:00:00+00:00', changedFields: [], completeness: 'complete',
  },
  ...overrides,
});

const watchState = {
  contract_version: 'step-12c-v1', barcode: '8901058000191', watching: false, label_version: null,
  started_at: null, watchable: true, anchorable_label_version: 1, watching_this_pack: false, reason: null,
  delivery: { notifications_enabled: true, product_watch_enabled: true, native_push_enabled: false },
};

async function renderVerdict(source: Record<string, unknown>) {
  mockGetProductVerdict.mockResolvedValue(source);
  render(<VerdictScreen />);
  // The screen has finished loading once its closing actions are shown. A
  // reference view offers "scan first" in place of reporting.
  const settled = mockParams.reference ? V.referenceView.scanFirstAction : V.communityObservations.reportAction;
  await waitFor(() => expect(screen.getByText(settled)).toBeTruthy());
}

beforeEach(() => {
  jest.clearAllMocks();
  mockParams.barcode = '8901058000191';
  delete mockParams.reference;
  mockRegistrationState = 'registered';
  mockReadWatch.mockResolvedValue(watchState);
});

describe('Where the Watch control appears', () => {
  it('appears for a signed-in person holding a confirmed pack', async () => {
    await renderVerdict(verdictSource());

    expect(await screen.findByText(S.watch)).toBeTruthy();
    expect(mockReadWatch).toHaveBeenCalledWith('8901058000191');
  });

  it('never appears for an anonymous visitor', async () => {
    mockRegistrationState = 'anonymous';
    await renderVerdict(verdictSource());

    expect(screen.queryByTestId('product-watch')).toBeNull();
    expect(mockReadWatch).not.toHaveBeenCalled();
  });

  it('never appears in a reference view opened from another product', async () => {
    mockParams.reference = 'alternative';
    await renderVerdict(verdictSource({ physicalPackContext: false }));

    expect(screen.queryByTestId('product-watch')).toBeNull();
    expect(mockReadWatch).not.toHaveBeenCalled();
  });

  it('never appears when the server did not grant physical-pack context', async () => {
    await renderVerdict(verdictSource({ physicalPackContext: false }));

    expect(screen.queryByTestId('product-watch')).toBeNull();
    expect(mockReadWatch).not.toHaveBeenCalled();
  });

  it('never appears for Open Food Facts facts with no confirmed label version', async () => {
    await renderVerdict(verdictSource({ labelVersion: null, factsProvenance: 'open_food_facts' }));

    expect(screen.queryByTestId('product-watch')).toBeNull();
    expect(mockReadWatch).not.toHaveBeenCalled();
  });
});

describe('Product watch on the Notifications screen', () => {
  const preferences = {
    enabled: true, native_push_enabled: false, preferred_hour: 9,
    quiet_hours: { start: 21, end: 7 }, modules: {},
    topics: { today_style: true, care: true, event_preparation: true, maintenance: true, product_watch: true },
  };

  beforeEach(() => {
    (AsyncStorage.getItem as jest.Mock).mockResolvedValue('11111111-1111-4111-8111-111111111111');
    apiV2.getNotificationPreferences.mockResolvedValue({ preferences, recent: [], current_device_registered: false });
    apiV2.patchNotificationPreferences.mockImplementation(async (body: { topics?: Record<string, boolean> }) => ({
      preferences: { ...preferences, topics: { ...preferences.topics, ...(body.topics ?? {}) } },
    }));
  });

  it('has its own row, separate from the master switch and from native push', async () => {
    render(<NotificationsScreen />);
    const row = await screen.findByLabelText(S.notificationsRow);

    expect(row.props.value).toBe(true);
    expect(screen.getByLabelText('Daily appearance notification')).toBeTruthy();
    expect(screen.getByLabelText('Native push on this device')).toBeTruthy();

    await act(async () => { fireEvent(row, 'valueChange', false); });

    expect(apiV2.patchNotificationPreferences).toHaveBeenCalledWith({ topics: { product_watch: false } });
    // The master switch was not touched, and no permission prompt appeared.
    expect(apiV2.patchNotificationPreferences).not.toHaveBeenCalledWith(expect.objectContaining({ enabled: expect.anything() }));
    expect(mockNotifications.requestPermissionsAsync).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByLabelText(S.notificationsRow).props.value).toBe(false));
  });

  it('defaults on for a server that does not yet send the topic', async () => {
    const { product_watch: _omitted, ...older } = preferences.topics;
    apiV2.getNotificationPreferences.mockResolvedValue({
      preferences: { ...preferences, topics: older }, recent: [], current_device_registered: false,
    });
    render(<NotificationsScreen />);

    expect((await screen.findByLabelText(S.notificationsRow)).props.value).toBe(true);
  });
});
