import type { InventoryCategory } from '../services/apiV2';

/** The customer-facing IA contract. Internal category keys remain unchanged. */
export const PRIMARY_TABS = ['today', 'style', 'care', 'plan', 'you'] as const;
export const PRIMARY_TAB_LABELS = ['Today', 'Style', 'Care', 'Plan', 'You'] as const;

export const STYLE_CATEGORIES: readonly InventoryCategory[] = ['wardrobe', 'shoes', 'accessories'];
export const CARE_CATEGORIES: readonly InventoryCategory[] = ['beauty', 'hair', 'perfumes', 'supplements'];

export type InventoryDomain = 'style' | 'care';

export const categoriesForDomain = (domain?: string): readonly InventoryCategory[] | undefined => {
  if (domain === 'style') return STYLE_CATEGORIES;
  if (domain === 'care') return CARE_CATEGORIES;
  return undefined;
};

export const LEGACY_HIDDEN_TAB_ROUTES = [
  'home', 'inventory', 'style-me-tab', 'planner', 'profile', 'services', 'scan-tab', 'history',
] as const;
