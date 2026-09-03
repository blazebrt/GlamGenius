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
const mockOwnReports = jest.fn();
const mockUploadMedia = jest.fn();
jest.mock('../services/apiV2', () => ({
  readCommunityPackContext: (...args: unknown[]) => mockReadContext(...args),
  submitCommunityObservation: (...args: unknown[]) => mockSubmit(...args),
  withdrawCommunityObservation: (...args: unknown[]) => mockWithdraw(...args),
  readOwnCommunityReports: (...args: unknown[]) => mockOwnReports(...args),
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
  mockOwnReports.mockResolvedValue([]);
  mockSubmit.mockResolvedValue({ id: 'report-1', created: true });
  mockWithdraw.mockResolvedValue({ id: 'report-1', status: 'withdrawn' });
});

/**
 * The first render in this file pays for the whole verdict screen's module
 * graph — every component, string table and service it imports — before a
 * single assertion runs. Locally that is a few hundred milliseconds; on a
 * contended CI runner it has been measured close to Jest's 5-second default,
 * and a suite that fails on machine speed rather than on behaviour tells
 * nobody anything.
 *
 * This raises the clock for that one render and nothing else. Every assertion
 * is unchanged, and a screen that genuinely stops rendering the action still
 * fails the test.
 */
const FIRST_RENDER_TIMEOUT_MS = 20_000;

describe('the community action on the real verdict screen', () => {
  it('offers reporting when there is no public signal at all', async () => {
    // The first reporter necessarily sees nothing published. If the action
    // lived inside the signal card, nobody could ever become the first one.
    await renderScreen(null);
    expect(screen.queryByText(S.communityObservations.heading)).toBeNull();
    expect(screen.getByLabelText(S.communityObservations.reportAction)).toBeTruthy();
  }, FIRST_RENDER_TIMEOUT_MS);

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
    mockOwnReports.mockResolvedValue([{
      id: 'report-1', barcode: '8901058000191', observation_code: 'pack_leaking',
      scope: 'batch', batch_number: 'b-123', status: 'accepted',
      created_at: '2026-09-01T00:00:00+00:00', withdrawn_at: null,
    }]);
    await act(async () => { fireEvent.press(screen.getByLabelText(S.communityObservations.submit)); });

    // Each own row carries its own retraction, named so a screen reader says
    // which observation is being taken back.
    const label = `${S.communityObservations.withdraw}: ${S.communityObservations.observation.pack_leaking}`;
    await act(async () => { fireEvent.press(screen.getByLabelText(label)); });
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


// ---------------------------------------------------------------------------
// Retrying one draft, and managing what you already sent
// ---------------------------------------------------------------------------

const ownRow = (over: Partial<Record<string, unknown>> = {}) => ({
  id: 'report-1', barcode: '8901058000191', observation_code: 'seal_broken',
  scope: 'batch', batch_number: 'b-123', status: 'accepted',
  created_at: '2026-09-01T00:00:00+00:00', withdrawn_at: null, ...over,
});

async function openSheet() {
  await renderScreen(null);
  fireEvent.press(screen.getByLabelText(S.communityObservations.reportAction));
  await waitFor(() => expect(screen.getByText(S.communityObservations.chooseObservation)).toBeTruthy());
}

async function compose(code = 'seal_broken') {
  fireEvent.press(screen.getByLabelText(S.communityObservations.observation[code]));
  await act(async () => { fireEvent.press(screen.getByLabelText(S.communityObservations.photoAction)); });
}

describe('one draft keeps one idempotency key', () => {
  it('reuses the key when a lost response makes the shopper press send again', async () => {
    // The failure this closes: the first POST reached the server and created a
    // report, the response was lost, and a fresh key on the retry made the
    // server create a second identical report from one person.
    mockSubmit
      .mockRejectedValueOnce(Object.assign(new Error('network'), { response: undefined }))
      .mockResolvedValueOnce({ id: 'report-1', created: false });

    await openSheet();
    await compose();
    await act(async () => { fireEvent.press(screen.getByLabelText(S.communityObservations.submit)); });
    await act(async () => { fireEvent.press(screen.getByLabelText(S.communityObservations.submit)); });

    const [first] = mockSubmit.mock.calls[0];
    const [second] = mockSubmit.mock.calls[1];
    expect(second.client_report_id).toBe(first.client_report_id);
    expect(second.barcode).toBe(first.barcode);
    expect(second.observation_code).toBe(first.observation_code);
    expect(second.photo_asset_id).toBe(first.photo_asset_id);
    // The server recognised the retry; the shopper sees one saved observation.
    expect(screen.getByText(S.communityObservations.reportSaved)).toBeTruthy();
  });

  it('mints a new key when the shopper deliberately changes the draft', async () => {
    mockSubmit.mockResolvedValue({ id: 'report-1', created: true });
    await openSheet();
    await compose('seal_broken');
    await act(async () => { fireEvent.press(screen.getByLabelText(S.communityObservations.submit)); });

    // A different observation is a different thing to say, not a retry.
    fireEvent.press(screen.getByLabelText(S.communityObservations.observation.pack_leaking));
    await act(async () => { fireEvent.press(screen.getByLabelText(S.communityObservations.submit)); });
    expect(mockSubmit.mock.calls[1][0].client_report_id)
      .not.toBe(mockSubmit.mock.calls[0][0].client_report_id);

    // So is replacing the photograph.
    mockUploadMedia.mockResolvedValue({ id: 'asset-2' });
    await act(async () => { fireEvent.press(screen.getByLabelText(S.communityObservations.photoAction)); });
    await act(async () => { fireEvent.press(screen.getByLabelText(S.communityObservations.submit)); });
    expect(mockSubmit.mock.calls[2][0].client_report_id)
      .not.toBe(mockSubmit.mock.calls[1][0].client_report_id);
    expect(mockSubmit.mock.calls[2][0].photo_asset_id).toBe('asset-2');
  });
});

describe('managing what you already sent', () => {
  it('recovers the shopper′s own report after the sheet is closed and reopened', async () => {
    mockSubmit.mockResolvedValue({ id: 'report-1', created: true });
    await openSheet();
    await compose();
    mockOwnReports.mockResolvedValue([ownRow()]);
    await act(async () => { fireEvent.press(screen.getByLabelText(S.communityObservations.submit)); });

    fireEvent.press(screen.getByLabelText(S.communityObservations.cancel));
    fireEvent.press(screen.getByLabelText(S.communityObservations.reportAction));
    await waitFor(() => expect(screen.getByText(S.communityObservations.yourObservations)).toBeTruthy());
    // Recovered from the server, not from state that died with the modal.
    expect(mockOwnReports).toHaveBeenCalledWith('8901058000191');

    const label = `${S.communityObservations.withdraw}: ${S.communityObservations.observation.seal_broken}`;
    mockOwnReports.mockResolvedValue([ownRow({ status: 'withdrawn', withdrawn_at: '2026-09-02T00:00:00+00:00' })]);
    await act(async () => { fireEvent.press(screen.getByLabelText(label)); });
    expect(mockWithdraw).toHaveBeenCalledWith('report-1');

    fireEvent.press(screen.getByLabelText(S.communityObservations.cancel));
    fireEvent.press(screen.getByLabelText(S.communityObservations.reportAction));
    await waitFor(() => expect(screen.getByText(S.communityObservations.chooseObservation)).toBeTruthy());
    // A retraction is final, so it is no longer offered as a target.
    expect(screen.queryByLabelText(label)).toBeNull();
  });

  it('withdraws one of several own reports without touching the others', async () => {
    mockOwnReports.mockResolvedValue([
      ownRow({ id: 'r1', observation_code: 'seal_broken' }),
      ownRow({ id: 'r2', observation_code: 'pack_leaking' }),
      ownRow({
        id: 'r3', observation_code: 'ingredients_list_differs_from_app',
        status: 'withdrawn', withdrawn_at: '2026-09-01T00:00:00+00:00',
      }),
    ]);
    await openSheet();
    await waitFor(() => expect(screen.getByText(S.communityObservations.yourObservations)).toBeTruthy());

    const labelFor = (code: string) =>
      `${S.communityObservations.withdraw}: ${S.communityObservations.observation[code]}`;
    expect(screen.getByLabelText(labelFor('seal_broken'))).toBeTruthy();
    expect(screen.getByLabelText(labelFor('pack_leaking'))).toBeTruthy();
    // Already withdrawn, so no action at all.
    expect(screen.queryByLabelText(labelFor('ingredients_list_differs_from_app'))).toBeNull();

    mockOwnReports.mockResolvedValue([
      ownRow({ id: 'r1', status: 'withdrawn', withdrawn_at: '2026-09-02T00:00:00+00:00' }),
      ownRow({ id: 'r2', observation_code: 'pack_leaking' }),
    ]);
    await act(async () => { fireEvent.press(screen.getByLabelText(labelFor('seal_broken'))); });
    expect(mockWithdraw).toHaveBeenCalledWith('r1');
    expect(mockWithdraw).toHaveBeenCalledTimes(1);
    // The other one is untouched and still retractable.
    expect(screen.getByLabelText(labelFor('pack_leaking'))).toBeTruthy();
  });
});

describe('the right of reply fails closed on the screen too', () => {
  const withReply = (over: Record<string, unknown>) => ({ ...publicSignals, ...over });

  it.each([
    ['display switched off', withReply({ public_enabled: false })],
    ['no reply address', withReply({ brand_reply_url: null })],
    ['an empty reply address', withReply({ brand_reply_url: '' })],
    ['a plain http address', withReply({ brand_reply_url: 'http://example.org/reply' })],
    ['a malformed address', withReply({ brand_reply_url: 'not a url' })],
  ])('renders no card with %s, and still offers reporting', async (_label, envelope) => {
    // Publishing the claim and merely dropping the link is the failure mode.
    await renderScreen(envelope);
    expect(screen.queryByText(S.communityObservations.heading)).toBeNull();
    expect(screen.queryByText(/3 shoppers reported/)).toBeNull();
    expect(screen.queryByLabelText(S.communityObservations.brandRightOfReply)).toBeNull();
    // Collection is separate from publication.
    expect(screen.getByLabelText(S.communityObservations.reportAction)).toBeTruthy();
  });

  it('renders the card with its reply link on a valid https address', async () => {
    await renderScreen(publicSignals);
    expect(screen.getByText(S.communityObservations.heading)).toBeTruthy();
    expect(screen.getByLabelText(S.communityObservations.brandRightOfReply)).toBeTruthy();
    expect(screen.getByLabelText(S.communityObservations.reportAction)).toBeTruthy();
  });
});
