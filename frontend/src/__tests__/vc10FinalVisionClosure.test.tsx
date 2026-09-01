import { existsSync } from 'fs';
import { join } from 'path';

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react-native';

import { EntryBrandTagline, EntryFeatures, EntryHero } from '../../app/index';
import ScanRedirect from '../../app/scan';
import StyleRedirect from '../../app/(tabs)/style';
import MyAppearanceRedirect from '../../app/my-appearance';
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

describe('Step 1 product-shell closure', () => {
  it('keeps Scan and Account as the only visible shell concepts', () => {
    expect(PRIMARY_TAB_LABELS).toEqual(['Scan', 'You']);
    expect(CATEGORY_META.beauty.label).toBe('Skin Care');
    expect(CATEGORY_META.hair.label).toBe('Hair Care');
  });

  it('renders product-intelligence entry language without appearance positioning', () => {
    render(<><EntryBrandTagline /><EntryHero /><EntryFeatures /></>);
    expect(screen.getByText('Scan a product.\nMake a clearer decision.')).toBeTruthy();
    expect(screen.getByText('SCAN · DECIDE · UNDERSTAND')).toBeTruthy();
    expect(screen.getByText('Scan a product')).toBeTruthy();
    expect(screen.getByText('See the decision')).toBeTruthy();
    expect(screen.getByText('Scan again')).toBeTruthy();
    expect(screen.queryByText('Fashion stylist')).toBeNull();
    expect(screen.queryByText('Wellness coach')).toBeNull();
  });

  it('renders Account without appearance or style controls', async () => {
    render(<ProfileScreen />);
    await waitFor(() => expect(screen.getByText('Your account')).toBeTruthy());
    expect(screen.getByText('ACCOUNT')).toBeTruthy();
    expect(screen.getByText('Account, privacy and notification controls.')).toBeTruthy();
    expect(screen.queryByLabelText('Open My Appearance')).toBeNull();
    expect(screen.queryByText('Style vibe')).toBeNull();
    expect(screen.getByLabelText('Open Memory')).toBeTruthy();
    expect(screen.getByLabelText('Open Notifications')).toBeTruthy();
    expect(screen.queryByText('Private beta member')).toBeNull();
    expect(screen.queryByText('Retake stylist quiz')).toBeNull();
  });

  it.each([
    ['scan', ScanRedirect, '/scan-product'],
    ['scan tab', ScanTabRedirect, '/scan-product'],
    ['style', StyleRedirect, '/scan-product'],
    ['my appearance', MyAppearanceRedirect, '/scan-product'],
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
