import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import * as ImagePicker from 'expo-image-picker';

import ShoppingCheckScreen from '../../app/shopping-check';
import {
  CareCandidateInspection, CarePurchaseCheck, PurchaseStrategiesResponse,
} from '../services/apiV2';
import * as api from '../services/apiV2';

jest.mock('expo-image-picker', () => ({
  launchImageLibraryAsync: jest.fn(),
}));

jest.mock('../services/apiV2', () => {
  const actual = jest.requireActual('../services/apiV2');
  return {
    ...actual,
    getPurchaseStrategies: jest.fn(),
    inspectPurchaseCandidate: jest.fn(),
    getCarePurchaseCheck: jest.fn(),
    confirmPurchaseCandidate: jest.fn(),
    evaluateItemDetails: jest.fn(),
    evaluateScreenshot: jest.fn(),
    uploadMedia: jest.fn(),
  };
});

const strategies = (): PurchaseStrategiesResponse => ({
  purchase_strategy_registry_version: 'v3-05.6',
  strategies: [
    { key: 'style_purchase', label: 'Style', state: 'active', categories: [
      { key: 'wardrobe', label: 'Wardrobe' }, { key: 'shoes', label: 'Shoes' }, { key: 'accessories', label: 'Accessories' },
    ] },
    { key: 'care_purchase', label: 'Care', state: 'active', categories: [
      { key: 'beauty', label: 'Skin Care' }, { key: 'hair', label: 'Hair Care' },
    ] },
    { key: 'fragrance_purchase', label: 'Fragrance', state: 'inactive', categories: [{ key: 'perfumes', label: 'Perfumes' }] },
    { key: 'supplement_purchase', label: 'Supplements', state: 'prohibited', categories: [{ key: 'supplements', label: 'Supplements' }] },
  ],
});

const candidate = (trusted: boolean, price: number | null = 499, brand: string | null = 'Example'): CareCandidateInspection => ({
  candidate_truth_version: 'v3-05.1', care_purchase_candidate_schema_version: 'v3-05.1',
  candidate: {
    id: 'candidate-1', source: 'manual', category: 'beauty', subcategory: null,
    display_name: 'Daily cleanser', brand, details: { product_type: 'cleanser', ingredients_text: 'glycerin' },
    price, currency: 'INR', product_url: null, media_asset_id: null, verification_state: trusted ? 'user_declared' : 'draft',
    uncertain_fields: [], extraction_confidence: null, ai_run_id: null, model_version: null, prompt_version: null, schema_version: null, in_inventory: false,
  }, review_required: !trusted, facts_trusted: trusted, care_slot: 'cleanser', missing_information: [],
  recognised_ingredient_keys: ['glycerin'], recognised_ingredient_families: ['humectant'], note: 'Prospective candidate.',
});

const careCheck = (): CarePurchaseCheck => ({
  care_purchase_check_version: 'v3-05.7', strategy: 'care_purchase', candidate_truth: candidate(true),
  assessment: { assessment_fingerprint: 'assessment-1', dimensions: { role_utility: { status: 'addresses_required_gap', care_slot: 'cleanser' }, redundancy: { eligible_owned_same_slot: [] }, compatibility: { findings: [] }, identity_confidence: { missing_information: [] } } },
  evidence: { assessment_fingerprint: 'assessment-1', evidence_support: { findings: [] } },
  value: { assessment_fingerprint: 'assessment-1', value_fingerprint: 'value-1', value_context: { owned_value_recovery: { items: [] } } },
  verdict: { assessment_fingerprint: 'assessment-1', value_fingerprint: 'value-1', verdict: 'wait', headline: 'Hold this one for now.', explanation: 'A clear current-context explanation.', primary_reason_code: 'candidate_price_missing', reason_codes: ['candidate_price_missing'], supporting_reason_codes: [], decision_context: {} },
});

const mockedApi = api as jest.Mocked<typeof api>;

describe('ShoppingCheckScreen strategy and Care flows', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedApi.getPurchaseStrategies.mockResolvedValue(strategies());
    mockedApi.getCarePurchaseCheck.mockResolvedValue(careCheck());
  });

  it('uses the registry for active categories and retries a failed discovery without fallback activation', async () => {
    mockedApi.getPurchaseStrategies.mockRejectedValueOnce(new Error('registry unavailable')).mockResolvedValueOnce(strategies());
    render(<ShoppingCheckScreen />);
    await waitFor(() => expect(screen.getByText(/could not load purchase categories/i)).toBeTruthy());
    expect(screen.queryByLabelText('Perfumes')).toBeNull();
    expect(screen.queryByLabelText('Supplements')).toBeNull();
    fireEvent.press(screen.getByLabelText('Try again'));
    await waitFor(() => expect(screen.getByLabelText('Skin Care')).toBeTruthy());
    expect(mockedApi.getPurchaseStrategies).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText('Wardrobe')).toBeTruthy();
    expect(screen.getByLabelText('Shoes')).toBeTruthy();
    expect(screen.getByLabelText('Accessories')).toBeTruthy();
    expect(screen.getByLabelText('Hair Care')).toBeTruthy();
  });

  it('routes a trusted Care manual candidate through inspection and the Care check only', async () => {
    mockedApi.inspectPurchaseCandidate.mockResolvedValue(candidate(true));
    render(<ShoppingCheckScreen />);
    await waitFor(() => expect(screen.getByLabelText('Skin Care')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Skin Care'));
    fireEvent.press(screen.getByLabelText('Enter the details myself'));
    fireEvent.changeText(screen.getByLabelText('Product name'), 'Daily cleanser');
    fireEvent.press(screen.getByLabelText('Check this item'));
    await waitFor(() => expect(mockedApi.getCarePurchaseCheck).toHaveBeenCalledWith('candidate-1'));
    expect(mockedApi.inspectPurchaseCandidate).toHaveBeenCalled();
    expect(mockedApi.evaluateItemDetails).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Care verdict: Wait')).toBeTruthy();
  });

  it('keeps a draft screenshot candidate in review until confirmation', async () => {
    (ImagePicker.launchImageLibraryAsync as jest.Mock).mockResolvedValue({ canceled: false, assets: [{ uri: 'file://label.jpg', fileName: 'label.jpg', mimeType: 'image/jpeg' }] });
    mockedApi.uploadMedia.mockResolvedValue({ id: 'media-1' } as never);
    mockedApi.inspectPurchaseCandidate.mockResolvedValue(candidate(false));
    mockedApi.confirmPurchaseCandidate.mockResolvedValue(candidate(true));
    render(<ShoppingCheckScreen />);
    await waitFor(() => expect(screen.getByLabelText('Skin Care')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Skin Care'));
    fireEvent.press(screen.getByLabelText('Upload a product screenshot'));
    await waitFor(() => expect(screen.getByLabelText('Confirm product facts')).toBeTruthy());
    expect(mockedApi.getCarePurchaseCheck).not.toHaveBeenCalled();
    fireEvent.press(screen.getByLabelText('Confirm product facts'));
    await waitFor(() => expect(mockedApi.getCarePurchaseCheck).toHaveBeenCalledWith('candidate-1'));
    expect(mockedApi.confirmPurchaseCandidate).toHaveBeenCalled();
  });

  it('sends explicit nulls when screenshot facts are cleared before confirmation', async () => {
    (ImagePicker.launchImageLibraryAsync as jest.Mock).mockResolvedValue({ canceled: false, assets: [{ uri: 'file://label.jpg', fileName: 'label.jpg', mimeType: 'image/jpeg' }] });
    mockedApi.uploadMedia.mockResolvedValue({ id: 'media-1' } as never);
    mockedApi.inspectPurchaseCandidate.mockResolvedValue(candidate(false, 1299, 'Extracted brand'));
    mockedApi.confirmPurchaseCandidate.mockResolvedValue(candidate(true, null, null));
    render(<ShoppingCheckScreen />);
    await waitFor(() => expect(screen.getByLabelText('Skin Care')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Skin Care'));
    fireEvent.press(screen.getByLabelText('Upload a product screenshot'));
    await waitFor(() => expect(screen.getByLabelText('Correct product facts')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Correct product facts'));
    fireEvent.changeText(screen.getByLabelText('Corrected product brand'), '');
    fireEvent.changeText(screen.getByLabelText('Corrected product price'), '');
    fireEvent.press(screen.getByLabelText('Save corrected product facts'));
    await waitFor(() => expect(mockedApi.confirmPurchaseCandidate).toHaveBeenCalledWith('candidate-1', expect.objectContaining({ brand: null, price: null })));
    expect(mockedApi.getCarePurchaseCheck).toHaveBeenCalledWith('candidate-1');
    expect(mockedApi.confirmPurchaseCandidate.mock.calls[0][1]).not.toEqual(expect.objectContaining({ price: 1299 }));
  });

  it('keeps the Style manual path on evaluateItemDetails and outside Care APIs', async () => {
    mockedApi.evaluateItemDetails.mockRejectedValue(new Error('test stop'));
    render(<ShoppingCheckScreen />);
    await waitFor(() => expect(screen.getByLabelText('Wardrobe')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Enter the details myself'));
    fireEvent.changeText(screen.getByLabelText('Product name'), 'Olive shirt');
    fireEvent.press(screen.getByLabelText('Check this item'));
    await waitFor(() => expect(mockedApi.evaluateItemDetails).toHaveBeenCalledWith(
      expect.objectContaining({ category: 'wardrobe', display_name: 'Olive shirt' }), undefined, undefined,
    ));
    expect(mockedApi.inspectPurchaseCandidate).not.toHaveBeenCalled();
    expect(mockedApi.getCarePurchaseCheck).not.toHaveBeenCalled();
  });

  it('renders the preserved allowance trust signal for an explicit Style failure response', async () => {
    mockedApi.evaluateItemDetails.mockRejectedValue({ response: { data: { detail: {
      code: 'ANALYSIS_UNAVAILABLE', message: 'Style analysis unavailable', retryable: true, allowance_consumed: false,
    } } } });
    render(<ShoppingCheckScreen />);
    await waitFor(() => expect(screen.getByLabelText('Wardrobe')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Enter the details myself'));
    fireEvent.changeText(screen.getByLabelText('Product name'), 'Olive shirt');
    fireEvent.press(screen.getByLabelText('Check this item'));
    await waitFor(() => expect(screen.getByTestId('allowance-preserved')).toBeTruthy());
  });
});
