/* eslint-disable import/first -- module mocks must be declared before screen imports. */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

const mockRouter = { push: jest.fn(), replace: jest.fn(), back: jest.fn() };
const mockParams: { eventId?: string } = { eventId: 'event-1' };
const mockGetEventReady = jest.fn();
const mockGenerateEventReady = jest.fn();
const mockGetOccasionTypes = jest.fn();
const mockPatchCalendarEvent = jest.fn();
const mockSetEventReadyActionComplete = jest.fn();

jest.mock('expo-router', () => {
  const ReactActual = jest.requireActual<typeof React>('react');
  return {
    useRouter: () => mockRouter,
    useLocalSearchParams: () => mockParams,
    useFocusEffect: (callback: () => void) => ReactActual.useEffect(callback, [callback]),
  };
});

jest.mock('../services/apiV2', () => {
  const actual = jest.requireActual('../services/apiV2');
  return {
    ...actual,
    getEventReady: (...args: unknown[]) => mockGetEventReady(...args),
    generateEventReady: (...args: unknown[]) => mockGenerateEventReady(...args),
    getOccasionTypes: (...args: unknown[]) => mockGetOccasionTypes(...args),
    patchCalendarEvent: (...args: unknown[]) => mockPatchCalendarEvent(...args),
    setEventReadyActionComplete: (...args: unknown[]) => mockSetEventReadyActionComplete(...args),
  };
});

import EventReadyScreen from '../../app/event-ready';
import { EventReady, OccasionDefinition } from '../services/apiV2';

const event = (overrides: Record<string, unknown> = {}) => ({
  id: 'event-1', title: 'Wedding', starts_at: '2030-09-12T12:30:00Z',
  local_time: '18:00', local_date: '2030-09-12', ends_at: null, all_day: false,
  location: 'Hall', occasion_key: 'wedding', dress_code_hint: 'black_tie',
  inference_confidence: 1, user_confirmed: true, provider: 'manual',
  source: 'user_declared', status: 'active', ...overrides,
});

const occasion: OccasionDefinition = {
  key: 'wedding', label: 'Wedding', formality: 5, dress_codes: ['black_tie'],
  default_dress_code: 'black_tie', default_setting: 'indoor', required_slots: ['clothing'],
  optional_slots: [], questions: [], notes: 'A wedding.',
};

const ready = (overrides: Record<string, unknown> = {}): EventReady => ({
  event_ready_version: 'vc-02-v1', event: event() as any, status: 'preparing',
  countdown: { days_until: 20, event_local_date: '2030-09-12' },
  context: { weather: null, air_quality: null },
  style: { authority: 'style', status: 'needs_look', selected_look: null },
  care: null, timeline: [], readiness: { completed_actions: 0, total_actions: 0, all_done: true },
  missing_information: [], event_ready_fingerprint: 'fp', ...overrides,
} as EventReady);

beforeEach(() => {
  jest.clearAllMocks();
  mockParams.eventId = 'event-1';
  mockGetOccasionTypes.mockResolvedValue({ occasions: [occasion] });
  mockPatchCalendarEvent.mockResolvedValue(event({ user_confirmed: true, occasion_key: 'wedding' }));
  mockSetEventReadyActionComplete.mockResolvedValue(ready());
});

describe('VC-03 Event Ready screen', () => {
  it('loads the canonical read model on opening without generating', async () => {
    mockGetEventReady.mockResolvedValue(ready());
    render(<EventReadyScreen />);
    await waitFor(() => expect(mockGetEventReady).toHaveBeenCalledWith('event-1'));
    expect(mockGenerateEventReady).not.toHaveBeenCalled();
  });

  it('confirms an ungenerated event by PATCH then canonical GET, without generation', async () => {
    const initial = ready({ status: 'not_generated', event: event({ user_confirmed: false, occasion_key: null }), missing_information: ['event_confirmation'] });
    const canonical = ready({ status: 'not_generated', event: event({ user_confirmed: true, occasion_key: 'wedding' }) });
    mockGetEventReady.mockResolvedValueOnce(initial).mockResolvedValueOnce(canonical);
    render(<EventReadyScreen />);
    await waitFor(() => expect(screen.getByLabelText('Choose event type')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Choose event type'));
    await waitFor(() => expect(screen.getByLabelText('Confirm Wedding')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Confirm Wedding'));
    await waitFor(() => expect(mockGetEventReady).toHaveBeenCalledTimes(2));
    expect(mockPatchCalendarEvent).toHaveBeenCalledTimes(1);
    expect(mockGenerateEventReady).not.toHaveBeenCalled();
    expect(screen.queryByText('Confirm what the event is.')).toBeNull();
    expect(screen.getByLabelText('Prepare for this event')).toBeTruthy();
  });

  it('regenerates only an already-generated needs-confirmation plan', async () => {
    mockGetEventReady.mockResolvedValue(ready({ status: 'needs_confirmation', event: event({ user_confirmed: false }) }));
    mockGenerateEventReady.mockResolvedValue(ready({ status: 'preparing' }));
    render(<EventReadyScreen />);
    await waitFor(() => expect(screen.getByLabelText('Choose event type')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Choose event type'));
    fireEvent.press(await screen.findByLabelText('Confirm Wedding'));
    await waitFor(() => expect(mockGenerateEventReady).toHaveBeenCalledWith('event-1'));
    expect(mockGetEventReady).toHaveBeenCalledTimes(1);
  });

  it('allows one prepare request and replaces state with the canonical response', async () => {
    let resolveGeneration: ((value: EventReady) => void) | undefined;
    mockGetEventReady.mockResolvedValue(ready({ status: 'not_generated' }));
    mockGenerateEventReady.mockReturnValue(new Promise((resolve) => { resolveGeneration = resolve; }));
    render(<EventReadyScreen />);
    const prepare = await screen.findByLabelText('Prepare for this event');
    fireEvent.press(prepare);
    fireEvent.press(prepare);
    expect(mockGenerateEventReady).toHaveBeenCalledTimes(1);
    resolveGeneration?.(ready({ status: 'preparing' }));
    await waitFor(() => expect(screen.getByText('YOUR LOOK')).toBeTruthy());
  });

  it.each([
    ['generated', ready({ status: 'past', countdown: { days_until: -3, event_local_date: '2030-09-12' } })],
    ['ungenerated', ready({ status: 'not_generated', countdown: { days_until: -3, event_local_date: '2030-09-12' } })],
    ['unconfirmed ungenerated', ready({ status: 'not_generated', countdown: { days_until: -3, event_local_date: '2030-09-12' }, event: event({ user_confirmed: false, occasion_key: null }), missing_information: ['event_confirmation'] })],
  ])('keeps a %s past event read-only', async (_label, response) => {
    mockGetEventReady.mockResolvedValue(response);
    render(<EventReadyScreen />);
    await waitFor(() => expect(screen.getByText('This event has passed.')).toBeTruthy());
    expect(screen.getByText('Event passed')).toBeTruthy();
    expect(screen.queryByLabelText('Prepare for this event')).toBeNull();
    expect(screen.queryByLabelText('Choose event type')).toBeNull();
    expect(screen.queryByText('Confirm what the event is.')).toBeNull();
    expect(screen.queryByLabelText('Choose a look')).toBeNull();
  });

  it('keeps Event Ready product-focused and opens the Care destination', async () => {
    mockGetEventReady.mockResolvedValue(ready({ status: 'preparing', care: { hair_wash: { status: 'not_due' } } as any }));
    render(<EventReadyScreen />);
    await screen.findByText('YOUR LOOK');
    fireEvent.press(screen.getByLabelText('Scan a product'));
    expect(mockRouter.push).toHaveBeenCalledWith('/scan-product');
    fireEvent.press(screen.getByLabelText('Open Care'));
    expect(mockRouter.push).toHaveBeenCalledWith('/(tabs)/care');
  });

  it('uses exact server action ids, guards duplicates, and supports undo', async () => {
    const action = { id: 'server-action-7', action_key: 'style:choose_event_look', domain: 'style', timing: 'before_event', title: 'Choose your event look', body: 'Choose it.', relevance: '', inventory_item_id: null, completed: false, completed_at: null };
    mockGetEventReady.mockResolvedValue(ready({ timeline: [action] }));
    let resolveAction: ((value: EventReady) => void) | undefined;
    mockSetEventReadyActionComplete.mockReturnValue(new Promise((resolve) => { resolveAction = resolve; }));
    render(<EventReadyScreen />);
    const toggle = await screen.findByLabelText('Complete Choose your event look');
    fireEvent.press(toggle);
    fireEvent.press(toggle);
    expect(mockSetEventReadyActionComplete).toHaveBeenCalledTimes(1);
    expect(mockSetEventReadyActionComplete).toHaveBeenCalledWith('event-1', 'server-action-7', true);
    resolveAction?.(ready({ timeline: [{ ...action, completed: true }] }));
    await waitFor(() => expect(screen.getByLabelText('Undo Choose your event look')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Undo Choose your event look'));
    expect(mockSetEventReadyActionComplete).toHaveBeenLastCalledWith('event-1', 'server-action-7', false);
  });
});
