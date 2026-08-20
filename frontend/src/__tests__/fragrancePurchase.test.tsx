import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import ShoppingCheckScreen from '../../app/shopping-check';
import * as api from '../services/apiV2';

jest.mock('expo-image-picker', () => ({ launchImageLibraryAsync: jest.fn() }));
jest.mock('../services/apiV2', () => {
  const actual = jest.requireActual('../services/apiV2');
  return { ...actual, getPurchaseStrategies: jest.fn(), inspectPurchaseCandidate: jest.fn(), getFragrancePurchaseCheck: jest.fn(), recordPurchaseCandidateDecision: jest.fn(), evaluateItemDetails: jest.fn(), evaluateScreenshot: jest.fn() };
});

const mockedApi = api as jest.Mocked<typeof api>;
const strategyResponse = {
  purchase_strategy_registry_version: 'v3-05.9',
  fragrance_context_options: { occasions: [{ key: 'business_meeting', label: 'Business meeting' }], seasons: [{ key: 'summer', label: 'Summer' }] },
  strategies: [
    { key: 'style_purchase' as const, label: 'Style', state: 'active' as const, categories: [{ key: 'wardrobe' as const, label: 'Wardrobe' }] },
    { key: 'care_purchase' as const, label: 'Care', state: 'active' as const, categories: [{ key: 'beauty' as const, label: 'Skin Care' }] },
    { key: 'fragrance_purchase' as const, label: 'Fragrance', state: 'active' as const, categories: [{ key: 'perfumes' as const, label: 'Perfumes' }] },
    { key: 'supplement_purchase' as const, label: 'Supplements', state: 'prohibited' as const, categories: [{ key: 'supplements' as const, label: 'Supplements' }] },
  ],
};

const candidate = {
  candidate_truth_version: 'v3-05.1', fragrance_purchase_candidate_schema_version: 'v3-05.9' as const,
  candidate: { id: 'fragrance-1', source: 'manual', category: 'perfumes' as const, subcategory: null, display_name: 'Rain Garden', brand: 'House', details: { fragrance_family: 'woody', concentration: null, occasion: ['office'], season: ['summer'] }, price: 1200, currency: 'INR', product_url: null, media_asset_id: null, verification_state: 'user_declared', uncertain_fields: [], extraction_confidence: null, ai_run_id: null, model_version: null, prompt_version: null, schema_version: null, in_inventory: false as const }, review_required: false, facts_trusted: true, normalised_fragrance_family: 'woody', missing_information: [], note: 'Prospective candidate.',
};

const check = {
  fragrance_purchase_check_version: 'v3-05.9' as const, strategy: 'fragrance_purchase' as const, candidate_truth: candidate,
  collection_context: { owned_perfume_count: 0, draft_perfume_count: 0, normalised_candidate_family: 'woody', exact_owned: [], same_family_owned: [], intended_use: { occasion: ['office'], season: ['summer'] }, coverage: { covered: [], unknown: [], uncovered: [] }, owned_options_to_use_first: [] },
  verdict: { fragrance_purchase_verdict_version: 'v3-05.9' as const, verdict: 'buy' as const, headline: 'This fills a fragrance gap.', explanation: 'You do not have a fragrance recorded yet, so this fills a real category gap.', primary_reason_code: 'first_fragrance_gap', supporting_reason_codes: [], decision_fingerprint: 'fp-1', normalised_candidate_family: 'woody', same_family_owned: [], owned_options_to_use_first: [], missing_information: [] }, decision: null,
};

describe('Fragrance Purchase routing', () => {
  beforeEach(() => { jest.clearAllMocks(); mockedApi.getPurchaseStrategies.mockResolvedValue(strategyResponse); mockedApi.inspectPurchaseCandidate.mockResolvedValue(candidate); mockedApi.getFragrancePurchaseCheck.mockResolvedValue(check); });

  it('discovers Perfumes only from the active canonical strategy and keeps Supplements unavailable', async () => {
    render(<ShoppingCheckScreen />);
    await waitFor(() => expect(screen.getByLabelText('Perfumes')).toBeTruthy());
    expect(screen.queryByLabelText('Supplements')).toBeNull();
    expect(mockedApi.getPurchaseStrategies).toHaveBeenCalled();
  });

  it('uses server-provided canonical fragrance context options', async () => {
    render(<ShoppingCheckScreen />);
    await waitFor(() => expect(screen.getByLabelText('Perfumes')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Perfumes'));
    fireEvent.press(screen.getByLabelText('Enter the details myself'));
    expect(screen.getByLabelText('Occasion Business meeting')).toBeTruthy();
    expect(screen.getByLabelText('Season Summer')).toBeTruthy();
    expect(screen.getByPlaceholderText('Fragrance name')).toBeTruthy();
  });

  it('uses the Fragrance candidate/check path and never the Style evaluator', async () => {
    render(<ShoppingCheckScreen />);
    await waitFor(() => expect(screen.getByLabelText('Perfumes')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Perfumes'));
    fireEvent.press(screen.getByLabelText('Enter the details myself'));
    fireEvent.changeText(screen.getByLabelText('Product name'), 'Rain Garden');
    fireEvent.press(screen.getByLabelText('Check this item'));
    await waitFor(() => expect(mockedApi.getFragrancePurchaseCheck).toHaveBeenCalledWith('fragrance-1'));
    expect(mockedApi.evaluateItemDetails).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Fragrance verdict: Buy')).toBeTruthy();
  });
});
