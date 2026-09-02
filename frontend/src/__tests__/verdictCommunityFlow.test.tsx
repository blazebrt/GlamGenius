/**
 * The Community flow on the real verdict screen, not in isolation.
 *
 * Component tests proved the pieces render. They could not prove the screen
 * ever shows them, and it did not: "Report what you saw" has to exist for the
 * very first reporter, who by definition sees no public signal at all.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import VerdictScreen from '../../app/verdict';
import { S } from '../strings/verdict';

const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useLocalSearchParams: () => ({ barcode: '8901058000191' }),
  useRouter: () => ({ push: mockPush, replace: jest.fn(), back: jest.fn() }),
}));
jest.mock('expo-image-picker', () => ({
  requestCameraPermissionsAsync: jest.fn(async () => ({ granted: true })),
  launchCameraAsync: jest.fn(async () => ({ canceled: false, assets: [{ uri: 'file:///pack.jpg' }] })),
}));
jest.mock('../services/speech', () => ({
  isSpeechAvailable: () => false, speak: jest.fn(), stopSpeaking: jest.fn(),
}));
jest.mock('../services/errorReports', () => ({
  flushReports: jest.fn(async () => undefined), makeReport: jest.fn(), submitReport: jest.fn(),
}));

const mockGetProductVerdict = jest.fn();
jest.mock('../services/verdictClient', () => ({
  getProductVerdict: (barcode: string) => mockGetProductVerdict(barcode),
}));

const mockReadContext = jest.fn();
const mockSubmit = jest.fn();
const mockWithdraw = jest.fn();
const mockUploadMedia = jest.fn();
jest.mock('../services/apiV2', () => ({
  readCommunityPackContext: (...args: unknown[]) => mockReadContext(...args),
  submitCommunityObservation: (...args: unknown[]) => mockSubmit(...args),
  withdrawCommunityObservation: (...args: unknown[]) => mockWithdraw(...args),
  uploadMedia: (...args: unknown[]) => mockUploadMedia(...args),
}));

let mockRegistrationState = 'registered';
jest.mock('../store/userStore', () => ({
  useUserStore: (selector: (state: { registrationState: string }) => unknown) =>
    selector({ registrationState: mockRegistrationState }),
}));

const verdictSource = (communityObservations: unknown = null) => ({
  outcome: 'graded', grade: 'C', productName: 'Oat Cereal',
  totalSugarG: 12, saltG: 0.8, totalFatG: 4, proteinG: 6, packSizeG: 180,
  negatives: [], positives: [], components: [], ingredients: [],
  officialRecords: null, communityObservations,
});

const publicSignals = {
  policy_version: 'community-observations-v1', public_enabled: true, active_window_days: 90,
  brand_reply_url: 'https://example.org/reply',
  signals: [{
    observation_code: 'seal_broken', scope: 'batch', batch_number: 'b-123',
    independent_reporters: 3, first_reported_at: '2026-08-01T00:00:00+00:00',
    last_reported_at: '2026-08-20T00:00:00+00:00',
    analysis_score_eligible: false, official_finding: false,
  }],
};

async function renderScreen(communityObservations: unknown = null) {
  mockGetProductVerdict.mockResolvedValue(verdictSource(communityObservations));
  const view = render(<VerdictScreen />);
  await waitFor(() => expect(screen.getByText(S.communityObservations.reportAction)).toBeTruthy());
  return view;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockRegistrationState = 'registered';
  mockReadContext.mockResolvedValue({
    barcode: '8901058000191', has_current_scan_context: true,
    batch_context_available: true, batch_number: 'b-123',
    batch_scoped_observation_codes: ['seal_broken'],
  });
  mockUploadMedia.mockResolvedValue({ id: 'asset-1' });
  mockSubmit.mockResolvedValue({ id: 'report-1', created: true });
  mockWithdraw.mockResolvedValue({ id: 'report-1', status: 'withdrawn' });
});

describe('the community action on the real verdict screen', () => {
  it('offers reporting when there is no public signal at all', async () => {
    // The first reporter necessarily sees nothing published. If the action
    // lived inside the signal card, nobody could ever become the first one.
    await renderScreen(null);
    expect(screen.queryByText(S.communityObservations.heading)).toBeNull();
    expect(screen.getByLabelText(S.communityObservations.reportAction)).toBeTruthy();
  });

  it('offers reporting when public display is switched off', async () => {
    await renderScreen({ ...publicSignals, public_enabled: false, signals: [] });
    expect(screen.queryByText(S.communityObservations.heading)).toBeNull();
    expect(screen.getByLabelText(S.communityObservations.reportAction)).toBeTruthy();
  });

  it('shows the card and the action together once a signal is public', async () => {
    await renderScreen(publicSignals);
    expect(screen.getByText(S.communityObservations.heading)).toBeTruthy();
    expect(screen.getByText(/3 shoppers reported a broken seal/)).toBeTruthy();
    expect(screen.getByLabelText(S.communityObservations.reportAction)).toBeTruthy();
  });

  it('sends a signed-out shopper to the existing authentication route', async () => {
    mockRegistrationState = 'signed_out';
    await renderScreen(null);
    fireEvent.press(screen.getByLabelText(S.communityObservations.reportAction));
    expect(mockPush).toHaveBeenCalledWith('/(auth)/welcome');
    // No sheet, and no second onboarding of our own.
    expect(screen.queryByText(S.communityObservations.chooseObservation)).toBeNull();
  });

  it('opens the community sheet for a signed-in shopper, not the label-error one', async () => {
    await renderScreen(null);
    fireEvent.press(screen.getByLabelText(S.communityObservations.reportAction));
    await waitFor(() => expect(screen.getByText(S.communityObservations.chooseObservation)).toBeTruthy());
    // The correction flow is a different job and stays separate.
    expect(screen.queryByText(S.report.subtitle)).toBeNull();
    expect(mockReadContext).toHaveBeenCalledWith('8901058000191');
  });
});

describe('sending an observation from the real screen', () => {
  async function openSheet() {
    await renderScreen(null);
    fireEvent.press(screen.getByLabelText(S.communityObservations.reportAction));
    await waitFor(() => expect(screen.getByText(S.communityObservations.chooseObservation)).toBeTruthy());
  }

  it('uploads the photo under the community purpose and posts the observation', async () => {
    await openSheet();
    fireEvent.press(screen.getByLabelText(S.communityObservations.observation.seal_broken));
    await act(async () => { fireEvent.press(screen.getByLabelText(S.communityObservations.photoAction)); });

    expect(mockUploadMedia).toHaveBeenCalledWith(
      expect.objectContaining({ uri: 'file:///pack.jpg' }), 'community_observation',
    );

    await act(async () => { fireEvent.press(screen.getByLabelText(S.communityObservations.submit)); });
    const body = mockSubmit.mock.calls[0][0];
    expect(body).toMatchObject({
      barcode: '8901058000191', observation_code: 'seal_broken', photo_asset_id: 'asset-1',
    });
    // Identity comes from the session and the device token, never the body.
    expect(Object.keys(body).sort()).toEqual(
      ['barcode', 'client_report_id', 'observation_code', 'photo_asset_id'],
    );
    expect(screen.getByText(S.communityObservations.reportSaved)).toBeTruthy();
    for (const overclaim of [/verified/i, /confirmed/i, /proven/i, /validated/i]) {
      expect(screen.queryByText(overclaim)).toBeNull();
    }
  });

  it('routes to label capture when the server says this pack has no batch yet', async () => {
    mockReadContext.mockResolvedValue({
      barcode: '8901058000191', has_current_scan_context: true,
      batch_context_available: false, batch_number: null,
      batch_scoped_observation_codes: ['seal_broken'],
    });
    await openSheet();
    fireEvent.press(screen.getByLabelText(S.communityObservations.observation.seal_broken));

    expect(screen.getByText(S.communityObservations.batchCaptureRequired)).toBeTruthy();
    const { TextInput } = jest.requireActual('react-native');
    // Never a box to type the lot into.
    expect(screen.UNSAFE_queryAllByType(TextInput)).toHaveLength(0);

    fireEvent.press(screen.getByLabelText(S.communityObservations.captureLabelAction));
    expect(mockPush).toHaveBeenCalledWith(
      expect.objectContaining({ pathname: '/scan-product' }),
    );
    expect(mockSubmit).not.toHaveBeenCalled();
  });

  it('withdraws the shopper′s own observation through the real endpoint', async () => {
    await openSheet();
    fireEvent.press(screen.getByLabelText(S.communityObservations.observation.pack_leaking));
    await act(async () => { fireEvent.press(screen.getByLabelText(S.communityObservations.photoAction)); });
    await act(async () => { fireEvent.press(screen.getByLabelText(S.communityObservations.submit)); });

    await act(async () => { fireEvent.press(screen.getByLabelText(S.communityObservations.withdraw)); });
    expect(mockWithdraw).toHaveBeenCalledWith('report-1');
    expect(screen.getByText(S.communityObservations.withdrawn)).toBeTruthy();
  });

  it('shows keyed copy for a backend reason, never the backend′s own prose', async () => {
    mockSubmit.mockRejectedValue({
      response: { data: { detail: {
        reason: 'batch_capture_required',
        message: 'Capture the pack label first so we can match the batch.',
      } } },
    });
    await openSheet();
    fireEvent.press(screen.getByLabelText(S.communityObservations.observation.seal_broken));
    await act(async () => { fireEvent.press(screen.getByLabelText(S.communityObservations.photoAction)); });
    await act(async () => { fireEvent.press(screen.getByLabelText(S.communityObservations.submit)); });

    expect(screen.getByText(S.communityObservations.batchCaptureRequired)).toBeTruthy();
    expect(screen.queryByText(/we can match the batch/)).toBeNull();
  });
});
