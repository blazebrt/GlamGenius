/**
 * Server-driven capability, loaded once at startup.
 *
 * The audit found the app selling ₹249/month subscriptions the backend refuses
 * on principle, because nothing ever asked the backend. Screens now read
 * `billingAvailable` from here instead of assuming.
 *
 * The safe default while loading, and after a failure, is "nothing is
 * available". A network blip must never be the reason a payment button appears.
 */
import { create } from 'zustand';
import { AppConfig, getConfig } from '../services/apiV2';

interface ConfigStore {
  config: AppConfig | null;
  loading: boolean;
  loaded: boolean;
  error: string | null;
  load: () => Promise<void>;

  billingAvailable: () => boolean;
  analysisAvailable: () => boolean;
  consentRequired: () => boolean;
  featureEnabled: (key: string) => boolean;
  betaMessage: () => string;
}

const BETA_FALLBACK =
  'GlamGenius is in a private beta. Your invite gives you full access — there is nothing to pay for yet.';

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
      // Leave config null. Every getter below then returns the closed-down
      // answer, which is the safe direction to fail in.
      set({
        error: err?.message ?? 'Could not load app configuration',
        loaded: true,
      });
    } finally {
      set({ loading: false });
    }
  },

  billingAvailable: () => get().config?.billing.subscriptions_available === true,
  analysisAvailable: () => get().config?.analysis.provider_configured === true,
  consentRequired: () => get().config?.analysis.consent_required === true,
  featureEnabled: (key: string) => get().config?.features?.[key] === true,
  betaMessage: () => get().config?.billing.beta_message ?? BETA_FALLBACK,
}));
