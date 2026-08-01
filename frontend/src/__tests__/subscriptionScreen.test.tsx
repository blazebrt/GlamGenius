/**
 * The subscription screen is where the worst defect in the audit lived: it sold
 * a plan the backend refuses, then told the user their payment failed.
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react-native';
import SubscriptionScreen from '../../app/subscription';
import { useConfigStore } from '../store/configStore';
import { useUserStore } from '../store/userStore';
import { api } from '../services/api';

jest.mock('../services/api', () => ({
  api: { get: jest.fn(), post: jest.fn(), delete: jest.fn() },
  errorMessage: (_e: any, f: string) => f,
  isRateLimited: () => false,
  setUnauthorizedHandler: jest.fn(),
  loadAuthToken: jest.fn(() => Promise.resolve(null)),
  setAuthToken: jest.fn(),
  clearAuthToken: jest.fn(),
}));

const mockedGet = api.get as jest.MockedFunction<typeof api.get>;

const setBilling = (available: boolean) => {
  useConfigStore.setState({
    loaded: true,
    loading: false,
    error: null,
    config: {
      api_version: 'v2',
      billing: {
        subscriptions_available: available,
        invite_only: !available,
        beta_message: 'Your invite already gives you full access.',
      },
      analysis: {
        provider_configured: true,
        consent_required: false,
        consent_version: '2026-08-01',
      },
      media: {
        max_bytes: 1,
        allowed_types: [],
        face_photos_stored: false,
        storage_note: '',
      },
      features: {},
    },
  });
};

beforeEach(() => {
  jest.clearAllMocks();
  useUserStore.setState({ userId: 'user-1', user: null } as any);
  mockedGet.mockResolvedValue({
    data: {
      plan: 'plus',
      subscriptions_available: false,
      invite_scans_per_month: 50,
      scans_used_this_month: 3,
      plus_price_inr: 249,
      plus_yearly_inr: 1999,
    },
  } as any);
});

describe('when billing is unavailable', () => {
  beforeEach(() => setBilling(false));

  it('shows the private-beta view instead of plans', async () => {
    render(<SubscriptionScreen />);
    await waitFor(() => expect(screen.getByTestId('beta-membership')).toBeTruthy());
    expect(screen.queryByTestId('paid-plans')).toBeNull();
  });

  it('offers nothing to buy', async () => {
    render(<SubscriptionScreen />);
    await waitFor(() => expect(screen.getByTestId('beta-membership')).toBeTruthy());

    expect(screen.queryByTestId('buy-monthly')).toBeNull();
    expect(screen.queryByTestId('buy-yearly')).toBeNull();
    expect(screen.queryByText(/start monthly/i)).toBeNull();
    expect(screen.queryByText(/start yearly/i)).toBeNull();
    expect(screen.queryByText(/go plus/i)).toBeNull();
  });

  it('shows no prices', async () => {
    render(<SubscriptionScreen />);
    await waitFor(() => expect(screen.getByTestId('beta-membership')).toBeTruthy());

    expect(screen.queryByText(/₹249/)).toBeNull();
    expect(screen.queryByText(/₹1999/)).toBeNull();
  });

  it('does not promise unlimited checks', async () => {
    render(<SubscriptionScreen />);
    await waitFor(() => expect(screen.getByTestId('beta-membership')).toBeTruthy());

    // Invited members have a monthly allowance like everyone else.
    expect(screen.queryByText(/unlimited/i)).toBeNull();
  });

  it('shows the real allowance instead', async () => {
    render(<SubscriptionScreen />);
    await waitFor(() => expect(screen.getByText(/3 of 50 used/)).toBeTruthy());
  });
});

describe('when billing is available', () => {
  beforeEach(() => {
    setBilling(true);
    mockedGet.mockResolvedValue({
      data: {
        plan: 'free',
        subscriptions_available: true,
        free_scans_per_month: 1,
        scans_used_this_month: 0,
        plus_price_inr: 249,
        plus_yearly_inr: 1999,
      },
    } as any);
  });

  it('shows the plans again', async () => {
    render(<SubscriptionScreen />);
    await waitFor(() => expect(screen.getByTestId('paid-plans')).toBeTruthy());

    expect(screen.getByTestId('buy-monthly')).toBeTruthy();
    expect(screen.getByTestId('buy-yearly')).toBeTruthy();
  });
});

describe('when the two sources disagree', () => {
  it('takes the more cautious answer', async () => {
    // Config says billing is on, this user's status says it is off.
    setBilling(true);
    mockedGet.mockResolvedValue({
      data: { plan: 'plus', subscriptions_available: false, invite_scans_per_month: 50 },
    } as any);

    render(<SubscriptionScreen />);
    await waitFor(() => expect(screen.getByTestId('beta-membership')).toBeTruthy());
  });
});
