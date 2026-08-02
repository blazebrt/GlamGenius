import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';

import {
  ActionRow, ClarificationCard, MODULE_META, MissingInformation, NeedsInventory,
  OfflineNotice, OptionalModules, OutfitCard, STALE_AFTER_MINUTES, StaleNotice,
  TodayHeader, TodayLoading, confidenceLabel, isStale,
} from '../components/today/TodayPieces';
import { DailyPlan, LookPiece, PlanAction } from '../services/apiV2';

const ownedPiece = (overrides: Partial<LookPiece> = {}): LookPiece => ({
  id: 'piece-1', slot: 'clothing', ownership: 'owned', inventory_item_id: 'inv-1',
  display_name: 'Navy formal shirt', brand: null, category: 'wardrobe', subcategory: 'shirt',
  role_note: '', score: 0.8, owned: true, label: 'In your inventory', ...overrides,
});

const optionalPiece = (): LookPiece => ({
  id: 'piece-2', slot: 'shoes', ownership: 'optional_addition', inventory_item_id: null,
  display_name: 'formal closed shoes in a dark neutral', brand: null, category: null,
  subcategory: null, role_note: 'You have no confirmed shoes for this.', score: 0,
  owned: false, label: 'Optional addition — you do not own this',
});

const action = (overrides: Partial<PlanAction> = {}): PlanAction => ({
  id: 'act-1', module: 'hydration', action_type: 'reminder', title: 'Carry water today',
  body: 'It is hot enough that you will notice it by the afternoon.', priority: 70,
  relevance: 'Today is recorded as hot.', inventory_item_id: null,
  completed: false, completed_at: null, ...overrides,
});

const plan = (overrides: Partial<DailyPlan> = {}): DailyPlan => ({
  plan_date: '2026-08-03', timezone: 'Asia/Kolkata', weekday: 'Monday', status: 'ready',
  headline: 'Monday: office', confidence: 0.68, generated_from: 'cache',
  engine_version: 'phase5-v1', used_llm: false, locked: false, version: 1,
  outfit: {
    id: 'look-1', run_id: 'run-1', variant: 'recommended', title: 'Recommended', rank: 1,
    score: 0.7, confidence: 0.68,
    why_it_works: 'Navy formal shirt is the balanced choice for office.',
    weather_note: '', dress_code_note: '', preparation_steps: [], missing_information: [],
    factor_scores: {}, explanation_source: 'deterministic', status: 'active', saved: false,
    version: 1,
    slots: { clothing: [ownedPiece()], shoes: [optionalPiece()], accessories: [], perfume: [], hair: [], grooming: [] },
    owned_items: [ownedPiece()], optional_additions: [optionalPiece()],
    owned_item_count: 1, optional_addition_count: 1, unavailable_items: [],
    share: { title: 'Recommended', text: 'Recommended', includes_personal_data: false, note: '' },
    created_at: null,
  },
  weather: {
    id: 'w-1', for_date: '2026-08-03', condition: 'hot', temp_min_c: 27, temp_max_c: 34,
    precipitation_chance: 10, humidity: 60, location: 'Mumbai', provider: 'manual', source: 'user_declared',
  },
  weather_note: 'Dress for the heat.', event_note: '',
  primary: [action({ id: 'p-1', module: 'outfit', action_type: 'wear_outfit', title: 'Recommended', priority: 10 })],
  optional_modules: [action()],
  needs_clarification: false, clarification: null,
  missing_information: ['No weather recorded for today.'],
  worn: false, computed_at: new Date().toISOString(),
  disclaimer: 'Built from what you own and told us. Not medical or diagnostic advice.',
  ...overrides,
});

describe('Today UI', () => {
  it('shows a loading state before anything has arrived', () => {
    render(<TodayLoading />);
    expect(screen.getByLabelText('Loading today')).toBeTruthy();
  });

  it('marks a plan as stale only once it is genuinely old', () => {
    const fresh = plan({ computed_at: new Date().toISOString() });
    expect(isStale(fresh)).toBe(false);

    const old = new Date(Date.now() - (STALE_AFTER_MINUTES + 60) * 60000).toISOString();
    expect(isStale(plan({ computed_at: old }))).toBe(true);

    // Never computed means nothing to call stale.
    expect(isStale(plan({ computed_at: null }))).toBe(false);
  });

  it('offers a refresh when the plan is stale', () => {
    const refresh = jest.fn();
    render(<StaleNotice onRefresh={refresh} />);
    expect(screen.getByLabelText('This plan may be out of date')).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Refresh today'));
    expect(refresh).toHaveBeenCalled();
  });

  it('says plainly when it is showing a saved plan offline', () => {
    render(<OfflineNotice />);
    expect(screen.getByText(/you are offline/i)).toBeTruthy();
    expect(screen.getByText(/saved for you/i)).toBeTruthy();
  });

  it('the header shows the day, the weather and that it came from cache', () => {
    render(<TodayHeader plan={plan()} />);
    expect(screen.getByText('MONDAY')).toBeTruthy();
    expect(screen.getByText('Monday: office')).toBeTruthy();
    expect(screen.getByLabelText('Weather hot')).toBeTruthy();
    expect(screen.getByLabelText('Loaded from your saved plan')).toBeTruthy();
  });

  it('a fresh plan does not claim to be a saved one', () => {
    render(<TodayHeader plan={plan({ generated_from: 'fresh' })} />);
    expect(screen.queryByLabelText('Loaded from your saved plan')).toBeNull();
  });

  it('the outfit separates what you own from what you would need to buy', () => {
    render(<OutfitCard plan={plan()} />);
    expect(screen.getByLabelText('Navy formal shirt, in your inventory')).toBeTruthy();
    expect(screen.getByText('In your inventory')).toBeTruthy();
    expect(screen.getByLabelText(/optional addition you do not own/i)).toBeTruthy();
    expect(screen.getByText('Optional addition — you do not own this')).toBeTruthy();
  });

  it('wearing, rejecting and reporting an item unavailable are all reachable', () => {
    const wore = jest.fn(); const other = jest.fn(); const unavailable = jest.fn();
    render(<OutfitCard plan={plan()} onWore={wore} onNotForMe={other} onUnavailable={unavailable} />);

    fireEvent.press(screen.getByLabelText('I wore this'));
    fireEvent.press(screen.getByLabelText('Show me something else'));
    fireEvent.press(screen.getByLabelText('Navy formal shirt is not available'));

    expect(wore).toHaveBeenCalled();
    expect(other).toHaveBeenCalled();
    expect(unavailable).toHaveBeenCalledWith(expect.objectContaining({ inventory_item_id: 'inv-1' }));
  });

  it('an already worn day says so instead of asking again', () => {
    render(<OutfitCard plan={plan({ worn: true })} onWore={jest.fn()} />);
    expect(screen.getByLabelText('I wore this')).toBeTruthy();
    expect(screen.getByText('Worn')).toBeTruthy();
  });

  it('asks exactly one question, with the reason it is worth answering', () => {
    const answer = jest.fn();
    render(
      <ClarificationCard
        clarification={{
          key: 'occasion_key',
          question: 'Is "work thing" an office kind of day?',
          why: 'Getting this right changes how formal today\'s outfit is.',
          options: [{ value: 'office', label: 'Office' }, { value: 'everyday', label: 'Just a normal day' }],
        }}
        onAnswer={answer}
      />
    );
    expect(screen.getByLabelText('One quick question')).toBeTruthy();
    expect(screen.getByText(/changes how formal/i)).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Office'));
    expect(answer).toHaveBeenCalledWith('office');
  });

  it('an action can be ticked off and reads its state to a screen reader', () => {
    const complete = jest.fn();
    render(<ActionRow action={action()} onComplete={complete} />);
    const box = screen.getByLabelText('Mark Carry water today done');
    expect(box.props.accessibilityState.checked).toBe(false);
    fireEvent.press(box);
    expect(complete).toHaveBeenCalled();

    render(<ActionRow action={action({ id: 'done', completed: true })} onComplete={jest.fn()} />);
    expect(screen.getByLabelText('Mark Carry water today not done').props.accessibilityState.checked).toBe(true);
  });

  it('every optional module says why it is showing today', () => {
    render(<ActionRow action={action()} />);
    expect(screen.getByText('Today is recorded as hot.')).toBeTruthy();
  });

  it('optional modules stay collapsed so Today is not a dashboard', () => {
    const toggle = jest.fn();
    render(<OptionalModules actions={[action()]} expanded={false} onToggle={toggle} />);
    expect(screen.getByText('Also today · 1')).toBeTruthy();
    // Collapsed means the content is genuinely not rendered.
    expect(screen.queryByText('Carry water today')).toBeNull();
    fireEvent.press(screen.getByLabelText('Show 1 more for today'));
    expect(toggle).toHaveBeenCalled();
  });

  it('expanded optional modules render their content', () => {
    render(<OptionalModules actions={[action()]} expanded onToggle={jest.fn()} />);
    expect(screen.getByText('Carry water today')).toBeTruthy();
  });

  it('renders nothing at all when no module is relevant', () => {
    const view = render(<OptionalModules actions={[]} expanded onToggle={jest.fn()} />);
    expect(view.toJSON()).toBeNull();
  });

  it('an empty wardrobe is explained rather than shown as a failure', () => {
    const add = jest.fn();
    const empty = plan({
      status: 'needs_inventory', outfit: null,
      primary: [action({
        id: 'e-1', module: 'outfit', action_type: 'add_inventory',
        title: 'Add a few things you own',
        body: 'There is not enough confirmed inventory yet. We will not invent clothes you do not have.',
      })],
    });
    render(<NeedsInventory plan={empty} onAdd={add} />);
    expect(screen.getByText(/will not invent clothes/i)).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Add items to inventory'));
    expect(add).toHaveBeenCalled();
  });

  it('confidence is described in words and gaps are listed', () => {
    expect(confidenceLabel(0.8)).toMatch(/a lot of what you told us/i);
    expect(confidenceLabel(0.2)).toMatch(/very little/i);
    render(<MissingInformation plan={plan()} />);
    expect(screen.getByText('• No weather recorded for today.')).toBeTruthy();
  });

  it('covers all seven modules with a label and an icon', () => {
    expect(Object.keys(MODULE_META)).toHaveLength(7);
    expect(MODULE_META.skincare.label).toBe('Skincare');
    expect(MODULE_META.outfit.icon).toBeTruthy();
  });

  it('never uses body-shaming or shaming-about-money language', () => {
    const rendered = render(
      <>
        <TodayHeader plan={plan()} />
        <OutfitCard plan={plan()} />
        <ActionRow action={action()} />
      </>
    );
    const text = JSON.stringify(rendered.toJSON()).toLowerCase();
    for (const banned of ['money wasted', 'slimming', 'flattering', 'problem area', 'body type', 'lose weight']) {
      expect(text).not.toContain(banned);
    }
  });
});
