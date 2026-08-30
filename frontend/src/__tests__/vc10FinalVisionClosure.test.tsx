import { existsSync } from 'fs';
import { join } from 'path';

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react-native';

import { EntryBrandTagline, EntryFeatures, EntryHero } from '../../app/index';
import ScanRedirect from '../../app/scan';
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
    ['scan', ScanRedirect, '/my-appearance'],
    ['scan tab', ScanTabRedirect, '/my-appearance'],
    ['history', HistoryRedirect, '/progress'],
  ])('renders the retired %s route as a deterministic redirect', (_name, Route, destination) => {
    render(<Route />);
    expect(screen.getByTestId(`redirect:${destination}`)).toBeTruthy();
  });

  // The recommendation engine is retained for Event Ready, but the exception
  // covers backend modules only: no Style, quiz or colour-analysis SCREEN may
  // exist (PRODUCT_CONSTITUTION.md, master rule). Choosing an event look now
  // happens inside Event Ready. A redirect would itself be a way in, so none of
  // these routes exists at all.
  it.each(['style-me', 'get-advice', 'recommendations', 'style-quiz', '(tabs)/style-me-tab'])(
    'has no %s screen',
    (route) => {
      expect(existsSync(join(__dirname, '..', '..', 'app', `${route}.tsx`))).toBe(false);
    },
  );
});
