/**
 * The config store decides whether payment UI is allowed to exist. Its most
 * important property is how it behaves when it does *not* know.
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
  billing: {
    subscriptions_available: false,
    invite_only: true,
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

it('reports billing unavailable before anything has loaded', () => {
  expect(useConfigStore.getState().billingAvailable()).toBe(false);
});

it('fails closed when the config request fails', async () => {
  mockedGetConfig.mockRejectedValue(new Error('offline'));

  await useConfigStore.getState().load();

  const state = useConfigStore.getState();
  expect(state.loaded).toBe(true);
  expect(state.error).toBeTruthy();
  // The important assertion: a network failure must not surface a paywall.
  expect(state.billingAvailable()).toBe(false);
  expect(state.analysisAvailable()).toBe(false);
  expect(state.featureEnabled('v2_media')).toBe(false);
});

it('still has something to say when it has no server message', () => {
  expect(useConfigStore.getState().betaMessage()).toContain('private beta');
});

it('reflects the server answer once loaded', async () => {
  mockedGetConfig.mockResolvedValue(config());

  await useConfigStore.getState().load();

  const state = useConfigStore.getState();
  expect(state.billingAvailable()).toBe(false);
  expect(state.analysisAvailable()).toBe(true);
  expect(state.featureEnabled('v2_media')).toBe(true);
  expect(state.featureEnabled('v2_privacy')).toBe(false);
  expect(state.betaMessage()).toBe('Private beta.');
});

it('allows billing only when the server turns it on', async () => {
  mockedGetConfig.mockResolvedValue(
    config({
      billing: {
        subscriptions_available: true,
        invite_only: false,
        beta_message: '',
      },
    })
  );

  await useConfigStore.getState().load();

  expect(useConfigStore.getState().billingAvailable()).toBe(true);
});
