import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';

import {
  ComparisonPanel, EmptyState, GoalCard, MemoryCategoryRow, MemoryFactCard, MetricCard,
  MilestoneRow, NoOverallScoreNote, barWidth, formatMetric,
} from '../components/progress/ProgressPieces';
import {
  ComparisonResponse, EarnedMilestone, Goal, MemoryFact, Metric,
} from '../services/apiV2';

const metric = (overrides: Partial<Metric> = {}): Metric => ({
  key: 'wardrobe_utilisation',
  label: 'Wardrobe Utilisation',
  value: 0.62,
  unit: 'ratio',
  direction: 'higher_is_better',
  status: 'ok',
  formula: 'Items worn at least once in the last 90 days, divided by items you owned for the whole window.',
  formula_version: 'v1',
  explanation: 'How much of what you own you actually use.',
  not_a_measure_of: 'Whether you own too much or too little.',
  update_frequency: 'daily',
  inputs: { eligible_items: 21, items_worn: 13 },
  missing_inputs: [],
  note: '',
  ...overrides,
});

const goal = (overrides: Partial<Goal> = {}): Goal => ({
  id: 'g-1', kind: 'wardrobe', title: 'Use more of what I own',
  metric_key: 'wardrobe_utilisation', metric_status: 'ok',
  starting_value: 0.4, current_value: 0.62, target_value: 0.8, progress: 0.55,
  starts_on: '2026-07-01', target_date: null, status: 'active', note: null,
  updates: [{ recorded_on: '2026-07-01', value: 0.4, source: 'created', note: 'Where you started.' }],
  progress_note: null,
  ...overrides,
});

const fact = (overrides: Partial<MemoryFact> = {}): MemoryFact => ({
  id: 'm-1', category: 'avoided_colour', category_label: 'Colours you avoid',
  fact: 'You turned down mustard.', source: 'feedback', source_label: 'From feedback you gave',
  confidence: 0.7, created_at: '2026-07-01T00:00:00Z', last_reinforced_at: '2026-07-20T00:00:00Z',
  reinforcement_count: 3, verification_state: 'unverified', deletion_state: 'active',
  deleted_at: null, influences_recommendations: true,
  linked_evidence: [{ source: 'feedback', source_label: 'From feedback you gave', observed_at: '2026-07-20T00:00:00Z', evidence: {} }],
  why_we_remember: 'This came from feedback you gave on a suggestion.',
  category_enabled: true,
  ...overrides,
});

// --- Metric formatting ------------------------------------------------------

describe('Metric formatting', () => {
  it('renders each unit in the way a person reads it', () => {
    expect(formatMetric(metric({ unit: 'ratio', value: 0.62 }))).toBe('62%');
    expect(formatMetric(metric({ unit: 'days', value: 40 }))).toBe('40 days');
    expect(formatMetric(metric({ unit: 'days', value: 1 }))).toBe('1 day');
    expect(formatMetric(metric({ unit: 'scale_1_5', value: 4.5 }))).toBe('4.5 out of 5');
    expect(formatMetric(metric({ unit: 'count', value: 5 }))).toBe('5');
  });

  it('says a missing value is missing rather than calling it zero', () => {
    expect(formatMetric(metric({ value: null, status: 'unavailable' })))
      .toBe('Not enough information yet');
  });

  it('draws no bar at all for a metric with no value', () => {
    // A zero-width bar and a zero-value bar look identical, and one is an accusation.
    expect(barWidth(metric({ value: null }))).toBeNull();
    expect(barWidth(metric({ unit: 'currency', value: 2400 }))).toBeNull();
    expect(barWidth(metric({ unit: 'ratio', value: 0.62 }))).toBe(62);
  });
});

// --- Metric cards ------------------------------------------------------------

describe('Metric cards', () => {
  it('never shows a number without offering its formula', () => {
    render(<MetricCard metric={metric()} expanded={false} onToggle={jest.fn()} />);
    expect(screen.getByText('62%')).toBeTruthy();
    expect(screen.getByLabelText('Show how Wardrobe Utilisation is worked out')).toBeTruthy();
  });

  it('shows the exact formula and its version when expanded', () => {
    render(<MetricCard metric={metric()} expanded onToggle={jest.fn()} />);
    expect(screen.getByText(/divided by items you owned for the whole window/)).toBeTruthy();
    expect(screen.getByText(/Formula v1/)).toBeTruthy();
  });

  it('states what the metric is not a measure of', () => {
    render(<MetricCard metric={metric()} expanded onToggle={jest.fn()} />);
    expect(screen.getByText(/Not a measure of: Whether you own too much/)).toBeTruthy();
  });

  it('shows a dash and the reason for an unavailable metric', () => {
    render(<MetricCard
      metric={metric({
        value: null, status: 'unavailable', missing_inputs: ['usage'],
        note: 'We look at things you have owned for 90 days or more.',
      })}
      expanded={false}
      onToggle={jest.fn()}
    />);
    expect(screen.getByText('—')).toBeTruthy();
    expect(screen.getByText(/owned for 90 days or more/)).toBeTruthy();
    expect(screen.queryByText('0%')).toBeNull();
  });

  it('reads its value in words to a screen reader', () => {
    render(<MetricCard metric={metric()} expanded={false} onToggle={jest.fn()} />);
    expect(screen.getByLabelText('Wardrobe Utilisation: 62%')).toBeTruthy();
  });

  it('an unavailable metric reads as unavailable to a screen reader', () => {
    render(<MetricCard
      metric={metric({ value: null, status: 'unavailable', note: 'Not yet.' })}
      expanded={false}
      onToggle={jest.fn()}
    />);
    expect(screen.getByLabelText('Wardrobe Utilisation: Not enough information yet')).toBeTruthy();
  });

  it('flags a partial metric so the number is not read as complete', () => {
    render(<MetricCard
      metric={metric({ status: 'partial', note: '3 products have no date recorded.' })}
      expanded={false}
      onToggle={jest.fn()}
    />);
    expect(screen.getByText('3 products have no date recorded.')).toBeTruthy();
  });

  it('lists what it is still waiting on', () => {
    render(<MetricCard
      metric={metric({ status: 'partial', missing_inputs: ['usage', 'expiry'] })}
      expanded
      onToggle={jest.fn()}
    />);
    expect(screen.getByText('Waiting on: usage, expiry')).toBeTruthy();
  });

  it('toggles the formula open and closed', () => {
    const toggle = jest.fn();
    render(<MetricCard metric={metric()} expanded={false} onToggle={toggle} />);
    fireEvent.press(screen.getByLabelText('Show how Wardrobe Utilisation is worked out'));
    expect(toggle).toHaveBeenCalled();
  });
});

// --- No overall score ---------------------------------------------------------

describe('No overall score', () => {
  it('says so at the top of the screen', () => {
    render(<NoOverallScoreNote note="There is no single number here summing you up." />);
    expect(screen.getByText(/no single number here summing you up/)).toBeTruthy();
  });
});

// --- Goals ---------------------------------------------------------------------

describe('Goals', () => {
  it('shows progress against where you started', () => {
    render(<GoalCard goal={goal()} />);
    expect(screen.getByText('55%')).toBeTruthy();
    expect(screen.getByText(/Started at 0.4, aiming for 0.8/)).toBeTruthy();
  });

  it('reads its progress in words to a screen reader', () => {
    render(<GoalCard goal={goal()} />);
    expect(screen.getByLabelText('Use more of what I own. 55% of the way there.')).toBeTruthy();
  });

  it('says progress is unavailable rather than showing zero', () => {
    render(<GoalCard goal={goal({
      progress: null, current_value: null, metric_status: 'unavailable',
      progress_note: 'The metric it uses does not have enough data.',
    })} />);
    expect(screen.getByText('—')).toBeTruthy();
    expect(screen.getByText(/does not have enough data/)).toBeTruthy();
    expect(screen.queryByText('0%')).toBeNull();
  });

  it('can be marked as reached', () => {
    const onAchieve = jest.fn();
    render(<GoalCard goal={goal()} onAchieve={onAchieve} />);
    fireEvent.press(screen.getByLabelText('Mark "Use more of what I own" as reached'));
    expect(onAchieve).toHaveBeenCalled();
  });

  it('offers no "mark reached" once it is done', () => {
    render(<GoalCard goal={goal({ status: 'achieved' })} onAchieve={jest.fn()} />);
    expect(screen.queryByLabelText(/as reached/)).toBeNull();
  });
});

// --- Milestones -------------------------------------------------------------------

describe('Milestones', () => {
  const milestone = (overrides: Partial<EarnedMilestone> = {}): EarnedMilestone => ({
    id: 'ms-1', rule_id: 'milestone.used_five_low_use',
    label: 'Five unused products back in use',
    description: 'You used five products that had been sitting untouched.',
    earned_on: '2026-07-28', evidence: {}, acknowledged: false,
    ...overrides,
  });

  it('states what happened, plainly', () => {
    render(<MilestoneRow milestone={milestone()} />);
    expect(screen.getByText('Five unused products back in use')).toBeTruthy();
    expect(screen.getByText(/sitting untouched/)).toBeTruthy();
  });

  it('uses no childish wording', () => {
    render(<MilestoneRow milestone={milestone()} />);
    for (const word of [/badge/i, /trophy/i, /congratulations/i, /level up/i, /points/i]) {
      expect(screen.queryByText(word)).toBeNull();
    }
  });
});

// --- Photo comparison ---------------------------------------------------------------

describe('Photo comparison', () => {
  const refused = (): ComparisonResponse => ({
    body_area: 'face', comparable: false, comparison: null, photos_held: 2,
    rejected: [{ photo_id: 'p-1', taken_on: '2026-07-01', reasons: ['The light is different between the two.'] }],
    message: 'We are not going to show a side-by-side from these.',
    guidance: ['Stand in the same spot by the same window.'],
  });

  it('explains a refusal instead of quietly showing nothing', () => {
    render(<ComparisonPanel result={refused()} />);
    expect(screen.getByText('No comparison right now')).toBeTruthy();
    expect(screen.getByText(/The light is different/)).toBeTruthy();
  });

  it('says how to take a comparable photo next time', () => {
    render(<ComparisonPanel result={refused()} />);
    expect(screen.getByText(/Stand in the same spot by the same window/)).toBeTruthy();
  });

  it('shows the gap and the disclaimer when a comparison is allowed', () => {
    render(<ComparisonPanel result={{
      body_area: 'face', comparable: true, photos_held: 2,
      message: 'These two were taken in comparable conditions.',
      comparison: {
        comparable: true, checks: [], blocking_reasons: [], days_apart: 30, guidance: [],
        disclaimer: 'A side-by-side is only honest when both photos were taken in the same conditions.',
        baseline: { photo_id: 'p-1', media_id: 'm-1', taken_on: '2026-06-28' },
        current: { photo_id: 'p-2', media_id: 'm-2', taken_on: '2026-07-28' },
      },
    }} />);
    expect(screen.getByText('30 days apart')).toBeTruthy();
    expect(screen.getByText(/only honest when both photos/)).toBeTruthy();
  });
});

// --- Memory --------------------------------------------------------------------------

describe('Memory control', () => {
  it('shows the fact, where it came from, and why', () => {
    render(<MemoryFactCard fact={fact()} />);
    expect(screen.getByText('You turned down mustard.')).toBeTruthy();
    expect(screen.getByText(/Colours you avoid · From feedback you gave/)).toBeTruthy();
    expect(screen.getByText(/came from feedback you gave/)).toBeTruthy();
  });

  it('says outright whether a fact is shaping suggestions', () => {
    render(<MemoryFactCard fact={fact()} />);
    expect(screen.getByText('Currently used in what we suggest')).toBeTruthy();
  });

  it('says when a fact is not being used', () => {
    render(<MemoryFactCard fact={fact({ influences_recommendations: false })} />);
    expect(screen.getByText('Not used in what we suggest')).toBeTruthy();
  });

  it('offers confirm, correct and forget', () => {
    const onConfirm = jest.fn();
    const onCorrect = jest.fn();
    const onDelete = jest.fn();
    render(<MemoryFactCard fact={fact()} onConfirm={onConfirm} onCorrect={onCorrect} onDelete={onDelete} />);

    fireEvent.press(screen.getByLabelText('Confirm: You turned down mustard.'));
    fireEvent.press(screen.getByLabelText('Correct: You turned down mustard.'));
    fireEvent.press(screen.getByLabelText('Forget: You turned down mustard.'));
    expect(onConfirm).toHaveBeenCalled();
    expect(onCorrect).toHaveBeenCalled();
    expect(onDelete).toHaveBeenCalled();
  });

  it('does not offer to confirm something already confirmed', () => {
    render(<MemoryFactCard fact={fact({ verification_state: 'confirmed' })} onConfirm={jest.fn()} />);
    expect(screen.queryByLabelText(/^Confirm:/)).toBeNull();
  });

  it('shows a deleted fact as deleted, with no text and no actions', () => {
    render(<MemoryFactCard
      fact={fact({ deletion_state: 'deleted', fact: '', influences_recommendations: false })}
      onDelete={jest.fn()}
    />);
    expect(screen.getByText('Deleted')).toBeTruthy();
    expect(screen.queryByLabelText(/^Forget:/)).toBeNull();
  });

  it('says how many times something was seen', () => {
    render(<MemoryFactCard fact={fact()} />);
    expect(screen.getByText('Seen 3 times')).toBeTruthy();
  });

  it('a category switch reads as a switch to a screen reader', () => {
    const onToggle = jest.fn();
    render(<MemoryCategoryRow
      category={{ key: 'avoided_colour', label: 'Colours you avoid', enabled: true, count: 2 }}
      onToggle={onToggle}
    />);
    const row = screen.getByLabelText('Colours you avoid, 2 remembered');
    expect(row).toBeTruthy();
    fireEvent.press(row);
    expect(onToggle).toHaveBeenCalled();
  });

  it('a switched-off category says nothing was deleted', () => {
    render(<MemoryCategoryRow
      category={{ key: 'avoided_colour', label: 'Colours you avoid', enabled: false, count: 2 }}
      onToggle={jest.fn()}
    />);
    expect(screen.getByText(/switched off, nothing deleted/)).toBeTruthy();
  });
});

// --- Empty states ------------------------------------------------------------------------

describe('Empty states', () => {
  it('explains what to do rather than showing zeroes', () => {
    const onPress = jest.fn();
    render(<EmptyState
      title="Nothing to measure yet"
      body="Catalogue a few things and these fill in on their own."
      actionLabel="Open inventory"
      onPress={onPress}
    />);
    expect(screen.getByText('Nothing to measure yet')).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Open inventory'));
    expect(onPress).toHaveBeenCalled();
  });
});
