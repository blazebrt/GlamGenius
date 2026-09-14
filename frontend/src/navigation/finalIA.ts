import type { InventoryCategory } from '../services/apiV2';

/** The customer-facing IA contract. Internal category keys remain unchanged. */
export const PRIMARY_TABS = ['scan', 'you'] as const;
export const PRIMARY_TAB_LABELS = ['Scan', 'You'] as const;

export const CARE_CATEGORIES: readonly InventoryCategory[] = ['beauty', 'hair', 'perfumes', 'supplements'];

/** The only customer product shelf is the governed body-product shelf. */
export const categoriesForDomain = (_domain?: string): readonly InventoryCategory[] => CARE_CATEGORIES;

export const countForDomain = (
  categories: Partial<Record<InventoryCategory, number>> | undefined,
): number => CARE_CATEGORIES.reduce((total, category) => total + (categories?.[category] || 0), 0);
