/**
 * Server-driven capability, loaded once at startup.
 *
 * The safe default while loading, and after a failure, is "nothing is
 * available". Screens read from the getters below rather than assuming.
 *
 * Payment concepts are intentionally absent from this store (§4 hardening
 * spec): the backend no longer publishes a ``billing`` block, and screens
 * that used to render an upgrade path have been removed.
 */
import { create } from 'zustand';
import { AppConfig, getConfig } from '../services/apiV2';

interface ConfigStore {
  config: AppConfig | null;
  loading: boolean;
  loaded: boolean;
  error: string | null;
  load: () => Promise<void>;

  analysisAvailable: () => boolean;
  consentRequired: () => boolean;
  featureEnabled: (key: string) => boolean;
  inviteRequired: () => boolean;
  betaMessage: () => string;
}

const BETA_FALLBACK =
  'GlamGenius is a private beta. Your invite gives you full access.';

export const useConfigStore = create<ConfigStore>((set, get) => ({
  config: null,
  loading: false,
  loaded: false,
  error: null,

  load: async () => {
    set({ loading: true, error: null });
    try {
      const config = await getConfig();
      set({ config, loaded: true });
    } catch (err: any) {
      set({
        error: err?.message ?? 'Could not load app configuration',
        loaded: true,
      });
    } finally {
      set({ loading: false });
    }
  },

  analysisAvailable: () => get().config?.analysis.provider_configured === true,
  consentRequired: () => get().config?.analysis.consent_required === true,
  featureEnabled: (key: string) => get().config?.features?.[key] === true,
  inviteRequired: () => get().config?.access?.invite_required !== false,
  betaMessage: () => get().config?.access?.beta_message ?? BETA_FALLBACK,
}));
