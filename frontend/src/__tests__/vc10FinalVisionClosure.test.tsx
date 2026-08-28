import React from 'react';
import { render, screen, waitFor } from '@testing-library/react-native';

import { EntryBrandTagline, EntryFeatures, EntryHero } from '../../app/index';
import GetAdviceRedirect from '../../app/get-advice';
import RecommendationsRedirect from '../../app/recommendations';
import ScanRedirect from '../../app/scan';
import StyleQuizRedirect from '../../app/style-quiz';
import HistoryRedirect from '../../app/(tabs)/history';
import ScanTabRedirect from '../../app/(tabs)/scan-tab';
import ProfileScreen from '../../app/(tabs)/profile';
import { CATEGORY_META } from '../components/inventory/InventoryPieces';
import { PRIMARY_TAB_LABELS } from '../navigation/finalIA';

jest.mock('../store/userStore', () => {
  const state = {
    userId: null,
    user: null,
    fetchUser: jest.fn().mockResolvedValue(undefined),
    updateUser: jest.fn().mockResolvedValue(undefined),
    logout: jest.fn().mockResolvedValue(undefined),
  };
  const useUserStore = () => state;
  useUserStore.getState = () => state;
  return { useUserStore };
});

jest.mock('../services/apiV2', () => ({
  getPrivacyAccountDeletionStatus: jest.fn().mockResolvedValue(null),
  requestPrivacyAccountDeletion: jest.fn(),
  cancelPrivacyAccountDeletion: jest.fn(),
}));

jest.mock('react-native-reanimated', () => {
  const React = require('react');
  const { View } = require('react-native');
  const AnimatedView = ({ children, ...props }: any) => React.createElement(View, props, children);
  const entering = { delay: () => entering };
  return { default: { View: AnimatedView }, View: AnimatedView, FadeIn: entering, FadeInDown: entering, FadeInUp: entering };
});

describe('VC-10 final vision closure', () => {
  it('keeps the five-tab appearance OS and customer category terminology', () => {
    expect(PRIMARY_TAB_LABELS).toEqual(['Today', 'Style', 'Care', 'Plan', 'You']);
    expect(CATEGORY_META.beauty.label).toBe('Skin Care');
    expect(CATEGORY_META.hair.label).toBe('Hair Care');
  });

  it('renders the final entry language without the retired coach surface', () => {
    render(<><EntryBrandTagline /><EntryHero /><EntryFeatures /></>);
    expect(screen.getByText('Know what to wear.\nKnow what needs attention.')).toBeTruthy();
    expect(screen.getByText('STYLE · CARE · PLAN')).toBeTruthy();
    expect(screen.getByText('Today, decided')).toBeTruthy();
    expect(screen.getByText('Style from your wardrobe')).toBeTruthy();
    expect(screen.getByText('Care and planning together')).toBeTruthy();
    expect(screen.queryByText('Fashion stylist')).toBeNull();
    expect(screen.queryByText('Wellness coach')).toBeNull();
  });

  it('renders the You profile without beta membership or a quiz retake', async () => {
    render(<ProfileScreen />);
    await waitFor(() => expect(screen.getByText('Your profile')).toBeTruthy());
    expect(screen.getByText('YOU')).toBeTruthy();
    expect(screen.getByText('Preferences, appearance context, progress, memory, privacy and account settings.')).toBeTruthy();
    expect(screen.getByLabelText('Open My Appearance')).toBeTruthy();
    expect(screen.getByLabelText('Open Progress')).toBeTruthy();
    expect(screen.getByLabelText('Open Memory')).toBeTruthy();
    expect(screen.getByLabelText('Open Notifications')).toBeTruthy();
    expect(screen.queryByText('Private beta member')).toBeNull();
    expect(screen.queryByText('Retake stylist quiz')).toBeNull();
  });

  it.each([
    ['style quiz', StyleQuizRedirect, '/my-appearance'],
    ['advice', GetAdviceRedirect, '/style-me'],
    ['recommendations', RecommendationsRedirect, '/style-me'],
    ['scan', ScanRedirect, '/my-appearance'],
    ['scan tab', ScanTabRedirect, '/my-appearance'],
    ['history', HistoryRedirect, '/progress'],
  ])('renders the retired %s route as a deterministic redirect', (_name, Route, destination) => {
    render(<Route />);
    expect(screen.getByTestId(`redirect:${destination}`)).toBeTruthy();
  });
});
