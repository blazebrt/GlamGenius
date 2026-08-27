import React from 'react';
import { render, screen, waitFor } from '@testing-library/react-native';

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
  getTodayAgenda: jest.fn().mockResolvedValue({ agenda_version: 'vc-09-v1', generated_for: '2026-08-07', timezone: 'Asia/Kolkata', items: [] }),
  getRoutinesToday: jest.fn(),
  getPerfumeRecommendation: jest.fn(),
  getNutritionSuggestions: jest.fn(),
}));

describe('Today Screen', () => {
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
});
