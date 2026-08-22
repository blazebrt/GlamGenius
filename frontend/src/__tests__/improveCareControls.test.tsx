import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import ImproveScreen from '../../app/improve';
import * as apiV2 from '../services/apiV2';

jest.mock('expo-router', () => ({
  useRouter: () => ({ back: jest.fn(), push: jest.fn() }),
  useFocusEffect: (callback: () => void) => jest.requireActual<typeof React>('react').useEffect(callback, []),
}));

const overview = {
  has_shelf: true, has_routines: true,
  routines: [{ id: 'routine-1', kind: 'morning', label: 'Morning routine', frequency: 'Every morning', steps: [{ id: 'step-1', slot: 'cleanser', label: 'Cleanser', order: 10, required: true, optional: false, why: 'A clean start.', frequency: 'Every morning', inventory_item_id: 'item-1', product_name: 'Gentle Cleanser', owned: true, safety_note: '', alternative: '', climate_note: '', is_gap: false }], warnings: [], climate_notes: [], skipped_for_allergy: [], disclaimer: 'Built from your confirmed products.' }],
  consistency: { days_considered: 14, days_with_activity: 2, steps_completed: 2, note: 'A steady start.' }, needs_attention: [],
  expiring: { window_days: 60, expired: [], expiring_soon: [], no_date_recorded: [], note: 'Dates are yours to record.' },
  low_use: { products: [], count: 0, definition: 'Low use', note: 'Nothing to flag.' }, missing_categories: [],
  counts: { products: 1, avoid: 0, caution: 0, needs_attention: 0, awaiting_confirmation: 0, drafts: 0 },
  routine_effort: { resolved: 'detailed', source: 'user_declared', can_simplify: true, next_simpler: 'balanced' },
  care_product_controls: [
    { inventory_item_id: 'item-1', display_name: 'Gentle Cleanser', category: 'skin_care', slot: 'cleanser', paused: false, preferred: false, eligible: true },
    { inventory_item_id: 'cream-cleanser-id', display_name: 'Cream Cleanser', category: 'skin_care', slot: 'cleanser', paused: false, preferred: false, eligible: true },
  ],
  disclaimer: 'Care is built from what you confirmed.',
};

const canonicalAlternative = (overrides: Partial<typeof overview> = {}) => ({
  ...overview,
  ...overrides,
});

describe('Improve Care controls', () => {
  beforeEach(() => jest.clearAllMocks());
  afterEach(() => jest.restoreAllMocks());

  it('keeps the routine visible while simplifying and prevents duplicate submits, then reloads', async () => {
    const getOverview = jest.spyOn(apiV2, 'getImproveOverview').mockResolvedValue(overview as any);
    let resolve!: (value: any) => void;
    jest.spyOn(apiV2, 'simplifyCareRoutine').mockReturnValue(new Promise((done) => { resolve = done; }));
    render(<ImproveScreen />);
    await waitFor(() => expect(screen.getByLabelText('Simplify my routine')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Simplify my routine')); fireEvent.press(screen.getByLabelText('Simplify my routine'));
    expect(apiV2.simplifyCareRoutine).toHaveBeenCalledTimes(1); expect(screen.getByText('Gentle Cleanser')).toBeTruthy();
    resolve({ changed: true, status: 'applied', message: 'Simplified.' });
    await waitFor(() => expect(getOverview).toHaveBeenCalledTimes(2));
  });

  it('leaves the canonical routine unchanged on failure and offers retry', async () => {
    jest.spyOn(apiV2, 'getImproveOverview').mockResolvedValue(overview as any);
    const simplify = jest.spyOn(apiV2, 'simplifyCareRoutine').mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ changed: true, status: 'applied', message: 'Simplified.' });
    render(<ImproveScreen />);
    await waitFor(() => expect(screen.getByLabelText('Simplify my routine')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Simplify my routine'));
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
    expect(screen.getByText('Gentle Cleanser')).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Try again'));
    await waitFor(() => expect(simplify).toHaveBeenCalledTimes(2));
  });

  it('reaches an eligible same-slot alternative and follows the canonical preference refresh', async () => {
    const getOverview = jest.spyOn(apiV2, 'getImproveOverview')
      .mockResolvedValueOnce(overview as any)
      .mockResolvedValueOnce(canonicalAlternative({
        routines: [{ ...overview.routines[0], steps: [{ ...overview.routines[0].steps[0], inventory_item_id: 'cream-cleanser-id', product_name: 'Cream Cleanser' }] }],
        care_product_controls: overview.care_product_controls.map((product) => product.inventory_item_id === 'cream-cleanser-id' ? { ...product, preferred: true } : product),
      }) as any)
      .mockResolvedValueOnce(canonicalAlternative({
        routines: [{ ...overview.routines[0], steps: [{ ...overview.routines[0].steps[0], inventory_item_id: 'cream-cleanser-id', product_name: 'Cream Cleanser' }] }],
      }) as any);
    const prefer = jest.spyOn(apiV2, 'preferCareProduct').mockResolvedValue({ changed: true, status: 'preferred', message: 'Preferred.' });
    const unprefer = jest.spyOn(apiV2, 'unpreferCareProduct').mockResolvedValue({ changed: true, status: 'standard', message: 'Standard.' });
    render(<ImproveScreen />);
    await waitFor(() => expect(screen.getByLabelText('Routine choices for Gentle Cleanser')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Routine choices for Gentle Cleanser'));
    expect(screen.getByText('Gentle Cleanser')).toBeTruthy();
    expect(screen.getByText('Cream Cleanser')).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Prefer Cream Cleanser'));
    expect(prefer).toHaveBeenCalledTimes(1);
    expect(prefer).toHaveBeenCalledWith('cream-cleanser-id');
    await waitFor(() => expect(getOverview).toHaveBeenCalledTimes(2));
    expect(screen.getByText('Cream Cleanser')).toBeTruthy();
    expect(screen.getByLabelText('Unprefer Cream Cleanser')).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Unprefer Cream Cleanser'));
    expect(unprefer).toHaveBeenCalledTimes(1);
    expect(unprefer).toHaveBeenCalledWith('cream-cleanser-id');
    await waitFor(() => expect(getOverview).toHaveBeenCalledTimes(3));
  });

  it('pauses and resumes from canonical state, retries the same failed preference, and blocks double submit', async () => {
    const preferred = canonicalAlternative({
      routines: [{ ...overview.routines[0], steps: [{ ...overview.routines[0].steps[0], inventory_item_id: 'cream-cleanser-id', product_name: 'Cream Cleanser' }] }],
      care_product_controls: overview.care_product_controls.map((product) => product.inventory_item_id === 'cream-cleanser-id' ? { ...product, preferred: true } : product),
    });
    const paused = canonicalAlternative({
      routines: [{ ...overview.routines[0], steps: [{ ...overview.routines[0].steps[0], inventory_item_id: 'item-1', product_name: 'Gentle Cleanser' }] }],
      care_product_controls: overview.care_product_controls.map((product) => product.inventory_item_id === 'cream-cleanser-id' ? { ...product, paused: true, preferred: true } : product),
    });
    const getOverview = jest.spyOn(apiV2, 'getImproveOverview')
      .mockResolvedValueOnce(overview as any)
      .mockResolvedValueOnce(preferred as any)
      .mockResolvedValueOnce(paused as any)
      .mockResolvedValueOnce(paused as any);
    const prefer = jest.spyOn(apiV2, 'preferCareProduct').mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ changed: true, status: 'preferred', message: 'Preferred.' });
    const pause = jest.spyOn(apiV2, 'pauseCareProduct').mockResolvedValue({ changed: true, status: 'paused', message: 'Paused.' });
    const resume = jest.spyOn(apiV2, 'resumeCareProduct').mockResolvedValue({ changed: true, status: 'active', message: 'Active.' });
    render(<ImproveScreen />);
    await waitFor(() => expect(screen.getByLabelText('Routine choices for Gentle Cleanser')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Routine choices for Gentle Cleanser'));
    fireEvent.press(screen.getByLabelText('Prefer Cream Cleanser'));
    fireEvent.press(screen.getByLabelText('Prefer Cream Cleanser'));
    expect(prefer).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
    expect(screen.getByText('Gentle Cleanser')).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Try again'));
    await waitFor(() => expect(prefer).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(getOverview).toHaveBeenCalledTimes(2));
    fireEvent.press(screen.getByLabelText('Pause Cream Cleanser'));
    expect(pause).toHaveBeenCalledWith('cream-cleanser-id');
    await waitFor(() => expect(getOverview).toHaveBeenCalledTimes(3));
    expect(screen.getByLabelText('Paused Care products')).toBeTruthy();
    expect(screen.getByText('Products you paused')).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Resume Cream Cleanser'));
    expect(resume).toHaveBeenCalledWith('cream-cleanser-id');
    await waitFor(() => expect(getOverview).toHaveBeenCalledTimes(4));
  });
});
