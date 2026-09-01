import React from 'react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import * as apiV2 from '../services/apiV2';
import { DuplicateCandidate, InventoryItem, InventorySummary } from '../services/apiV2';
import InventoryScreen from '../../app/(tabs)/inventory';
import InventoryAddScreen from '../../app/inventory-add';
import InventoryInsightsScreen from '../../app/inventory-insights';
import InventoryItemScreen from '../../app/inventory-item';

let mockRouteParams: Record<string, string> = {};
const mockRouter = { push: jest.fn(), replace: jest.fn(), back: jest.fn(), canGoBack: () => false };

function mockUseFocusEffect(callback: () => void | (() => void)) {
  // The test double intentionally delegates to React's lifecycle hook.
  // eslint-disable-next-line react-hooks/rules-of-hooks
  React.useEffect(() => callback(), [callback]);
}

jest.mock('expo-router', () => {
  return {
    useLocalSearchParams: () => mockRouteParams,
    useRouter: () => mockRouter,
    useFocusEffect: mockUseFocusEffect,
    Redirect: ({ href }: { href: string }) => <>{href}</>,
  };
});
jest.mock('react-native-safe-area-context', () => ({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }) }));
jest.mock('expo-image-picker', () => ({ requestCameraPermissionsAsync: jest.fn(), launchCameraAsync: jest.fn(), launchImageLibraryAsync: jest.fn() }));

const summary = (categories: Partial<Record<keyof InventorySummary['categories'], number>>): InventorySummary => ({
  total_items: Object.values(categories).reduce((sum, value) => sum + (value || 0), 0),
  categories: { wardrobe: 0, shoes: 0, accessories: 0, beauty: 0, hair: 0, perfumes: 0, supplements: 0, ...categories },
  low_use_products: 0, products_expiring_soon: 0, products_needing_attention: 0, duplicate_candidates: 0, at_risk_value: 0, currency: 'INR',
  inventory_balance: { metric_version: 'v1', visible_inputs: {}, explanation: 'Counts only' },
  purchase_efficiency: { metric_version: 'v1', items_used: 0, items_with_price: 0, explanation: 'Inputs only' },
});

const item = (category: InventoryItem['category'], display_name: string): InventoryItem => ({
  id: `${category}-${display_name}`, category, subcategory: null, display_name, brand: 'Test', source: 'user_declared', verification_state: 'confirmed',
  confidence: 1, status: 'active', purchase_date: null, purchase_price: null, currency: 'INR', usage_count: 0, last_used_at: null,
  condition: 'good', replacement_priority: 'none', version: 1, details: {}, effective_expiry: null, low_use: true, image_ids: [],
  attributes: [], created_at: null, updated_at: null,
});

const listing = (items: InventoryItem[]) => ({ items, pagination: { total: items.length, page: 1, page_size: 20, pages: 1 } });

describe('VC-08 final IA domain boundaries', () => {
  beforeEach(() => {
    cleanup(); mockRouteParams = {}; jest.clearAllMocks();
    jest.spyOn(apiV2, 'getInventorySummary').mockResolvedValue(summary({}));
    jest.spyOn(apiV2, 'getInventoryItems').mockResolvedValue(listing([]));
    jest.spyOn(apiV2, 'getLowUseInventory').mockResolvedValue([]);
    jest.spyOn(apiV2, 'getExpiringInventory').mockResolvedValue([]);
    jest.spyOn(apiV2, 'getInventoryDuplicates').mockResolvedValue([]);
    jest.spyOn(apiV2, 'getValueToRecover').mockResolvedValue({ label: 'Value to Recover', estimated_total: 0, currency: 'INR', is_estimate: true, metric_version: 'v1', explanation: 'No items', items: [] });
  });
  afterEach(() => { cleanup(); jest.restoreAllMocks(); });

  it('keeps the retired Style tab as a deterministic Scan redirect', () => {
    const source = readFileSync(join(process.cwd(), 'app', '(tabs)', 'style.tsx'), 'utf8');
    expect(source).toContain('<Redirect href="/scan-product" />');
  });

  it('quarantines Style inventory-add traffic', async () => {
    mockRouteParams = { domain: 'style', category: 'wardrobe' };
    render(<InventoryAddScreen />);
    expect(screen.toJSON()).toBe('/scan-product');
  });

  it('limits Care add flow to Care categories', () => {
    mockRouteParams = { domain: 'care', category: 'beauty' };
    render(<InventoryAddScreen />);
    expect(screen.getByLabelText('Skin Care')).toBeTruthy(); expect(screen.getByLabelText('Hair Care')).toBeTruthy(); expect(screen.getByLabelText('Perfumes')).toBeTruthy(); expect(screen.getByLabelText('Supplements')).toBeTruthy();
    expect(screen.queryByLabelText('Wardrobe')).toBeNull(); expect(screen.queryByLabelText('Shoes')).toBeNull(); expect(screen.queryByLabelText('Accessories')).toBeNull();
  });

  it('keeps Care add traffic with a missing category inside the safe Care default', () => {
    mockRouteParams = { domain: 'care' };
    render(<InventoryAddScreen />);
    expect(screen.getByLabelText('Skin Care').props.accessibilityState.selected).toBe(true);
    expect(screen.queryByLabelText('Wardrobe')).toBeNull(); expect(screen.queryByLabelText('Shoes')).toBeNull(); expect(screen.queryByLabelText('Accessories')).toBeNull();
  });

  it('keeps Care add traffic with an invalid category inside the safe Care default', () => {
    mockRouteParams = { domain: 'care', category: 'wardrobe' };
    render(<InventoryAddScreen />);
    expect(screen.getByLabelText('Skin Care').props.accessibilityState.selected).toBe(true);
    expect(screen.queryByLabelText('Wardrobe')).toBeNull(); expect(screen.queryByLabelText('Shoes')).toBeNull(); expect(screen.queryByLabelText('Accessories')).toBeNull();
  });

  it('quarantines inventory-add without a Care domain', () => {
    mockRouteParams = {};
    render(<InventoryAddScreen />);
    expect(screen.toJSON()).toBe('/scan-product');
  });

  it('quarantines Style inventory and its insight entry points', async () => {
    mockRouteParams = { domain: 'style' };
    jest.spyOn(apiV2, 'getInventorySummary').mockResolvedValue(summary({ wardrobe: 1, beauty: 1 }));
    jest.spyOn(apiV2, 'getInventoryItems').mockResolvedValue(listing([item('wardrobe', 'Kurta'), item('beauty', 'Serum')]));
    render(<InventoryScreen />);
    expect(screen.toJSON()).toBe('/scan-product');
  });

  it('quarantines inventory without an explicit Care domain', () => {
    mockRouteParams = {};
    render(<InventoryScreen />);
    expect(screen.toJSON()).toBe('/scan-product');
  });

  it('quarantines insights without an explicit Care domain', () => {
    mockRouteParams = {};
    render(<InventoryInsightsScreen />);
    expect(screen.toJSON()).toBe('/scan-product');
  });

  it('filters Care low-use rows to the requested domain', async () => {
    mockRouteParams = { domain: 'care', view: 'low-use' };
    jest.spyOn(apiV2, 'getLowUseInventory').mockResolvedValue([item('beauty', 'Serum'), item('wardrobe', 'Kurta')]);
    render(<InventoryInsightsScreen />);
    await waitFor(() => expect(screen.getByText('Serum')).toBeTruthy());
    expect(screen.queryByText('Kurta')).toBeNull();
  });

  it('returns a failed retained Care item load to the Care collection', async () => {
    mockRouteParams = { id: 'missing-care-item' };
    jest.spyOn(apiV2, 'getInventoryItem').mockRejectedValue(new Error('not found'));
    render(<InventoryItemScreen />);
    await waitFor(() => expect(screen.getByLabelText('Add an item instead')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Add an item instead'));
    expect(mockRouter.replace).toHaveBeenCalledWith({ pathname: '/(tabs)/inventory', params: { domain: 'care' } });
  });

  const duplicate = (id: string, item_a: InventoryItem, item_b: InventoryItem): DuplicateCandidate => ({
    id, confidence: .92, reason: 'similar name', status: 'open', item_a, item_b,
  });

  it('quarantines Style inventory insights', async () => {
    mockRouteParams = { domain: 'style', view: 'duplicates' };
    const stylePair = duplicate('style-pair', item('wardrobe', 'Jacket'), item('shoes', 'Boots'));
    const carePair = duplicate('care-pair', item('beauty', 'Serum'), item('hair', 'Shampoo'));
    const mixedPair = duplicate('mixed-pair', item('accessories', 'Watch'), item('beauty', 'Moisturiser'));
    jest.spyOn(apiV2, 'getInventoryDuplicates').mockResolvedValue([stylePair, carePair, mixedPair]);
    render(<InventoryInsightsScreen />);
    expect(screen.toJSON()).toBe('/scan-product');
  });

  it('shows only all-Care duplicate candidates and their actions on Care', async () => {
    mockRouteParams = { domain: 'care', view: 'duplicates' };
    const stylePair = duplicate('style-pair', item('wardrobe', 'Jacket'), item('shoes', 'Boots'));
    const carePair = duplicate('care-pair', item('beauty', 'Serum'), item('hair', 'Shampoo'));
    const mixedPair = duplicate('mixed-pair', item('accessories', 'Watch'), item('beauty', 'Moisturiser'));
    jest.spyOn(apiV2, 'getInventoryDuplicates').mockResolvedValue([stylePair, carePair, mixedPair]);
    render(<InventoryInsightsScreen />);
    await waitFor(() => expect(screen.getByText('Serum · Shampoo')).toBeTruthy());
    expect(screen.getByLabelText('Keep both Serum and Shampoo')).toBeTruthy();
    expect(screen.queryByText('Jacket · Boots')).toBeNull();
    expect(screen.queryByText('Watch · Moisturiser')).toBeNull();
  });
});
