import React from 'react';
import { render, screen } from '@testing-library/react-native';

import TodayScreen from '../../app/(tabs)/today';
import * as apiV2 from '../services/apiV2';

jest.mock('expo-router', () => {
  return {
    useRouter: () => ({ back: jest.fn(), push: jest.fn() }),
    useFocusEffect: jest.fn((cb) => {
      const react = jest.requireActual('react');
      return react.useEffect(cb, []);
    }),
    Redirect: ({ href }: { href: string }) => <>{href}</>,
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

describe('retired Today route', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (apiV2.getRoutinesToday as jest.Mock).mockResolvedValue({ routines: [] });
    (apiV2.getPerfumeRecommendation as jest.Mock).mockResolvedValue({ recommendations: [] });
    (apiV2.getNutritionSuggestions as jest.Mock).mockResolvedValue({ enabled: false, suggestions: [] });
    (apiV2.getTodayAgenda as jest.Mock).mockResolvedValue({ agenda_version: 'vc-09-v1', generated_for: '2026-08-07', timezone: 'Asia/Kolkata', items: [] });
  });

  it('redirects legacy Today traffic to the scanner without loading plan UI', () => {
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
    expect(screen.toJSON()).toBe('/scan-product');
    expect(apiV2.getToday).not.toHaveBeenCalled();
  });
});
