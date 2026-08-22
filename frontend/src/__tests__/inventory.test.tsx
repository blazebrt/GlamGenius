import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import { CATEGORY_META, CategoryTile, DraftReviewActions, EstimateNotice, GUIDED_TASKS, GuidedSprint, InventoryItemCard, InventoryRecovery, completedGuidedTasks } from '../components/inventory/InventoryPieces';
import InventoryScreen from '../../app/(tabs)/inventory';
import * as apiV2 from '../services/apiV2';
import { INVENTORY_CATEGORIES, InventoryItem, InventorySummary } from '../services/apiV2';

jest.mock('expo-router', () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock('react-native-safe-area-context', () => ({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }) }));

const item: InventoryItem = {
  id: 'item-1', category: 'wardrobe', subcategory: 'kurta', display_name: 'Teal work kurta', brand: 'Local maker', source: 'photo_extracted',
  verification_state: 'draft', confidence: .74, status: 'active', purchase_date: null, purchase_price: null, currency: 'INR', usage_count: 0,
  last_used_at: null, condition: 'good', replacement_priority: 'none', version: 1, details: { colour: 'teal' }, effective_expiry: null,
  low_use: false, image_ids: [], attributes: [], created_at: null, updated_at: null,
};
const summary: InventorySummary = {
  total_items: 0, categories: { wardrobe: 0, shoes: 0, accessories: 0, beauty: 0, hair: 0, perfumes: 0, supplements: 0 },
  low_use_products: 0, products_expiring_soon: 0, products_needing_attention: 0, duplicate_candidates: 0, at_risk_value: 0, currency: 'INR',
  inventory_balance: { metric_version: 'v1', visible_inputs: {}, explanation: 'Counts only' }, purchase_efficiency: { metric_version: 'v1', items_used: 0, items_with_price: 0, explanation: 'Inputs only' },
};

describe('complete inventory UI', () => {
  it('supports all seven named groups', () => {
    expect(INVENTORY_CATEGORIES).toHaveLength(7);
    expect(INVENTORY_CATEGORIES.map((key) => CATEGORY_META[key].label)).toEqual(['Wardrobe', 'Shoes', 'Accessories', 'Skin Care', 'Hair Care', 'Perfumes', 'Supplements']);
  });

  it('category controls expose labels, counts and selection actions', () => {
    const press = jest.fn(); render(<CategoryTile category="beauty" count={4} onPress={press} />);
    fireEvent.press(screen.getByLabelText('Skin Care, 4 items')); expect(press).toHaveBeenCalled();
  });

  it('uses customer category labels while preserving the internal Skin Care API key', async () => {
    jest.spyOn(apiV2, 'getInventorySummary').mockResolvedValue(summary);
    const getItems = jest.spyOn(apiV2, 'getInventoryItems').mockResolvedValue({ items: [], pagination: { total: 0, page: 1, page_size: 20, pages: 0 } });
    render(<InventoryScreen />);
    await waitFor(() => expect(screen.getByLabelText('Skin Care, 0 items')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Skin Care, 0 items'));
    await waitFor(() => expect(screen.getByText('In Skin Care')).toBeTruthy());
    expect(screen.queryByText('In beauty')).toBeNull();
    expect(screen.getByLabelText('Hair Care, 0 items')).toBeTruthy();
    expect(getItems).toHaveBeenLastCalledWith(expect.objectContaining({ category: 'beauty' }));
  });

  it('guided sprint gives value without forcing a full catalogue', () => {
    const add = jest.fn(); render(<GuidedSprint summary={summary} onAdd={add} />);
    expect(screen.getByText(/catalogue the rest whenever/i)).toBeTruthy();
    fireEvent.press(screen.getByLabelText(GUIDED_TASKS[0].label)); expect(add).toHaveBeenCalledWith('wardrobe');
    expect(completedGuidedTasks({ wardrobe: 7, shoes: 2, beauty: 1, hair: 1 })).toBe(5);
  });

  it('draft extraction has accessible correction and confirmation controls', () => {
    const confirm = jest.fn(); const edit = jest.fn(); render(<DraftReviewActions item={item} onConfirm={confirm} onEdit={edit} />);
    expect(screen.getByText(/nothing becomes verified/i)).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Correct Teal work kurta')); fireEvent.press(screen.getByLabelText('Confirm Teal work kurta'));
    expect(edit).toHaveBeenCalled(); expect(confirm).toHaveBeenCalled();
  });

  it('item card clearly marks unverified drafts', () => {
    const open = jest.fn(); render(<InventoryItemCard item={item} onPress={open} />);
    expect(screen.getByText('Review')).toBeTruthy(); fireEvent.press(screen.getByLabelText('Open Teal work kurta')); expect(open).toHaveBeenCalled();
  });

  it('value language is transparent and never judgmental', () => {
    const explanation = 'This is an estimate based on visible inputs. It is not exact.'; render(<EstimateNotice explanation={explanation} />);
    expect(screen.getByText(explanation)).toBeTruthy(); expect(screen.queryByText(/money wasted/i)).toBeNull();
  });

  it('error recovery has accessible retry and add actions', () => {
    const retry = jest.fn(); const add = jest.fn(); render(<InventoryRecovery onRetry={retry} onAdd={add} />);
    fireEvent.press(screen.getByLabelText('Retry inventory')); fireEvent.press(screen.getByLabelText('Add an item instead'));
    expect(retry).toHaveBeenCalled(); expect(add).toHaveBeenCalled();
  });
});
