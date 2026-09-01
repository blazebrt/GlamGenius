import type { InventoryCategory } from '../services/apiV2';

/** The customer-facing IA contract. Internal category keys remain unchanged. */
export const PRIMARY_TABS = ['scan', 'you'] as const;
export const PRIMARY_TAB_LABELS = ['Scan', 'You'] as const;

export const STYLE_CATEGORIES: readonly InventoryCategory[] = ['wardrobe', 'shoes', 'accessories'];
export const CARE_CATEGORIES: readonly InventoryCategory[] = ['beauty', 'hair', 'perfumes', 'supplements'];

export type InventoryDomain = 'style' | 'care';

export const categoriesForDomain = (domain?: string): readonly InventoryCategory[] | undefined => {
  if (domain === 'style') return STYLE_CATEGORIES;
  if (domain === 'care') return CARE_CATEGORIES;
  return undefined;
};

export const countForDomain = (
  categories: Partial<Record<InventoryCategory, number>> | undefined,
  domain: InventoryDomain,
): number => (categoriesForDomain(domain) || []).reduce((total, category) => total + (categories?.[category] || 0), 0);

export const LEGACY_HIDDEN_TAB_ROUTES = [
  'home', 'inventory', 'planner', 'profile', 'services', 'scan-tab', 'history', 'today', 'style', 'care', 'plan',
] as const;
