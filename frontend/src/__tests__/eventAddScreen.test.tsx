/* eslint-disable import/first -- module mocks must be declared before screen imports. */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

const mockRouter = { replace: jest.fn(), back: jest.fn() };
const mockAddCalendarEvent = jest.fn();
const mockGetOccasionTypes = jest.fn();

jest.mock('expo-router', () => ({ useRouter: () => mockRouter }));
jest.mock('../services/apiV2', () => {
  const actual = jest.requireActual('../services/apiV2');
  return {
    ...actual,
    addCalendarEvent: (...args: unknown[]) => mockAddCalendarEvent(...args),
    getOccasionTypes: (...args: unknown[]) => mockGetOccasionTypes(...args),
  };
});

import EventAddScreen from '../../app/event-add';

const occasions = [{
  key: 'wedding', label: 'Wedding', formality: 5, dress_codes: ['black_tie'],
  default_dress_code: 'black_tie', default_setting: 'indoor', required_slots: ['clothing'],
  optional_slots: [], questions: [], notes: 'A wedding.',
}];

beforeEach(() => {
  jest.clearAllMocks();
  mockGetOccasionTypes.mockResolvedValue({ occasions });
});

const fillValid = () => {
  fireEvent.changeText(screen.getByLabelText('Event title'), 'Wedding');
  fireEvent.changeText(screen.getByLabelText('Event date'), '2030-09-12');
  fireEvent.changeText(screen.getByLabelText('Event time'), '18:30');
};

describe('VC-03 Event Add screen', () => {
  it('requires a title, a real calendar date, and a valid time', async () => {
    render(<EventAddScreen />);
    await screen.findByLabelText('Event title');
    fireEvent.press(screen.getByLabelText('Add event'));
    expect(screen.getByText('Add a title for this event.')).toBeTruthy();
    fireEvent.changeText(screen.getByLabelText('Event title'), 'Wedding');
    fireEvent.changeText(screen.getByLabelText('Event date'), '2030-02-30');
    fireEvent.press(screen.getByLabelText('Add event'));
    expect(screen.getByText('That date or time is not valid.')).toBeTruthy();
    fireEvent.changeText(screen.getByLabelText('Event date'), '2030-09-12');
    fireEvent.changeText(screen.getByLabelText('Event time'), '25:80');
    fireEvent.press(screen.getByLabelText('Add event'));
    expect(screen.getByText('Use a time like 18:30.')).toBeTruthy();
    expect(mockAddCalendarEvent).not.toHaveBeenCalled();
  });

  it('sends canonical occasion and optional fields without account_id', async () => {
    mockAddCalendarEvent.mockResolvedValue({ event: { id: 'event-99' } });
    render(<EventAddScreen />);
    await screen.findByLabelText('Event title');
    fillValid();
    fireEvent.press(screen.getByLabelText('Event type Wedding'));
    fireEvent.press(screen.getByLabelText('Dress code black_tie'));
    fireEvent.changeText(screen.getByLabelText('Event location'), 'Hall');
    fireEvent.press(screen.getByLabelText('Add event'));
    await waitFor(() => expect(mockAddCalendarEvent).toHaveBeenCalledTimes(1));
    const body = mockAddCalendarEvent.mock.calls[0][0];
    expect(body).toEqual(expect.objectContaining({ occasion_key: 'wedding', dress_code_hint: 'black_tie', location: 'Hall', all_day: false }));
    expect(body.starts_at).toBe(new Date('2030-09-12T18:30:00').toISOString());
    expect(body).not.toHaveProperty('account_id');
    expect(mockRouter.replace).toHaveBeenCalledWith({ pathname: '/event-ready', params: { eventId: 'event-99' } });
  });

  it('omits occasion when none is selected and blocks duplicate submits', async () => {
    let resolveSubmit: ((value: { event: { id: string } }) => void) | undefined;
    mockAddCalendarEvent.mockReturnValue(new Promise((resolve) => { resolveSubmit = resolve; }));
    render(<EventAddScreen />);
    await screen.findByLabelText('Event title');
    fillValid();
    fireEvent.press(screen.getByLabelText('Add event'));
    fireEvent.press(screen.getByLabelText('Add event'));
    expect(mockAddCalendarEvent).toHaveBeenCalledTimes(1);
    expect(mockAddCalendarEvent.mock.calls[0][0]).not.toHaveProperty('occasion_key');
    resolveSubmit?.({ event: { id: 'event-100' } });
    await waitFor(() => expect(mockRouter.replace).toHaveBeenCalled());
  });

  it('keeps typed values after a failed submission', async () => {
    mockAddCalendarEvent.mockRejectedValue({ response: { data: { detail: { message: 'Could not save event.' } } } });
    render(<EventAddScreen />);
    await screen.findByLabelText('Event title');
    fillValid();
    fireEvent.changeText(screen.getByLabelText('Event location'), 'Hall');
    fireEvent.press(screen.getByLabelText('Add event'));
    await waitFor(() => expect(screen.getByText('Could not save event.')).toBeTruthy());
    expect(screen.getByLabelText('Event title').props.value).toBe('Wedding');
    expect(screen.getByLabelText('Event date').props.value).toBe('2030-09-12');
    expect(screen.getByLabelText('Event time').props.value).toBe('18:30');
    expect(screen.getByLabelText('Event location').props.value).toBe('Hall');
  });
});
