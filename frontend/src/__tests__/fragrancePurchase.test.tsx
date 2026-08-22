import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import * as ImagePicker from 'expo-image-picker';

import ShoppingCheckScreen from '../../app/shopping-check';
import { FragranceShoppingResult } from '../components/shopping/FragranceShoppingPieces';
import * as api from '../services/apiV2';

jest.mock('expo-image-picker', () => ({ launchImageLibraryAsync: jest.fn() }));
jest.mock('../services/apiV2', () => {
  const actual = jest.requireActual('../services/apiV2');
  return { ...actual, getPurchaseStrategies: jest.fn(), inspectPurchaseCandidate: jest.fn(), confirmPurchaseCandidate: jest.fn(), getFragrancePurchaseCheck: jest.fn(), recordPurchaseCandidateDecision: jest.fn(), uploadMedia: jest.fn(), evaluateItemDetails: jest.fn(), evaluateScreenshot: jest.fn(), getCarePurchaseCheck: jest.fn() };
});

const mockedApi = api as jest.Mocked<typeof api>;
const mockedPicker = ImagePicker.launchImageLibraryAsync as jest.MockedFunction<typeof ImagePicker.launchImageLibraryAsync>;
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
  candidate: { id: 'fragrance-1', source: 'manual', category: 'perfumes' as const, subcategory: null, display_name: 'Rain Garden', brand: 'House', details: { fragrance_family: 'woody', concentration: null, occasion: ['business_meeting'], season: ['summer'] }, price: 1200, currency: 'INR', product_url: null, media_asset_id: null, verification_state: 'user_declared', uncertain_fields: [], extraction_confidence: null, ai_run_id: null, model_version: null, prompt_version: null, schema_version: null, in_inventory: false as const }, review_required: false, facts_trusted: true, normalised_fragrance_family: 'woody', missing_information: [], note: 'Prospective candidate.',
};
const check = {
  fragrance_purchase_check_version: 'v3-05.9' as const, strategy: 'fragrance_purchase' as const, candidate_truth: candidate,
  collection_context: { owned_perfume_count: 0, draft_perfume_count: 0, normalised_candidate_family: 'woody', exact_owned: [], same_family_owned: [], intended_use: { occasion: ['business_meeting'], season: ['summer'] }, coverage: { covered: [], unknown: [], uncovered: [] }, owned_options_to_use_first: [], context_labels: { occasion: { business_meeting: 'Business meeting' }, season: { summer: 'Summer' } } },
  verdict: { fragrance_purchase_verdict_version: 'v3-05.9' as const, verdict: 'buy' as const, headline: 'This fills a fragrance gap.', explanation: 'You do not have a fragrance recorded yet, so this fills a real category gap.', primary_reason_code: 'first_fragrance_gap', supporting_reason_codes: [], decision_fingerprint: 'fp-1', normalised_candidate_family: 'woody', same_family_owned: [], owned_options_to_use_first: [], missing_information: [] }, decision: null,
};
const draftCandidate = { ...candidate, review_required: true, facts_trusted: false, candidate: { ...candidate.candidate, source: 'screenshot', details: { fragrance_family: 'woody', concentration: 'EDP', occasion: [], season: [] }, display_name: 'Screen Scent', price: 1299 } };
const savedDecision = { purchase_decision_memory_version: 'v3-05.8' as const, id: 'decision-1', candidate_id: 'fragrance-1', strategy: 'fragrance_purchase' as const, evaluation_id: null, recommendation_at_decision: { verdict: 'buy' as const, version: 'v3-05.9', fingerprint: 'fp-1' }, decision: 'waiting' as const, note: null, followed_recommendation: false, created_at: '', updated_at: '' };

describe('Fragrance Purchase routing', () => {
  beforeEach(() => { jest.clearAllMocks(); mockedApi.getPurchaseStrategies.mockResolvedValue(strategyResponse); mockedApi.inspectPurchaseCandidate.mockResolvedValue(candidate); mockedApi.getFragrancePurchaseCheck.mockResolvedValue(check); mockedApi.recordPurchaseCandidateDecision.mockResolvedValue(savedDecision); });

  it('discovers Perfumes only from the active canonical strategy and keeps Supplements unavailable', async () => {
    render(<ShoppingCheckScreen />); await waitFor(() => expect(screen.getByLabelText('Perfumes')).toBeTruthy()); expect(screen.queryByLabelText('Supplements')).toBeNull();
  });

  it('uses server-provided canonical fragrance context options and manual payload', async () => {
    render(<ShoppingCheckScreen />); await waitFor(() => expect(screen.getByLabelText('Perfumes')).toBeTruthy()); fireEvent.press(screen.getByLabelText('Perfumes')); fireEvent.press(screen.getByLabelText('Enter the details myself'));
    expect(screen.getByLabelText('Occasion Business meeting')).toBeTruthy(); expect(screen.getByLabelText('Season Summer')).toBeTruthy(); expect(screen.getByPlaceholderText('Fragrance name')).toBeTruthy();
    fireEvent.changeText(screen.getByLabelText('Product name'), 'Rain Garden'); fireEvent.changeText(screen.getByLabelText('Product brand'), 'House'); fireEvent.changeText(screen.getByLabelText('Fragrance family'), 'woody'); fireEvent.changeText(screen.getByLabelText('Fragrance concentration'), 'EDP'); fireEvent.press(screen.getByLabelText('Occasion Business meeting')); fireEvent.press(screen.getByLabelText('Season Summer')); fireEvent.press(screen.getByLabelText('Check this item'));
    await waitFor(() => expect(mockedApi.getFragrancePurchaseCheck).toHaveBeenCalledWith('fragrance-1')); expect(mockedApi.inspectPurchaseCandidate).toHaveBeenCalledWith(expect.objectContaining({ expected_category: 'perfumes', item: expect.objectContaining({ category: 'perfumes', details: expect.objectContaining({ fragrance_family: 'woody', concentration: 'EDP', occasion: ['business_meeting'], season: ['summer'] }) }) })); expect(mockedApi.evaluateItemDetails).not.toHaveBeenCalled(); expect(mockedApi.getCarePurchaseCheck).not.toHaveBeenCalled();
  });

  it('reviews a screenshot, confirms corrected facts including explicit clearing, and then checks it', async () => {
    mockedPicker.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://perfume.png', fileName: 'perfume.png', mimeType: 'image/png', width: 1, height: 1 }] } as any); mockedApi.uploadMedia.mockResolvedValue({ id: 'asset-1' } as any); mockedApi.inspectPurchaseCandidate.mockResolvedValueOnce(draftCandidate).mockResolvedValue(candidate); mockedApi.confirmPurchaseCandidate.mockResolvedValue(candidate);
    render(<ShoppingCheckScreen />); await waitFor(() => expect(screen.getByLabelText('Perfumes')).toBeTruthy()); fireEvent.press(screen.getByLabelText('Perfumes')); fireEvent.press(screen.getByLabelText('Upload a product screenshot')); await waitFor(() => expect(screen.getByLabelText('Review Fragrance product facts')).toBeTruthy()); expect(screen.getByText('Family: woody')).toBeTruthy(); expect(mockedApi.getFragrancePurchaseCheck).not.toHaveBeenCalled();
    fireEvent.press(screen.getByLabelText('Correct Fragrance facts')); fireEvent.changeText(screen.getByLabelText('Corrected fragrance concentration'), ''); fireEvent.changeText(screen.getByLabelText('Corrected fragrance price'), ''); fireEvent.press(screen.getByLabelText('Save corrected fragrance facts')); await waitFor(() => expect(mockedApi.confirmPurchaseCandidate).toHaveBeenCalledWith('fragrance-1', expect.objectContaining({ details: expect.objectContaining({ concentration: null }), price: null }))); expect(mockedApi.getFragrancePurchaseCheck).toHaveBeenCalledWith('fragrance-1');
  });

  it('renders customer-facing context, owned alternatives and missing information without Style purchase UI', () => {
    const richCheck = { ...check, collection_context: { ...check.collection_context, owned_options_to_use_first: [{ owned_item_id: 'owned-1', display_name: 'Office Floral', brand: 'House', remaining_percent: 60 }], same_family_owned: [{ owned_item_id: 'owned-2', display_name: 'Woody Reserve' }], coverage: { covered: ['business_meeting'], unknown: [], uncovered: [] } }, verdict: { ...check.verdict, missing_information: ['draft_owned_context'] } } as any;
    render(<FragranceShoppingResult check={richCheck} onReset={jest.fn()} onDecide={jest.fn()} />); expect(screen.getByLabelText('Fragrance intended use')).toBeTruthy(); expect(screen.getByLabelText('Owned fragrance alternatives')).toBeTruthy(); expect(screen.getByLabelText('Same family supporting information')).toBeTruthy(); expect(screen.getByText('Some perfume entries still need confirmation before they can count as owned.')).toBeTruthy(); expect(screen.queryByText(/ROI|ingredients|routine|score/i)).toBeNull();
  });

  it('saves a Fragrance decision once and retries a failed save without invoking Style evaluation', async () => {
    render(<ShoppingCheckScreen />); await waitFor(() => expect(screen.getByLabelText('Perfumes')).toBeTruthy()); fireEvent.press(screen.getByLabelText('Perfumes')); fireEvent.press(screen.getByLabelText('Enter the details myself')); fireEvent.changeText(screen.getByLabelText('Product name'), 'Rain Garden'); fireEvent.press(screen.getByLabelText('Check this item')); await waitFor(() => expect(screen.getByLabelText('I am waiting')).toBeTruthy());
    let resolve!: (value: typeof savedDecision) => void; mockedApi.recordPurchaseCandidateDecision.mockReturnValueOnce(new Promise((r) => { resolve = r; })); fireEvent.press(screen.getByLabelText('I am waiting')); fireEvent.press(screen.getByLabelText('I am waiting')); expect(mockedApi.recordPurchaseCandidateDecision).toHaveBeenCalledTimes(1); resolve(savedDecision); await waitFor(() => expect(mockedApi.recordPurchaseCandidateDecision).toHaveBeenCalledTimes(1));
    mockedApi.recordPurchaseCandidateDecision.mockRejectedValueOnce(new Error('temporary')); fireEvent.press(screen.getByLabelText('I am waiting')); await waitFor(() => expect(screen.getByLabelText('Try again')).toBeTruthy()); mockedApi.recordPurchaseCandidateDecision.mockResolvedValue(savedDecision); fireEvent.press(screen.getByLabelText('Try again')); await waitFor(() => expect(mockedApi.recordPurchaseCandidateDecision).toHaveBeenCalledTimes(3)); expect(mockedApi.evaluateItemDetails).not.toHaveBeenCalled();
  });
});
