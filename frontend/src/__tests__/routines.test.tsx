import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';

import {
  BoundaryNotice, ConsistencyCard, EmptyModule, ExpiringSection, LowUseSection,
  NutritionCard, PerfumeCard, RoutineCard, StepRow, SupplementCard, WarningCard, WarningList,
} from '../components/routines/RoutinePieces';
import { TodayFood, TodayPerfume, TodayRoutineCard } from '../components/routines/TodayRoutine';
import {
  ExpiringReport, NutritionSuggestion, PerfumePick, Routine, RoutineStep, RuleWarning,
  ShelfProductRow, SupplementRow,
} from '../services/apiV2';

const step = (overrides: Partial<RoutineStep> = {}): RoutineStep => ({
  id: 'step-1', slot: 'cleanser', label: 'Cleanser', order: 10, required: true, optional: false,
  why: 'Everything after this works better on a clean face.',
  frequency: 'Every morning', inventory_item_id: 'inv-1', product_name: 'Gentle Face Wash',
  owned: true, safety_note: '', alternative: '', climate_note: '', is_gap: false, ...overrides,
});

const warning = (overrides: Partial<RuleWarning> = {}): RuleWarning => ({
  rule_id: 'rule.retinoid_aha', severity: 'caution',
  headline: 'Retinoid and glycolic-type acid in the same routine',
  detail: 'Using both on the same night is a common cause of stinging.',
  evidence_note: 'Widely documented cumulative-irritation caution.',
  item_ids: ['inv-1', 'inv-2'], slot: null, ...overrides,
});

const routine = (overrides: Partial<Routine> = {}): Routine => ({
  id: 'r-1', kind: 'morning', label: 'Morning routine', frequency: 'Every morning',
  steps: [step(), step({ id: 'step-2', slot: 'sunscreen', label: 'Sunscreen', order: 60, product_name: 'SPF 50' })],
  warnings: [], climate_notes: [], skipped_for_allergy: [],
  disclaimer: 'A routine built from products you told us you own.', ...overrides,
});

const product = (name: string, overrides: Partial<ShelfProductRow> = {}): ShelfProductRow => ({
  inventory_item_id: `inv-${name}`, display_name: name, brand: null, category: 'beauty',
  slot: 'cleanser', slot_label: 'Cleanser', effective_expiry: null, days_to_expiry: null,
  low_use: false, usage_count: 0, remaining_percent: null, ingredients: [],
  needs_confirmation: [], ...overrides,
});

// --- Warnings ---------------------------------------------------------------

describe('Warnings', () => {
  it('shows the reviewed rule behind every warning', () => {
    // The whole phase rests on this. If a warning cannot name its rule, the
    // user cannot ask where it came from.
    render(<WarningCard warning={warning()} />);
    expect(screen.getByText('Rule: rule.retinoid_aha')).toBeTruthy();
    expect(screen.getByText(/cumulative-irritation/)).toBeTruthy();
  });

  it('says how serious something is in words, not colour alone', () => {
    render(<WarningCard warning={warning({ severity: 'avoid' })} />);
    expect(screen.getByLabelText(/Leave this out/)).toBeTruthy();
  });

  it('prefers the plain-English wording when the server sent one', () => {
    render(<WarningCard warning={warning({ plain_english: 'Use these on different nights.' })} />);
    expect(screen.getByText('Use these on different nights.')).toBeTruthy();
  });

  it('renders nothing at all when there is nothing to warn about', () => {
    const { toJSON } = render(<WarningList warnings={[]} />);
    expect(toJSON()).toBeNull();
  });
});

// --- Routine steps ------------------------------------------------------------

describe('Routine steps', () => {
  it('every step says why it exists, how often, and whether it is optional', () => {
    render(<StepRow step={step()} />);
    expect(screen.getByText(/works better on a clean face/)).toBeTruthy();
    expect(screen.getByText('Every morning')).toBeTruthy();
    expect(screen.getByText('Worth keeping')).toBeTruthy();
  });

  it('an optional step is labelled optional rather than looking required', () => {
    render(<StepRow step={step({ required: false, optional: true })} />);
    expect(screen.getByText('Optional')).toBeTruthy();
  });

  it('a gap names no product and cannot be ticked off', () => {
    const onComplete = jest.fn();
    render(<StepRow step={step({ is_gap: true, product_name: null, inventory_item_id: null, owned: false })} onComplete={onComplete} />);
    expect(screen.getByText('Nothing recorded for this step yet')).toBeTruthy();
    expect(screen.queryByLabelText(/Mark done/)).toBeNull();
  });

  it('a step can be ticked off and reports it', () => {
    const onComplete = jest.fn();
    render(<StepRow step={step()} onComplete={onComplete} />);
    fireEvent.press(screen.getByLabelText('Mark done: Cleanser'));
    expect(onComplete).toHaveBeenCalled();
  });

  it('a completed step reads as checked to a screen reader', () => {
    render(<StepRow step={step({ completed_today: true })} onComplete={jest.fn()} />);
    expect(screen.getByLabelText('Undo: Cleanser')).toBeTruthy();
  });

  it('offers feedback only for a persisted normal step', () => {
    const onFeedback = jest.fn();
    render(<StepRow step={step()} onFeedback={onFeedback} />);
    fireEvent.press(screen.getByLabelText('Record experience for Cleanser'));
    expect(onFeedback).toHaveBeenCalledWith(expect.objectContaining({ id: 'step-1' }));
  });

  it('does not offer feedback for a gap or a step without a persisted id', () => {
    const onFeedback = jest.fn();
    const { rerender } = render(<StepRow step={step({ is_gap: true })} onFeedback={onFeedback} />);
    expect(screen.queryByLabelText('Record experience for Cleanser')).toBeNull();
    rerender(<StepRow step={step({ id: undefined })} onFeedback={onFeedback} />);
    expect(screen.queryByLabelText('Record experience for Cleanser')).toBeNull();
  });

  it('shows the safety note and the alternative when there is one', () => {
    render(<StepRow step={step({
      safety_note: 'Two or three times a week is plenty.',
      alternative: 'Plain water is better than a harsh soap.',
    })} />);
    expect(screen.getByText('Two or three times a week is plenty.')).toBeTruthy();
    expect(screen.getByText(/Plain water is better/)).toBeTruthy();
  });
});

// --- Routines ------------------------------------------------------------------

describe('Routine card', () => {
  it('shows the routine, its steps and its disclaimer', () => {
    render(<RoutineCard routine={routine()} />);
    expect(screen.getByText('Morning routine')).toBeTruthy();
    expect(screen.getByText('Gentle Face Wash')).toBeTruthy();
    expect(screen.getByText(/products you told us you own/)).toBeTruthy();
  });

  it('says plainly what was left out because of a declared allergy', () => {
    render(<RoutineCard routine={routine({ skipped_for_allergy: ['Scented cream'] })} />);
    expect(screen.getByText(/Left out because of what you told us to avoid: Scented cream/)).toBeTruthy();
  });

  it('carries the routine warnings with their rule ids', () => {
    render(<RoutineCard routine={routine({ warnings: [warning()] })} />);
    expect(screen.getByText('Rule: rule.retinoid_aha')).toBeTruthy();
  });
});

// --- Consistency ------------------------------------------------------------------

describe('Consistency', () => {
  it('counts days and steps and never shows a streak', () => {
    render(<ConsistencyCard consistency={{
      days_considered: 14, days_with_activity: 6, steps_completed: 19,
      note: 'Missing a day is not a problem — we do not count streaks.',
    }} />);
    expect(screen.getByText('6')).toBeTruthy();
    expect(screen.getByText(/we do not count streaks/)).toBeTruthy();
    expect(screen.queryByText(/day streak/i)).toBeNull();
  });
});

// --- Shelf sections ------------------------------------------------------------------

describe('Shelf sections', () => {
  const report = (overrides: Partial<ExpiringReport> = {}): ExpiringReport => ({
    window_days: 60, expired: [], expiring_soon: [], no_date_recorded: [],
    note: 'Worked out from the dates you recorded.', ...overrides,
  });

  it('shows nothing when there is nothing to say about dates', () => {
    const { toJSON } = render(<ExpiringSection report={report()} />);
    expect(toJSON()).toBeNull();
  });

  it('separates what has expired from what is close', () => {
    render(<ExpiringSection report={report({
      expired: [product('Old cream', { days_to_expiry: -10 })],
      expiring_soon: [product('Serum', { days_to_expiry: 12 })],
    })} />);
    expect(screen.getByText('• Old cream — past its date')).toBeTruthy();
    expect(screen.getByText('• Serum — 12 days left')).toBeTruthy();
  });

  it('reports missing dates as missing rather than guessing one', () => {
    render(<ExpiringSection report={report({ no_date_recorded: [product('Cleanser')] })} />);
    expect(screen.getByText(/1 with no date recorded/)).toBeTruthy();
  });

  it('low use is stated plainly and never as waste', () => {
    render(<LowUseSection low={{
      products: [product('Unused serum')], count: 1,
      definition: 'Recorded at least 30 days ago and barely used.',
      note: 'Using these beats replacing them.',
    }} />);
    expect(screen.getByText('Sitting unused · 1')).toBeTruthy();
    expect(screen.queryByText(/wasted/i)).toBeNull();
  });

  it('hides the low-use section entirely when nothing is unused', () => {
    const { toJSON } = render(<LowUseSection low={{ products: [], count: 0, definition: '', note: '' }} />);
    expect(toJSON()).toBeNull();
  });
});

// --- Perfume ---------------------------------------------------------------------------

describe('Perfume', () => {
  const pick = (overrides: Partial<PerfumePick> = {}): PerfumePick => ({
    inventory_item_id: 'inv-p', display_name: 'Cedar EDP', brand: null,
    fragrance_family: 'woody', remaining_percent: 40,
    reasons: [{ rule_id: 'perfume.cool_weather', factor: 'weather', note: 'Cooler weather carries a heavier scent — convention, not chemistry.' }],
    missing_information: [], owned: true, ...overrides,
  });

  it('shows the owned bottle and the reason it was picked', () => {
    render(<PerfumeCard pick={pick()} />);
    expect(screen.getByText('Cedar EDP')).toBeTruthy();
    expect(screen.getByText(/convention, not chemistry/)).toBeTruthy();
  });

  it('says what it did not know rather than filling it in', () => {
    render(<PerfumeCard pick={pick({ missing_information: ['No fragrance family recorded.'] })} />);
    expect(screen.getByText('No fragrance family recorded.')).toBeTruthy();
  });

  it('the Today strip disappears when there is no perfume to suggest', () => {
    const { toJSON } = render(<TodayPerfume pick={null} />);
    expect(toJSON()).toBeNull();
  });
});

// --- Supplements -------------------------------------------------------------------------

describe('Supplements', () => {
  const row = (overrides: Partial<SupplementRow> = {}): SupplementRow => ({
    inventory_item_id: 'inv-s', display_name: 'Vitamin D3', brand: 'Test Brand',
    user_entered_purpose: 'general', use_frequency: 'daily',
    expiry_date: '2026-12-01', days_to_expiry: 120, flags: [], ...overrides,
  });

  it('shows dates and what you said, and never a dose', () => {
    render(<SupplementCard row={row()} />);
    expect(screen.getByText('Vitamin D3')).toBeTruthy();
    expect(screen.getByText('You noted: general')).toBeTruthy();
    expect(screen.queryByText(/mg|dose|dosage/i)).toBeNull();
  });

  it('says plainly when no expiry date was recorded', () => {
    render(<SupplementCard row={row({ expiry_date: null, days_to_expiry: null })} />);
    expect(screen.getByText('No expiry date recorded')).toBeTruthy();
  });

  it('shows the flag the server sent rather than inventing advice', () => {
    render(<SupplementCard row={row({ flags: [{ flag: 'expired', message: 'This is past the date you recorded.' }] })} />);
    expect(screen.getByText('This is past the date you recorded.')).toBeTruthy();
  });

  it('a health question shows the professional boundary', () => {
    render(<BoundaryNotice message="Please talk to a doctor, dermatologist or pharmacist about this one." />);
    expect(screen.getByLabelText('This one needs a professional')).toBeTruthy();
    expect(screen.getByText(/talk to a doctor/)).toBeTruthy();
  });
});

// --- Nutrition -------------------------------------------------------------------------------

describe('Food context', () => {
  const suggestion = (overrides: Partial<NutritionSuggestion> = {}): NutritionSuggestion => ({
    rule_id: 'nutrition.protein', nutrient: 'protein', display_name: 'Protein',
    appearance_context: 'Hair and nails are largely protein.',
    foods: ['dal — moong, masoor, toor', 'paneer', 'curd or dahi'],
    note: 'Spread across meals is the usual suggestion.', ...overrides,
  });

  it('shows Indian foods and never a number', () => {
    render(<NutritionCard suggestion={suggestion()} />);
    expect(screen.getByText(/dal — moong, masoor, toor · paneer · curd or dahi/)).toBeTruthy();
    expect(screen.queryByText(/calorie|kcal|grams per day/i)).toBeNull();
  });

  it('a nutrient with no sources left for this diet says so rather than vanishing', () => {
    render(<NutritionCard suggestion={suggestion({
      foods: [], note: 'The common sources we list are all outside a vegan diet. A dietitian can help.',
    })} />);
    expect(screen.getByText(/A dietitian can help/)).toBeTruthy();
  });

  it('the Today strip stays hidden when food context is off', () => {
    const { toJSON } = render(<TodayFood suggestion={null} />);
    expect(toJSON()).toBeNull();
  });
});

// --- Today ---------------------------------------------------------------------------------------

describe('Today routine strip', () => {
  it('shows progress without a score', () => {
    render(<TodayRoutineCard routine={routine({
      steps: [step({ completed_today: true }), step({ id: 'step-2', slot: 'sunscreen', label: 'Sunscreen', order: 60 })],
    })} />);
    expect(screen.getByText('1 of 2 done')).toBeTruthy();
  });

  it('renders nothing when the only steps are gaps', () => {
    const { toJSON } = render(<TodayRoutineCard routine={routine({
      steps: [step({ is_gap: true, product_name: null })],
    })} />);
    expect(toJSON()).toBeNull();
  });

  it('a step can be ticked straight from Today', () => {
    const onComplete = jest.fn();
    render(<TodayRoutineCard routine={routine()} onComplete={onComplete} />);
    fireEvent.press(screen.getByLabelText('Mark done: Cleanser, Gentle Face Wash'));
    expect(onComplete).toHaveBeenCalled();
  });
});

// --- Empty states ---------------------------------------------------------------------------------

describe('Empty modules', () => {
  it('an empty module explains what to do instead of showing zeroes', () => {
    const onPress = jest.fn();
    render(<EmptyModule
      title="Nothing on your shelf yet"
      body="Add a face wash you already own."
      actionLabel="Add a product"
      onPress={onPress}
    />);
    expect(screen.getByText('Nothing on your shelf yet')).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Add a product'));
    expect(onPress).toHaveBeenCalled();
  });

  it('an empty module with no action still reads sensibly', () => {
    render(<EmptyModule title="No perfumes recorded" body="Add one and this starts working." />);
    expect(screen.getByLabelText('No perfumes recorded')).toBeTruthy();
  });
});
