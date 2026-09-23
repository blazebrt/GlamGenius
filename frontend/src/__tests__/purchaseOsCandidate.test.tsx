/**
 * Step 14's secondary entry: "Check a product you're considering".
 *
 * The real screen, with the network mocked at apiV2. It proves the screen
 * routes on the server's Purchase OS answer — the canonical Care or Fragrance
 * result only when the server decided, the existing review when facts still
 * need confirming, the supplement boundary instead of any purchase UI — and
 * that a decision is one tap and never touches the shelf.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import PurchaseCandidateScreen from '../../app/purchase-candidate';
import { PURCHASE_OS } from '../strings/purchaseOs';
import type { PurchaseOsCheck } from '../services/apiV2';

const mockPush = jest.fn();
const mockReplace = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush, replace: mockReplace, back: jest.fn() }),
}));
const mockPickImage = jest.fn();
jest.mock('expo-image-picker', () => ({
  launchImageLibraryAsync: (...args: unknown[]) => mockPickImage(...args),
}));

const mockStrategies = jest.fn();
const mockInspect = jest.fn();
const mockConfirm = jest.fn();
const mockOsCheck = jest.fn();
const mockCareCheck = jest.fn();
const mockFragranceCheck = jest.fn();
const mockRecordCare = jest.fn();
const mockRecordCandidate = jest.fn();
const mockUpload = jest.fn();
// Every shelf write there is. None of them may fire from a candidate.
const mockCreateInventory = jest.fn();
const mockAddToShelf = jest.fn();
jest.mock('../services/apiV2', () => ({
  getPurchaseStrategies: (...args: unknown[]) => mockStrategies(...args),
  inspectPurchaseCandidate: (...args: unknown[]) => mockInspect(...args),
  confirmPurchaseCandidate: (...args: unknown[]) => mockConfirm(...args),
  getCandidatePurchaseCheck: (...args: unknown[]) => mockOsCheck(...args),
  getCarePurchaseCheck: (...args: unknown[]) => mockCareCheck(...args),
  getFragrancePurchaseCheck: (...args: unknown[]) => mockFragranceCheck(...args),
  recordCarePurchaseDecision: (...args: unknown[]) => mockRecordCare(...args),
  recordPurchaseCandidateDecision: (...args: unknown[]) => mockRecordCandidate(...args),
  uploadMedia: (...args: unknown[]) => mockUpload(...args),
  createInventoryItem: (...args: unknown[]) => mockCreateInventory(...args),
  addScanProductToShelf: (...args: unknown[]) => mockAddToShelf(...args),
}));

const REGISTRY = {
  purchase_strategy_registry_version: 'v1',
  strategies: [
    { key: 'style_purchase', label: 'Style', state: 'inactive', categories: [{ key: 'clothing', label: 'Clothing' }] },
    { key: 'care_purchase', label: 'Care', state: 'active', categories: [{ key: 'beauty', label: 'Beauty' }, { key: 'hair', label: 'Hair' }] },
    { key: 'fragrance_purchase', label: 'Fragrance', state: 'active', categories: [{ key: 'perfumes', label: 'Perfumes' }] },
    { key: 'supplement_purchase', label: 'Supplements', state: 'prohibited', categories: [{ key: 'supplements', label: 'Supplements' }] },
  ],
  fragrance_context_options: { occasions: [], seasons: [] },
};

const careCandidate = (trusted: boolean) => ({
  candidate_truth_version: 'v3-05.1', care_purchase_candidate_schema_version: 'v3-05.1',
  candidate: {
    id: 'candidate-1', source: trusted ? 'manual' : 'screenshot', category: 'beauty', subcategory: null,
    display_name: 'Daily cleanser', brand: 'Example', details: { product_type: 'cleanser', ingredients_text: 'glycerin' },
    price: 499, currency: 'INR', product_url: null, extraction_confidence: null, uncertain_fields: [],
    verification_state: trusted ? 'user_declared' : 'draft', media_asset_id: null, ai_run_id: null, model_version: null,
    prompt_version: null, schema_version: null, in_inventory: false,
  },
  review_required: !trusted, facts_trusted: trusted, care_slot: 'cleanser', missing_information: [],
  recognised_ingredient_keys: ['glycerin'], recognised_ingredient_families: ['humectant'], note: 'Considering only',
});

const careCheck = {
  care_purchase_check_version: 'v3-05.7', strategy: 'care_purchase',
  candidate_truth: careCandidate(true),
  assessment: { plan_date: '2026-08-20', assessment_fingerprint: 'assessment-1', dimensions: { role_utility: { status: 'addresses_required_gap', care_slot: 'cleanser' }, redundancy: { eligible_owned_same_slot: [] }, compatibility: { findings: [] }, identity_confidence: { missing_information: [] } } },
  evidence: { assessment_fingerprint: 'assessment-1', evidence_support: { findings: [] } },
  value: { assessment_fingerprint: 'assessment-1', value_fingerprint: 'value-1', value_context: { owned_value_recovery: { items: [] } } },
  verdict: { assessment_fingerprint: 'assessment-1', value_fingerprint: 'value-1', verdict: 'wait', headline: 'Hold this one for now.', explanation: 'A clear current-context explanation.', primary_reason_code: 'candidate_price_missing', reason_codes: [], supporting_reason_codes: [], decision_context: {} },
  decision: null,
};

const fragranceCandidate = {
  candidate_truth_version: 'v3-05.9', fragrance_purchase_candidate_schema_version: 'v3-05.9',
  candidate: {
    id: 'candidate-2', source: 'manual', category: 'perfumes', subcategory: null, display_name: 'Evening Oud', brand: 'House',
    details: { fragrance_family: 'woody', concentration: 'EDP', occasion: [], season: [] }, price: 2199, currency: 'INR',
    product_url: null, extraction_confidence: null, uncertain_fields: [], verification_state: 'user_declared',
    media_asset_id: null, ai_run_id: null, model_version: null, prompt_version: null, schema_version: null, in_inventory: false,
  },
  review_required: false, facts_trusted: true, normalised_fragrance_family: 'woody', missing_information: [], note: 'Considering only',
};

const fragranceCheck = {
  fragrance_purchase_check_version: 'v3-05.9', strategy: 'fragrance_purchase', candidate_truth: fragranceCandidate,
  collection_context: { owned_perfume_count: 0, draft_perfume_count: 0, normalised_candidate_family: 'woody', exact_owned: [], same_family_owned: [], intended_use: { occasion: [], season: [] }, coverage: { covered: [], unknown: [], uncovered: [] }, owned_options_to_use_first: [] },
  verdict: { fragrance_purchase_verdict_version: 'v3-05.9', verdict: 'buy', headline: 'Adds something new.', explanation: 'Nothing you own covers this.', missing_information: [], primary_reason_code: 'fills_gap', decision_fingerprint: 'c'.repeat(64) },
  decision: null,
};

function osCheck(state: PurchaseOsCheck['decision']['state'], strategy: string | null, extra: Partial<PurchaseOsCheck> = {}): PurchaseOsCheck {
  return {
    contract_version: 'step-14-v1',
    context: { kind: 'candidate', strategy, category: 'beauty' },
    subject: { household_subject_id: null, is_account_holder: true },
    identity: { state: 'exact' },
    decision: {
      state, verdict: state === 'decided' ? 'wait' : null,
      primary_reason_code: state === 'decided' ? 'candidate_price_missing' : state === 'prohibited' ? 'supplement_purchase_prohibited' : state === 'unsupported' ? 'unsupported_strategy' : 'candidate_confirmation_required',
      primary_reason_authority: strategy ?? 'purchase_os', decision_fingerprint: null,
    },
    authorities: [],
    memory: state === 'decided' ? {
      kind: 'candidate_decision', fidelity: 'recommendation_snapshot', state: 'no_prior_exact_decision',
      guard_state: 'no_step9a_prior_event', current_decision: null, most_recent_exact: null, prior_consideration_count: 0, history_complete: true,
    } : null,
    ownership: null, alternative: null, value: null,
    boundary: state === 'prohibited' ? { code: 'supplement_purchase_prohibited', redirect: 'supplement_label_utility' } : null,
    missing_information: [],
    ...extra,
  };
}

const copy = PURCHASE_OS.candidate;

async function renderScreen() {
  mockStrategies.mockResolvedValue(REGISTRY);
  render(<PurchaseCandidateScreen />);
  await waitFor(() => expect(screen.getByRole('button', { name: copy.category.beauty })).toBeTruthy());
}

async function enterCareDetails() {
  fireEvent.press(screen.getByRole('button', { name: copy.category.beauty }));
  fireEvent.press(screen.getByRole('button', { name: copy.mode.details }));
  fireEvent.changeText(screen.getByLabelText(copy.field.name), 'Daily cleanser');
  await act(async () => { fireEvent.press(screen.getByRole('button', { name: copy.check })); });
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe('Step 14 — checking a product you are considering', () => {
  it('keeps scanning first and offers the way back to the scanner', async () => {
    await renderScreen();
    expect(screen.getByText(copy.scanFirst)).toBeTruthy();
    fireEvent.press(screen.getByRole('button', { name: copy.scanAction }));
    expect(mockReplace).toHaveBeenCalledWith('/scan-product');
  });

  it('offers only registry categories it has words for, never an inactive Style category', async () => {
    await renderScreen();
    for (const key of ['beauty', 'hair', 'perfumes', 'supplements'] as const) {
      expect(screen.getByRole('button', { name: copy.category[key] })).toBeTruthy();
    }
    expect(screen.queryByText(/Clothing|Style/)).toBeNull();
  });

  it('reaches the canonical Care result only after the Purchase OS decided', async () => {
    mockInspect.mockResolvedValue(careCandidate(true));
    mockOsCheck.mockResolvedValue(osCheck('decided', 'care_purchase'));
    mockCareCheck.mockResolvedValue(careCheck);
    await renderScreen();
    await enterCareDetails();
    await waitFor(() => expect(screen.getByLabelText('Care verdict: Wait')).toBeTruthy());
    expect(mockInspect).toHaveBeenCalledWith(expect.objectContaining({
      source: 'manual', item: expect.objectContaining({ category: 'beauty', display_name: 'Daily cleanser' }),
    }));
    expect(mockOsCheck).toHaveBeenCalledWith('candidate-1');
    expect(mockCareCheck).toHaveBeenCalledWith('candidate-1');
    expect(mockFragranceCheck).not.toHaveBeenCalled();
  });

  it('reaches the canonical Fragrance result as a secondary action', async () => {
    mockInspect.mockResolvedValue(fragranceCandidate);
    mockOsCheck.mockResolvedValue(osCheck('decided', 'fragrance_purchase'));
    mockFragranceCheck.mockResolvedValue(fragranceCheck);
    await renderScreen();
    fireEvent.press(screen.getByRole('button', { name: copy.category.perfumes }));
    fireEvent.press(screen.getByRole('button', { name: copy.mode.details }));
    fireEvent.changeText(screen.getByLabelText(copy.field.name), 'Evening Oud');
    await act(async () => { fireEvent.press(screen.getByRole('button', { name: copy.check })); });
    await waitFor(() => expect(screen.getByLabelText('Fragrance verdict: Buy')).toBeTruthy());
    expect(mockInspect).toHaveBeenCalledWith(expect.objectContaining({ expected_category: 'perfumes' }));
    expect(mockFragranceCheck).toHaveBeenCalledWith('candidate-2');
    expect(mockCareCheck).not.toHaveBeenCalled();
  });

  it('gives supplements the prohibited boundary and never a purchase form', async () => {
    await renderScreen();
    fireEvent.press(screen.getByRole('button', { name: copy.category.supplements }));
    expect(screen.getByTestId('supplement-boundary')).toBeTruthy();
    expect(screen.getByText(PURCHASE_OS.supplementBoundary.body)).toBeTruthy();
    expect(screen.queryByRole('button', { name: copy.mode.details })).toBeNull();
    expect(screen.queryByRole('button', { name: copy.mode.photo })).toBeNull();
    expect(screen.queryByLabelText(copy.field.name)).toBeNull();
    fireEvent.press(screen.getByRole('button', { name: PURCHASE_OS.supplementBoundary.action }));
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/inventory-add', params: { category: 'supplements' } });
    expect(mockInspect).not.toHaveBeenCalled();
    expect(mockOsCheck).not.toHaveBeenCalled();
  });

  it('shows the boundary, not a purchase UI, whenever the server says prohibited', async () => {
    mockInspect.mockResolvedValue(careCandidate(true));
    mockOsCheck.mockResolvedValue(osCheck('prohibited', 'supplement_purchase'));
    await renderScreen();
    await enterCareDetails();
    await waitFor(() => expect(screen.getByTestId('supplement-boundary')).toBeTruthy());
    expect(mockCareCheck).not.toHaveBeenCalled();
    expect(mockFragranceCheck).not.toHaveBeenCalled();
  });

  it('refuses an unsupported strategy plainly and never falls back to Care', async () => {
    mockInspect.mockResolvedValue(careCandidate(true));
    mockOsCheck.mockResolvedValue(osCheck('unsupported', null));
    await renderScreen();
    await enterCareDetails();
    await waitFor(() => expect(screen.getByText(copy.unsupported)).toBeTruthy());
    expect(mockCareCheck).not.toHaveBeenCalled();
  });

  it('requires confirmation of a photo read before any purchase answer', async () => {
    mockPickImage.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://shot.jpg', fileName: 'shot.jpg', mimeType: 'image/jpeg' }] });
    mockUpload.mockResolvedValue({ id: 'media-1' });
    mockInspect.mockResolvedValue(careCandidate(false));
    mockOsCheck.mockResolvedValueOnce(osCheck('not_enough_information', 'care_purchase'));
    await renderScreen();
    fireEvent.press(screen.getByRole('button', { name: copy.category.beauty }));
    await act(async () => { fireEvent.press(screen.getByRole('button', { name: copy.mode.photo })); });
    await waitFor(() => expect(screen.getByText(copy.confirmationRequired)).toBeTruthy());
    expect(mockInspect).toHaveBeenCalledWith({ source: 'screenshot', media_asset_id: 'media-1', expected_category: 'beauty' });
    expect(mockCareCheck).not.toHaveBeenCalled();
    expect(screen.queryByLabelText('Care verdict: Wait')).toBeNull();

    mockConfirm.mockResolvedValue(careCandidate(true));
    mockOsCheck.mockResolvedValueOnce(osCheck('decided', 'care_purchase'));
    mockCareCheck.mockResolvedValue(careCheck);
    await act(async () => { fireEvent.press(screen.getByLabelText('Confirm product facts')); });
    await waitFor(() => expect(screen.getByLabelText('Care verdict: Wait')).toBeTruthy());
    expect(mockConfirm).toHaveBeenCalledWith('candidate-1', expect.objectContaining({ display_name: 'Daily cleanser' }));
  });

  it('obeys the server when even a typed entry still needs confirming', async () => {
    mockInspect.mockResolvedValue(careCandidate(false));
    mockOsCheck.mockResolvedValue(osCheck('not_enough_information', 'care_purchase'));
    await renderScreen();
    await enterCareDetails();
    await waitFor(() => expect(screen.getByText(copy.confirmationRequired)).toBeTruthy());
    expect(mockCareCheck).not.toHaveBeenCalled();
  });

  it('records a decision in one tap, reads memory back, and never touches the shelf', async () => {
    mockInspect.mockResolvedValue(careCandidate(true));
    mockOsCheck.mockResolvedValue(osCheck('decided', 'care_purchase'));
    mockCareCheck.mockResolvedValue(careCheck);
    mockRecordCare.mockResolvedValue({ purchase_decision_memory_version: 'v3-05.8', id: 'decision-1', candidate_id: 'candidate-1', decision: 'bought' });
    await renderScreen();
    await enterCareDetails();
    await waitFor(() => expect(screen.getByLabelText('I bought it')).toBeTruthy());
    const readsBefore = mockOsCheck.mock.calls.length;
    await act(async () => { fireEvent.press(screen.getByLabelText('I bought it')); });
    await waitFor(() => expect(mockOsCheck.mock.calls.length).toBe(readsBefore + 1));
    expect(mockRecordCare).toHaveBeenCalledTimes(1);
    expect(mockRecordCare).toHaveBeenCalledWith('candidate-1', 'bought', undefined, '2026-08-20');
    expect(mockCreateInventory).not.toHaveBeenCalled();
    expect(mockAddToShelf).not.toHaveBeenCalled();
    // The decision is memory, not a new answer: the canonical check is not re-run.
    expect(mockCareCheck).toHaveBeenCalledTimes(1);
  });

  it('shows purchase memory from the Decision Memory guard state the Purchase OS carries', async () => {
    mockInspect.mockResolvedValue(careCandidate(true));
    mockOsCheck.mockResolvedValue(osCheck('decided', 'care_purchase', {
      memory: {
        kind: 'candidate_decision', fidelity: 'recommendation_snapshot', state: 'prior_exact_decision',
        guard_state: 'exact_prior_waiting', current_decision: null,
        most_recent_exact: { decision: 'waiting', recommendation_at_decision: 'wait', occurred_at: '2026-08-01T00:00:00Z' },
        prior_consideration_count: 1, history_complete: true,
      },
    }));
    mockCareCheck.mockResolvedValue(careCheck);
    await renderScreen();
    await enterCareDetails();
    await waitFor(() => expect(screen.getByText('Last time, you chose to wait.')).toBeTruthy());
  });

  it('shows one keyed failure line and retries the same request', async () => {
    mockInspect.mockRejectedValueOnce(new Error('network')).mockResolvedValue(careCandidate(true));
    mockOsCheck.mockResolvedValue(osCheck('decided', 'care_purchase'));
    mockCareCheck.mockResolvedValue(careCheck);
    await renderScreen();
    await enterCareDetails();
    await waitFor(() => expect(screen.getByText(copy.failed)).toBeTruthy());
    await act(async () => { fireEvent.press(screen.getByRole('button', { name: copy.retry })); });
    await waitFor(() => expect(screen.getByLabelText('Care verdict: Wait')).toBeTruthy());
    expect(mockInspect).toHaveBeenCalledTimes(2);
  });
});
