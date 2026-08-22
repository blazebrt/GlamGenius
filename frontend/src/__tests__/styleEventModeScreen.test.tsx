/* eslint-disable import/first -- module mocks must be declared before screen imports. */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

const mockRouter = { push: jest.fn(), replace: jest.fn(), back: jest.fn() };
const mockParams: Record<string, string> = {
  eventReadyEventId: 'event-1', occasionKey: 'wedding', eventDate: '2030-09-12',
  eventTitle: 'Wedding', dressCode: 'black_tie', location: 'Hall',
};
const defaultParams = { ...mockParams };
const mockGetOccasionTypes = jest.fn();
const mockStyleForOccasion = jest.fn();
const mockSetEventReadyLook = jest.fn();

jest.mock('expo-router', () => ({
  useRouter: () => mockRouter,
  useLocalSearchParams: () => mockParams,
}));
jest.mock('../services/apiV2', () => {
  const actual = jest.requireActual('../services/apiV2');
  return {
    ...actual,
    getOccasionTypes: (...args: unknown[]) => mockGetOccasionTypes(...args),
    styleForOccasion: (...args: unknown[]) => mockStyleForOccasion(...args),
    setEventReadyLook: (...args: unknown[]) => mockSetEventReadyLook(...args),
  };
});

import StyleMeScreen from '../../app/style-me';

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
  unavailable_items: [], share: { title: 'Wedding look', includes_personal_data: false, text: 'Wedding look', note: '' }, created_at: null,
};

const result = {
  status: 'ok', run_id: 'run-1', occasion: { title: 'Wedding' }, looks: [look],
  disclaimer: '', confirmed_item_count: 0, unconfirmed_draft_count: 0, missing_information: [],
  entitlement: { feature: 'style_occasion', period: '2030-09', included: 60, used: 1, remaining: 59, source: 'beta_grant' },
};

beforeEach(() => {
  jest.clearAllMocks();
  Object.assign(mockParams, defaultParams);
  mockGetOccasionTypes.mockResolvedValue({ occasions: [occasion] });
  mockStyleForOccasion.mockResolvedValue(result);
  mockSetEventReadyLook.mockResolvedValue({});
});

describe('VC-03 Style Event Mode screen', () => {
  it('loads and preselects the event occasion, preserves canonical fields, and does not auto-run', async () => {
    render(<StyleMeScreen />);
    await screen.findByText('This event type is confirmed in Event Ready.');
    expect(mockGetOccasionTypes).toHaveBeenCalled();
    expect(mockStyleForOccasion).not.toHaveBeenCalled();
    expect(screen.queryByText('Is there a dress code?')).toBeNull();
    fireEvent.press(screen.getByLabelText('Build my Wedding looks'));
    await waitFor(() => expect(mockStyleForOccasion).toHaveBeenCalledTimes(1));
    expect(mockStyleForOccasion.mock.calls[0][0]).toEqual(expect.objectContaining({
      occasion_key: 'wedding', event_date: '2030-09-12', title: 'Wedding',
      dress_code: 'black_tie', location: 'Hall',
    }));
  });

  it('links a selected look through the canonical API and returns to exact Event Ready', async () => {
    render(<StyleMeScreen />);
    fireEvent.press(await screen.findByLabelText('Build my Wedding looks'));
    await screen.findByLabelText('Use Wedding look for this event');
    fireEvent.press(screen.getByLabelText('Use Wedding look for this event'));
    await waitFor(() => expect(mockSetEventReadyLook).toHaveBeenCalledWith('event-1', 'look-7'));
    expect(mockRouter.replace).toHaveBeenCalledWith({ pathname: '/event-ready', params: { eventId: 'event-1' } });
  });

  it('keeps the ordinary dress-code question when Event Ready has no dress code', async () => {
    mockParams.dressCode = '';
    render(<StyleMeScreen />);
    await screen.findByText('This event type is confirmed in Event Ready.');
    expect(screen.getByText('Is there a dress code?')).toBeTruthy();
  });

  it('keeps Style results visible when Event Ready rejects a look link and never calls purchase APIs', async () => {
    mockSetEventReadyLook.mockRejectedValue(new Error('look does not match event'));
    render(<StyleMeScreen />);
    fireEvent.press(await screen.findByLabelText('Build my Wedding looks'));
    fireEvent.press(await screen.findByLabelText('Use Wedding look for this event'));
    await waitFor(() => expect(screen.getByText('Wedding look')).toBeTruthy());
    expect(mockRouter.replace).not.toHaveBeenCalled();
  });
});
