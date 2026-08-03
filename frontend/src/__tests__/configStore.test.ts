/**
 * The config store no longer models billing (§4 hardening spec). It reads
 * ``access.beta_message``, ``analysis.provider_configured`` and the
 * ``features`` map. This test guards the safe-default behaviour: when the
 * store has not loaded, or loading fails, every getter returns the
 * closed-down answer.
 */
import { useConfigStore } from '../store/configStore';
import * as apiV2 from '../services/apiV2';

jest.mock('../services/apiV2', () => ({
  ...jest.requireActual('../services/apiV2'),
  getConfig: jest.fn(),
}));

const mockedGetConfig = apiV2.getConfig as jest.MockedFunction<typeof apiV2.getConfig>;

const config = (overrides: any = {}): any => ({
  api_version: 'v2',
  supabase: { url: 'https://example.supabase.co', anon_key: 'k', configured: true },
  access: {
    invite_required: true,
    beta_message: 'Private beta.',
  },
  analysis: {
    provider_configured: true,
    consent_required: false,
    consent_version: '2026-08-01',
  },
  media: {
    max_bytes: 8388608,
    allowed_types: ['image/jpeg'],
    face_photos_stored: false,
    storage_note: 'note',
  },
  features: { v2_media: true, v2_privacy: false },
  ...overrides,
});

beforeEach(() => {
  useConfigStore.setState({ config: null, loading: false, loaded: false, error: null });
  mockedGetConfig.mockReset();
});

it('reports analysis unavailable before anything has loaded', () => {
  expect(useConfigStore.getState().analysisAvailable()).toBe(false);
});

it('fails closed when the config request fails', async () => {
  mockedGetConfig.mockRejectedValue(new Error('offline'));

  await useConfigStore.getState().load();

  const state = useConfigStore.getState();
  expect(state.loaded).toBe(true);
  expect(state.error).toBeTruthy();
  expect(state.analysisAvailable()).toBe(false);
  expect(state.featureEnabled('v2_media')).toBe(false);
  expect(state.inviteRequired()).toBe(true); // fail-closed: assume invite is needed
});

it('still has something to say when it has no server message', () => {
  expect(useConfigStore.getState().betaMessage().toLowerCase()).toContain('beta');
});

it('reflects the server answer once loaded', async () => {
  mockedGetConfig.mockResolvedValue(config());

  await useConfigStore.getState().load();

  const state = useConfigStore.getState();
  expect(state.analysisAvailable()).toBe(true);
  expect(state.featureEnabled('v2_media')).toBe(true);
  expect(state.featureEnabled('v2_privacy')).toBe(false);
  expect(state.betaMessage()).toBe('Private beta.');
  expect(state.inviteRequired()).toBe(true);
});
