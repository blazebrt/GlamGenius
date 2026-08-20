import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';

import { CarePurchaseCheck } from '../services/apiV2';
import { CarePurchaseResult, formatCareMissingInformation } from '../components/shopping/CareShoppingPieces';

const check = (verdict: 'buy' | 'wait' | 'skip' = 'wait'): CarePurchaseCheck => ({
  care_purchase_check_version: 'v3-05.7', strategy: 'care_purchase',
  candidate_truth: {
    candidate_truth_version: 'v3-05.1', care_purchase_candidate_schema_version: 'v3-05.1',
    candidate: { id: 'candidate-1', source: 'manual', category: 'beauty', subcategory: null, display_name: 'Daily cleanser', brand: 'Example', details: { product_type: 'cleanser', ingredients_text: 'glycerin' }, price: 499, currency: 'INR', product_url: null, extraction_confidence: null, uncertain_fields: [], verification_state: 'user_declared', media_asset_id: null, ai_run_id: null, model_version: null, prompt_version: null, schema_version: null, in_inventory: false },
    review_required: false, facts_trusted: true, care_slot: 'cleanser', missing_information: [], recognised_ingredient_keys: ['glycerin'], recognised_ingredient_families: ['humectant'], note: 'Considering only',
  },
  assessment: { plan_date: '2026-08-20', assessment_fingerprint: 'assessment-1', dimensions: { role_utility: { status: 'addresses_required_gap', care_slot: 'cleanser' }, redundancy: { eligible_owned_same_slot: [{ owned_item_id: 'owned-1', display_name: 'Current cleanser' }] }, compatibility: { findings: [] }, identity_confidence: { missing_information: [] } } },
  evidence: { assessment_fingerprint: 'assessment-1', evidence_support: { findings: [] } },
  value: { assessment_fingerprint: 'assessment-1', value_fingerprint: 'value-1', value_context: { owned_value_recovery: { items: [] } } },
  verdict: { assessment_fingerprint: 'assessment-1', value_fingerprint: 'value-1', verdict, headline: verdict === 'wait' ? 'Hold this one for now.' : 'This fills a real gap.', explanation: 'A clear current-context explanation.', primary_reason_code: 'candidate_price_missing', reason_codes: ['candidate_price_missing'], supporting_reason_codes: [], decision_context: {} },
  decision: null,
});

describe('Care purchase customer experience', () => {
  it('formats internal missing-information markers for customers', () => {
    expect(formatCareMissingInformation('product_type')).toBe('product type');
    expect(formatCareMissingInformation('care_slot')).toBe('routine role');
    expect(formatCareMissingInformation('ingredients')).toBe('ingredient information');
    expect(formatCareMissingInformation('unrecognised_ingredient:retinyl-palmitate')).toBe('Ingredient not recognised: retinyl-palmitate');
  });

  it('renders the canonical verdict and routine context without Style ROI', () => {
    render(<CarePurchaseResult check={check()} onReset={() => undefined} />);
    expect(screen.getByLabelText('Care verdict: Wait')).toBeTruthy();
    expect(screen.getByText('Its place in your routine')).toBeTruthy();
    expect(screen.getByText(/fills a real gap|Hold this one/i)).toBeTruthy();
    expect(screen.queryByText(/ROI/i)).toBeNull();
    expect(screen.queryByText(/Appearance ROI/i)).toBeNull();
    expect(screen.queryByText(/wasted|ugly|unattractive|problem area/i)).toBeNull();
  });

  it('keeps the candidate separate from inventory and offers reset navigation', () => {
    const reset = jest.fn();
    render(<CarePurchaseResult check={check('buy')} onReset={reset} />);
    expect(screen.getByText(/candidate remains separate from your inventory/i)).toBeTruthy();
    expect(screen.getByLabelText('Check something else')).toBeTruthy();
  });

  it('reuses the generic decision actions and reports the server-selected state', () => {
    const decide = jest.fn();
    render(<CarePurchaseResult check={check()} onReset={() => undefined} onDecide={decide} />);
    fireEvent.press(screen.getByLabelText('I am waiting'));
    expect(decide).toHaveBeenCalledWith('waiting');
  });
});
