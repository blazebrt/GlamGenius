import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import TodayScreen from '../../app/(tabs)/today';
import * as apiV2 from '../services/apiV2';

jest.mock('expo-router', () => {
  return {
    useRouter: () => ({ back: jest.fn(), push: jest.fn() }),
    useFocusEffect: jest.fn((cb) => {
      const react = jest.requireActual('react');
      return react.useEffect(cb, []);
    }),
  };
});

jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 10, bottom: 10 }),
}));

jest.mock('../services/apiV2', () => ({
  getToday: jest.fn(),
  completePlanAction: jest.fn(),
  getTodayAgenda: jest.fn().mockResolvedValue({ agenda_version: 'vc-09-v1', generated_for: '2026-08-07', timezone: 'Asia/Kolkata', items: [] }),
  getRoutinesToday: jest.fn(),
  getPerfumeRecommendation: jest.fn(),
  getNutritionSuggestions: jest.fn(),
}));

describe('Today Screen', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (apiV2.getRoutinesToday as jest.Mock).mockResolvedValue({ routines: [] });
    (apiV2.getPerfumeRecommendation as jest.Mock).mockResolvedValue({ recommendations: [] });
    (apiV2.getNutritionSuggestions as jest.Mock).mockResolvedValue({ enabled: false, suggestions: [] });
    (apiV2.getTodayAgenda as jest.Mock).mockResolvedValue({ agenda_version: 'vc-09-v1', generated_for: '2026-08-07', timezone: 'Asia/Kolkata', items: [] });
  });

  it('renders TodayScreen safely with a mocked valid plan to ensure it does not crash', async () => {
    const mockedGetToday = apiV2.getToday as jest.Mock;
    mockedGetToday.mockResolvedValue({
      plan_date: '2026-08-07',
      weekday: 'Friday',
      status: 'ready',
      headline: 'A mocked plan',
      primary: [],
      optional_modules: [],
      missing_information: [],
      confidence: 'high',
      outfit: null,
      disclaimer: 'test',
    });

    (apiV2.getRoutinesToday as jest.Mock).mockResolvedValue({ routines: [] });
    (apiV2.getPerfumeRecommendation as jest.Mock).mockResolvedValue({ recommendations: [] });
    (apiV2.getNutritionSuggestions as jest.Mock).mockResolvedValue({ enabled: false, suggestions: [] });

    render(<TodayScreen />);
    
    // As long as it renders the header or something without crashing
    await waitFor(() => {
      expect(screen.getByText(/A mocked plan/i)).toBeTruthy();
    });
  });

  it('shows an agenda today action once while preserving unrelated plan actions', async () => {
    (apiV2.getToday as jest.Mock).mockResolvedValue({
      plan_date: '2026-08-07', weekday: 'Friday', status: 'ready', headline: 'A mocked plan',
      primary: [
        { id: 'agenda-action', module: 'care', action_type: 'reminder', title: 'Hydrate your skin', body: 'Agenda copy', priority: 10, relevance: '', completed: false, completed_at: null, inventory_item_id: null },
        { id: 'unrelated-action', module: 'hydration', action_type: 'reminder', title: 'Carry water', body: 'Keep water nearby', priority: 20, relevance: '', completed: false, completed_at: null, inventory_item_id: null },
      ],
      optional_modules: [], missing_information: [], confidence: 'high', outfit: null, disclaimer: 'test',
    });
    (apiV2.getTodayAgenda as jest.Mock).mockResolvedValue({
      agenda_version: 'vc-09-v1', generated_for: '2026-08-07', timezone: 'Asia/Kolkata',
      items: [{ key: 'today:agenda-action', source_kind: 'today_action', source_action_id: 'agenda-action', title: 'Hydrate your skin', body: 'Agenda copy', destination: '/(tabs)/care', destination_params: {} }],
    });

    render(<TodayScreen />);
    await waitFor(() => expect(screen.getAllByText('Hydrate your skin')).toHaveLength(1));
    expect(screen.getByText('Carry water')).toBeTruthy();
    expect(screen.getByLabelText('Next up')).toBeTruthy();
  });

  it('keeps the original Today plan when agenda loading fails', async () => {
    (apiV2.getToday as jest.Mock).mockResolvedValue({
      plan_date: '2026-08-07', weekday: 'Friday', status: 'ready', headline: 'A mocked plan',
      primary: [], optional_modules: [], missing_information: [], confidence: 'high', outfit: null, disclaimer: 'test',
    });
    (apiV2.getTodayAgenda as jest.Mock).mockRejectedValue(new Error('agenda unavailable'));

    render(<TodayScreen />);
    await waitFor(() => expect(screen.getByText('A mocked plan')).toBeTruthy());
    expect(screen.queryByLabelText('Next up')).toBeNull();
  });

  it('completes an agenda Today action through the canonical Today endpoint', async () => {
    (apiV2.getToday as jest.Mock).mockResolvedValue({
      plan_date: '2026-08-07', weekday: 'Friday', status: 'ready', headline: 'A mocked plan',
      primary: [{ id: 'agenda-action', module: 'care', action_type: 'reminder', title: 'Hydrate your skin', body: 'Agenda copy', priority: 10, relevance: '', completed: false, completed_at: null, inventory_item_id: null }],
      optional_modules: [], missing_information: [], confidence: 'high', outfit: null, disclaimer: 'test',
    });
    (apiV2.getTodayAgenda as jest.Mock)
      .mockResolvedValueOnce({ agenda_version: 'vc-09-v1', generated_for: '2026-08-07', timezone: 'Asia/Kolkata', items: [{ key: 'today:agenda-action', source_kind: 'today_action', source_action_id: 'agenda-action', title: 'Hydrate your skin', body: 'Agenda copy', destination: '/(tabs)/care', destination_params: {} }] })
      .mockResolvedValueOnce({ agenda_version: 'vc-09-v1', generated_for: '2026-08-07', timezone: 'Asia/Kolkata', items: [] });
    (apiV2.completePlanAction as jest.Mock).mockResolvedValue({
      plan_date: '2026-08-07', weekday: 'Friday', status: 'ready', headline: 'A mocked plan',
      primary: [], optional_modules: [], missing_information: [], confidence: 'high', outfit: null, disclaimer: 'test',
    });
    render(<TodayScreen />);
    const done = await screen.findByRole('button', { name: 'Complete Hydrate your skin' });
    fireEvent.press(done);
    await waitFor(() => expect(apiV2.completePlanAction).toHaveBeenCalledWith('agenda-action', true));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Complete Hydrate your skin' })).toBeNull());
  });
});
