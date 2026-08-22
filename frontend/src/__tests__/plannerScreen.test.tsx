/* eslint-disable import/first -- module mocks must be declared before screen imports. */
import React from 'react';
import { RefreshControl } from 'react-native';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

const mockRouter = { push: jest.fn() };
const mockGetWeek = jest.fn();
const mockGetUpcomingEvents = jest.fn();

jest.mock('expo-router', () => {
  const ReactActual = jest.requireActual<typeof React>('react');
  return {
    useRouter: () => mockRouter,
    useFocusEffect: (callback: () => void) => ReactActual.useEffect(callback, [callback]),
  };
});
jest.mock('../services/apiV2', () => {
  const actual = jest.requireActual('../services/apiV2');
  return {
    ...actual,
    getWeek: (...args: unknown[]) => mockGetWeek(...args),
    getUpcomingEvents: (...args: unknown[]) => mockGetUpcomingEvents(...args),
  };
});

import PlannerScreen from '../../app/(tabs)/planner';

const emptyWeek = { week_start: '2030-09-09', status: 'not_generated', version: 1, days: [], repetition: { repeated_items: [], note: '' }, laundry: [] };

beforeEach(() => {
  jest.clearAllMocks();
  mockGetWeek.mockResolvedValue(emptyWeek);
  mockGetUpcomingEvents.mockRejectedValue(new Error('offline'));
});

describe('VC-03 Planner screen event loading', () => {
  it('keeps the weekly planner visible when upcoming events fail, with retry and shared pull refresh', async () => {
    render(<PlannerScreen />);
    await waitFor(() => expect(screen.getByText('Your week')).toBeTruthy());
    expect(screen.getByText('We could not load upcoming events right now.')).toBeTruthy();
    expect(screen.queryByText('No important events here yet.')).toBeNull();
    fireEvent.press(screen.getByLabelText('Retry upcoming events'));
    await waitFor(() => expect(mockGetUpcomingEvents).toHaveBeenCalledTimes(2));

    const refresh = screen.UNSAFE_getByType(RefreshControl);
    refresh.props.onRefresh();
    await waitFor(() => expect(mockGetWeek).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(mockGetUpcomingEvents).toHaveBeenCalledTimes(3));
  });
});
