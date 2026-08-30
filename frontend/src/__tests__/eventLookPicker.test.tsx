/* eslint-disable import/first -- module mocks must be declared before imports. */
/**
 * Choosing an event look, inside Event Ready.
 *
 * Replaces the coverage that lived in styleEventModeScreen.test.tsx when the
 * Style Me screen still existed. The behaviour under test is the same — the
 * event is the source of truth, the look links through the canonical API, a
 * rejected link keeps the results on screen — but it now happens inside Event
 * Ready, because no Style screen exists (PRODUCT_CONSTITUTION.md).
 */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

const mockGetOccasionTypes = jest.fn();
const mockStyleForOccasion = jest.fn();
const mockSetEventReadyLook = jest.fn();

jest.mock('../services/apiV2', () => {
  const actual = jest.requireActual('../services/apiV2');
  return {
    ...actual,
    getOccasionTypes: (...args: unknown[]) => mockGetOccasionTypes(...args),
    styleForOccasion: (...args: unknown[]) => mockStyleForOccasion(...args),
    setEventReadyLook: (...args: unknown[]) => mockSetEventReadyLook(...args),
  };
});

import { EventLookPicker, buildEventOccasionInput } from '../components/planner/EventLookPicker';

const occasion = {
  key: 'wedding', label: 'Wedding', formality: 5, dress_codes: ['black_tie'],
  default_dress_code: 'black_tie', default_setting: 'indoor', required_slots: ['clothing'],
  optional_slots: [], questions: [
    { key: 'dress_code', label: 'Is there a dress code?', options: ['festive_traditional'], required: false },
    { key: 'weather', label: 'What is the weather likely to be?', options: ['hot'], required: false },
  ], notes: 'A wedding.',
};

const look = {
  id: 'look-7', run_id: 'run-1', variant: 'recommended', title: 'Wedding look', rank: 1,
  score: 0.8, confidence: 0.9, why_it_works: 'A confirmed look from your wardrobe.',
  weather_note: '', dress_code_note: '', preparation_steps: [], missing_information: [],
  factor_scores: {}, explanation_source: 'deterministic', status: 'active', saved: false, version: 1,
  slots: { clothing: [], shoes: [], accessories: [], perfume: [], hair: [], grooming: [] },
  owned_items: [], optional_additions: [], owned_item_count: 0, optional_addition_count: 0,
  unavailable_items: [],
  share: { title: 'Wedding look', includes_personal_data: false, text: 'Wedding look', note: '' },
  created_at: null,
};

const result = {
  status: 'ok', run_id: 'run-1', occasion: { title: 'Wedding' }, looks: [look],
  disclaimer: '', confirmed_item_count: 0, unconfirmed_draft_count: 0, missing_information: [],
  entitlement: { feature: 'style_occasion', period: '2030-09', included: 60, used: 1, remaining: 59, source: 'beta_grant' },
};

const event = {
  eventId: 'event-1', occasionKey: 'wedding', eventDate: '2030-09-12',
  eventTitle: 'Wedding', dressCode: 'black_tie', location: 'Hall',
};

beforeEach(() => {
  jest.clearAllMocks();
  mockGetOccasionTypes.mockResolvedValue({ occasions: [occasion] });
  mockStyleForOccasion.mockResolvedValue(result);
  mockSetEventReadyLook.mockResolvedValue({});
});

describe('Event Ready look picker', () => {
  it('carries the canonical event fields into the styling request and does not auto-run', async () => {
    render(<EventLookPicker event={event} onLinked={jest.fn()} onCancel={jest.fn()} />);
    await screen.findByLabelText('Choose a look for this event');
    expect(mockGetOccasionTypes).toHaveBeenCalled();
    expect(mockStyleForOccasion).not.toHaveBeenCalled();

    fireEvent.press(screen.getByLabelText('Build looks for this event'));
    await waitFor(() => expect(mockStyleForOccasion).toHaveBeenCalledTimes(1));
    expect(mockStyleForOccasion.mock.calls[0][0]).toEqual(expect.objectContaining({
      occasion_key: 'wedding', event_date: '2030-09-12', title: 'Wedding',
      dress_code: 'black_tie', location: 'Hall',
    }));
  });

  it('does not ask for a dress code the event already fixed', async () => {
    render(<EventLookPicker event={event} onLinked={jest.fn()} onCancel={jest.fn()} />);
    await screen.findByLabelText('Choose a look for this event');
    expect(screen.queryByText('Is there a dress code?')).toBeNull();
  });

  it('asks for a dress code when the event has none', async () => {
    render(<EventLookPicker event={{ ...event, dressCode: null }} onLinked={jest.fn()} onCancel={jest.fn()} />);
    await screen.findByLabelText('Choose a look for this event');
    expect(screen.getByText('Is there a dress code?')).toBeTruthy();
  });

  it('links a chosen look through the canonical API and tells Event Ready to refresh', async () => {
    const onLinked = jest.fn();
    render(<EventLookPicker event={event} onLinked={onLinked} onCancel={jest.fn()} />);
    fireEvent.press(await screen.findByLabelText('Build looks for this event'));
    fireEvent.press(await screen.findByLabelText('Use Wedding look for this event'));
    await waitFor(() => expect(mockSetEventReadyLook).toHaveBeenCalledWith('event-1', 'look-7'));
    await waitFor(() => expect(onLinked).toHaveBeenCalled());
  });

  it('keeps the looks on screen when Event Ready rejects the link', async () => {
    const onLinked = jest.fn();
    mockSetEventReadyLook.mockRejectedValue(new Error('look does not match event'));
    render(<EventLookPicker event={event} onLinked={onLinked} onCancel={jest.fn()} />);
    fireEvent.press(await screen.findByLabelText('Build looks for this event'));
    fireEvent.press(await screen.findByLabelText('Use Wedding look for this event'));
    await waitFor(() => expect(screen.getByText('Wedding look')).toBeTruthy());
    expect(onLinked).not.toHaveBeenCalled();
  });

  it('builds the request with the event overriding a contradicting answer', () => {
    const body = buildEventOccasionInput(occasion as any, { dress_code: 'festive_traditional' }, event);
    expect(body.dress_code).toBe('black_tie');
    expect(body.occasion_key).toBe('wedding');
  });
});
