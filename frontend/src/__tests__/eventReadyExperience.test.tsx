import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';

import { CareSummary, EventReadyActionRow, MissingInformation } from '../components/planner/EventReadyPieces';
import { EventReadyAction, EventReadyCare } from '../services/apiV2';

const action = (overrides: Partial<EventReadyAction> = {}): EventReadyAction => ({
  id: 'action-1', action_key: 'style:choose_event_look', domain: 'style', timing: 'before_event',
  title: 'Choose your event look', body: 'Choose an existing Style look.', relevance: 'No look selected.',
  inventory_item_id: null, completed: false, completed_at: null, ...overrides,
});

const care: EventReadyCare = {
  authority: 'care', decision_version: 'v1', decision_fingerprint: 'hidden',
  routine_plan_version: 'v1', routine_plan_fingerprint: 'hidden', resolved_effort: 'minimum',
  active_skin_slot_count: 1, active_hair_slot_count: 1, skin_gap_count: 0, hair_gap_count: 0,
  hair_wash: { version: 'v1', status: 'due', reason: 'due', declared_frequency: null, last_wash_on: null, next_due_on: null, fingerprint: 'hidden' },
};

describe('VC-03 Event Ready customer pieces', () => {
  it('uses server action identity and toggles completion without technical material', () => {
    const toggle = jest.fn();
    render(<EventReadyActionRow action={action()} onToggle={toggle} />);
    fireEvent.press(screen.getByLabelText('Complete Choose your event look'));
    expect(toggle).toHaveBeenCalled();
    expect(screen.queryByText('style:choose_event_look')).toBeNull();
    expect(screen.queryByText('hidden')).toBeNull();
  });

  it('translates Care and missing-information state into calm copy', () => {
    render(<CareSummary care={care} onOpen={jest.fn()} />);
    expect(screen.getByText('Hair Care is due before this event.')).toBeTruthy();
    expect(screen.getByLabelText('Open Care')).toBeTruthy();
    render(<MissingInformation keys={['event_confirmation', 'event_day_weather', 'future_internal_key']} />);
    expect(screen.getByText(/Confirm what the event is/)).toBeTruthy();
    expect(screen.getByText(/Event-day weather is not available yet/)).toBeTruthy();
    expect(screen.queryByText('future_internal_key')).toBeNull();
  });
});
