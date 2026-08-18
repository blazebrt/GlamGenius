import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';

import {
  DecisionActions, ExtractedItemReview, NewCombinations, OwnedComparisons,
  ROIBreakdown, RiskNotes, ShoppingUpload, VERDICT_META, VerdictCard, roiPercent,
} from '../components/shopping/ShoppingPieces';
import { PurchaseEvaluation, ShoppingCandidate, Verdict } from '../services/apiV2';
import ShoppingCheckScreen, { MANUAL_PURCHASE_CATEGORIES } from '../../app/shopping-check';

const candidate = (overrides: Partial<ShoppingCandidate> = {}): ShoppingCandidate => ({
  id: 'cand-1', source: 'screenshot', category: 'wardrobe', subcategory: 'shirt',
  display_name: 'Olive linen shirt', brand: 'Seen Brand', colour: 'olive green', size: 'M',
  fabric: 'linen', fit: null, formality: 'business casual', occasion_tags: ['office'],
  season_tags: ['summer'], price: 2199, currency: 'INR', product_url: null,
  extraction_confidence: 0.82, uncertain_fields: ['size'], verification_state: 'draft',
  media_asset_id: 'media-1', in_inventory: false,
  note: 'This is something you are considering. It has not been added to your inventory.',
  created_at: null, ...overrides,
});

const evaluation = (verdict: Verdict = 'wait', overrides: Partial<PurchaseEvaluation> = {}): PurchaseEvaluation => ({
  id: 'eval-1', run_id: 'run-1', candidate: candidate(), verdict,
  headline: `${verdict} — worth thinking about`,
  appearance_roi: {
    score: 0.58, version: 'appearance-roi-v1',
    formula: 'Weighted sum of the factors below, divided by the total weight of the factors that could be scored.',
    thresholds: { buy: 0.65, wait: 0.45 },
    factors: [
      { key: 'new_combinations', label: 'New outfit combinations', value: 0.72, weight: 0.22, contribution: 0.158, explanation: 'Creates about 6 new outfit combinations.' },
      { key: 'duplicate_penalty', label: 'How different it is from what you own', value: 0.4, weight: 0.16, contribution: 0.064, explanation: 'Closest match in your inventory scores 0.60 similarity.' },
    ],
  },
  confidence: 0.78, new_combinations: 6,
  summary: 'Olive linen shirt scores 0.58 on the Appearance ROI model.',
  explanation_source: 'deterministic',
  similar_owned_products: [{ inventory_item_id: 'inv-1', display_name: 'Olive cotton shirt', brand: 'Old Brand', category: 'wardrobe' }],
  existing_alternatives: [{ inventory_item_id: 'inv-2', display_name: 'White cotton shirt', brand: null, category: 'wardrobe' }],
  fit_risks: ['No size recorded on this item.'],
  colour_risks: [],
  climate_notes: ['Linen breathes well in hot weather.'],
  missing_information: ['No climate or weather was given, so that factor is a neutral placeholder.'],
  decision: null, created_at: null, ...overrides,
});

describe('Should I buy this UI', () => {
  it('offers only active Style categories in the manual purchase selector', () => {
    expect(MANUAL_PURCHASE_CATEGORIES).toEqual(['wardrobe', 'shoes', 'accessories']);
    const view = render(<ShoppingCheckScreen />);
    fireEvent.press(screen.getByLabelText('Enter the details myself'));
    expect(screen.getByLabelText('Wardrobe')).toBeTruthy();
    expect(screen.getByLabelText('Shoes')).toBeTruthy();
    expect(screen.getByLabelText('Accessories')).toBeTruthy();
    for (const label of ['Skin Care', 'Hair Care', 'Perfumes', 'Supplements']) {
      expect(screen.queryByLabelText(label)).toBeNull();
    }
    view.unmount();
  });

  it('offers a screenshot upload and a manual path', () => {
    const pick = jest.fn(); const manual = jest.fn();
    render(<ShoppingUpload onPickScreenshot={pick} onEnterDetails={manual} />);
    expect(screen.getByText(/nothing is bought here/i)).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Upload a product screenshot'));
    fireEvent.press(screen.getByLabelText('Enter the details myself'));
    expect(pick).toHaveBeenCalled();
    expect(manual).toHaveBeenCalled();
  });

  it('what was read from the screenshot is shown for review before it is trusted', () => {
    const confirm = jest.fn(); const edit = jest.fn();
    render(<ExtractedItemReview candidate={candidate()} onConfirm={confirm} onEdit={edit} />);
    expect(screen.getByText('Olive linen shirt')).toBeTruthy();
    expect(screen.getByText('Colour: olive green')).toBeTruthy();
    expect(screen.getByText(/check these before deciding: size/i)).toBeTruthy();
    expect(screen.getByText(/has not been added to your inventory/i)).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Correct Olive linen shirt'));
    expect(edit).toHaveBeenCalled();
  });

  it('a missing price is called out rather than filled in', () => {
    render(<ExtractedItemReview candidate={candidate({ price: null, uncertain_fields: [] })} />);
    expect(screen.getByText(/no price was visible/i)).toBeTruthy();
    expect(screen.queryByText(/^Price:/)).toBeNull();
  });

  it('all three verdicts render with their own wording', () => {
    for (const verdict of ['buy', 'wait', 'skip'] as Verdict[]) {
      const view = render(<VerdictCard evaluation={evaluation(verdict)} />);
      expect(screen.getByLabelText(`Verdict: ${VERDICT_META[verdict].label}`)).toBeTruthy();
      view.unmount();
    }
    expect(VERDICT_META.buy.label).toBe('Buy');
    expect(VERDICT_META.wait.label).toBe('Wait');
    expect(VERDICT_META.skip.label).toBe('Skip');
  });

  it('the ROI score is never shown without the factors behind it', () => {
    render(<ROIBreakdown roi={evaluation().appearance_roi} />);
    expect(screen.getByLabelText('Appearance ROI breakdown')).toBeTruthy();
    expect(screen.getByText(/weighted sum of the factors below/i)).toBeTruthy();
    expect(screen.getByLabelText('New outfit combinations, 72 percent')).toBeTruthy();
    expect(screen.getByText('Creates about 6 new outfit combinations.')).toBeTruthy();
    expect(screen.getByText(/buy at 65% and above/i)).toBeTruthy();
    expect(roiPercent(0.583)).toBe(58);
  });

  it('new combinations are explained, including when there are none', () => {
    const some = render(<NewCombinations count={6} />);
    expect(screen.getByText('6 new outfits')).toBeTruthy();
    some.unmount();
    render(<NewCombinations count={0} />);
    expect(screen.getByText(/would not combine with anything/i)).toBeTruthy();
  });

  it('similar owned products and existing alternatives are both shown', () => {
    render(<OwnedComparisons similar={evaluation().similar_owned_products} alternatives={evaluation().existing_alternatives} />);
    expect(screen.getByText(/already own something very like this/i)).toBeTruthy();
    expect(screen.getByText('• Olive cotton shirt (Old Brand)')).toBeTruthy();
    expect(screen.getByText(/could do the same job/i)).toBeTruthy();
    expect(screen.getByText('• White cotton shirt')).toBeTruthy();
  });

  it('nothing is rendered when there is nothing owned to compare against', () => {
    const view = render(<OwnedComparisons similar={[]} alternatives={[]} />);
    expect(view.toJSON()).toBeNull();
  });

  it('fit, colour and climate risks are grouped under things to check', () => {
    render(<RiskNotes evaluation={evaluation()} />);
    expect(screen.getByLabelText('Things to check')).toBeTruthy();
    expect(screen.getByText('Fit')).toBeTruthy();
    expect(screen.getByText('• No size recorded on this item.')).toBeTruthy();
    expect(screen.getByText('Climate')).toBeTruthy();
    expect(screen.queryByText('Colour')).toBeNull();
  });

  it('the decision can be saved, including disagreeing with us', () => {
    const decide = jest.fn();
    render(<DecisionActions current="skipped" onDecide={decide} />);
    expect(screen.getByLabelText('I skipped it').props.accessibilityState.selected).toBe(true);
    fireEvent.press(screen.getByLabelText('I bought it'));
    expect(decide).toHaveBeenCalledWith('bought');
    expect(screen.getByText(/you can disagree with us/i)).toBeTruthy();
  });

  it('never frames a purchase as wasted money or comments on a body', () => {
    const rendered = render(
      <>
        <VerdictCard evaluation={evaluation('skip')} />
        <ROIBreakdown roi={evaluation().appearance_roi} />
        <RiskNotes evaluation={evaluation()} />
      </>
    );
    const text = JSON.stringify(rendered.toJSON()).toLowerCase();
    for (const banned of ['money wasted', 'wasted', 'slimming', 'flattering', 'body type', 'problem area', 'attractiveness']) {
      expect(text).not.toContain(banned);
    }
  });
});
