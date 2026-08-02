import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';
import { AccessibilityInfo } from 'react-native';

import {
  BenefitRow, BillingUnavailableNote, CheckoutNote, EntitlementRow, ErrorRecovery,
  FadeIn, LimitReached, OfferCard, PlanBadge, PrimaryButton, SubscriptionState,
  formatPrice, offerLabel, periodLabel,
} from '../components/billing/PaywallPieces';
import { EntitlementFeature, EntitlementSnapshot, Offer } from '../services/apiV2';

const offer = (overrides: Partial<Offer> = {}): Offer => ({
  key: 'plus_monthly', plan_key: 'plus', label: 'Plus, monthly',
  amount_inr: 399, currency: 'INR', period: 'monthly',
  compare_at_inr: null, saving_percent: null, valid_days: null,
  tagline: 'Every week planned, and your whole wardrobe actually working.',
  benefits: [
    { headline: 'Plan every week', detail: 'Monday to Sunday, around what is actually in your diary.' },
    { headline: 'Use your complete wardrobe', detail: 'Catalogue all of it, and get told what you already own.' },
  ],
  ...overrides,
});

const feature = (overrides: Partial<EntitlementFeature> = {}): EntitlementFeature => ({
  feature: 'shopping_evaluations', label: '"Should I buy this?" checks',
  limit: 3, used: 1, remaining: 2, plan_key: 'free', expires_on: '2026-08-31',
  ...overrides,
});

const snapshot = (overrides: Partial<EntitlementSnapshot> = {}): EntitlementSnapshot => ({
  plan_key: 'plus', plan_label: 'Plus', features: [feature()],
  subscription: {
    status: 'active', plan_key: 'plus', current_period_end: '2026-09-01',
    grace_until: null, cancel_at_period_end: false,
  },
  as_of: '2026-08-02', cache_seconds: 900, valid_until: '2026-08-02T12:15:00Z',
  offline_note: 'The app may rely on this for a short while without a connection.',
  ...overrides,
});

// --- Prices -----------------------------------------------------------------

describe('Prices', () => {
  it('formats rupees the way Indian readers expect', () => {
    expect(formatPrice(399)).toBe('₹399');
    expect(formatPrice(3499)).toBe('₹3,499');
    expect(formatPrice(120000)).toBe('₹1,20,000');
  });

  it('says what the period means in words', () => {
    expect(periodLabel('monthly')).toBe('a month');
    expect(periodLabel('yearly')).toBe('a year');
    expect(periodLabel('one_time')).toBe('one payment');
  });

  it('reads the whole offer aloud, price and terms together', () => {
    expect(offerLabel(offer())).toBe('Plus, monthly, ₹399 a month');
    expect(offerLabel(offer({ key: 'event_pass', label: 'Event Pass', amount_inr: 499, period: 'one_time', valid_days: 14 })))
      .toBe('Event Pass, ₹499 one payment, valid for 14 days');
  });
});

// --- The paywall sells outcomes ------------------------------------------------

describe('The paywall', () => {
  it('leads with the outcome, not the mechanism', () => {
    render(<BenefitRow benefit={{ headline: 'Plan every week', detail: 'Monday to Sunday.' }} />);
    expect(screen.getByText('Plan every week')).toBeTruthy();
  });

  it('never advertises AI volume', () => {
    render(<OfferCard offer={offer()} selected onSelect={jest.fn()} />);
    for (const banned of [/unlimited ai/i, /unlimited scans/i, /more tokens/i, /ai calls/i]) {
      expect(screen.queryByText(banned)).toBeNull();
    }
  });

  it('shows the price and reads it to a screen reader', () => {
    render(<OfferCard offer={offer()} selected onSelect={jest.fn()} />);
    expect(screen.getByText('₹399')).toBeTruthy();
    expect(screen.getByLabelText('Plus, monthly, ₹399 a month')).toBeTruthy();
  });

  it('shows a genuine saving against the monthly price', () => {
    render(<OfferCard
      offer={offer({ key: 'plus_yearly', label: 'Plus, yearly', amount_inr: 3499, period: 'yearly', compare_at_inr: 4788, saving_percent: 27 })}
      selected={false}
      onSelect={jest.fn()}
    />);
    expect(screen.getByLabelText('Saves 27 percent against paying monthly')).toBeTruthy();
  });

  it('says how long a pass lasts', () => {
    render(<OfferCard
      offer={offer({ key: 'event_pass', label: 'Event Pass', period: 'one_time', valid_days: 14 })}
      selected={false}
      onSelect={jest.fn()}
    />);
    expect(screen.getByText('Yours for 14 days')).toBeTruthy();
  });

  it('reads as a radio option to a screen reader', () => {
    render(<OfferCard offer={offer()} selected onSelect={jest.fn()} />);
    const option = screen.getByLabelText('Plus, monthly, ₹399 a month');
    expect(option.props.accessibilityRole).toBe('radio');
    expect(option.props.accessibilityState.selected).toBe(true);
  });

  it('can be selected', () => {
    const onSelect = jest.fn();
    render(<OfferCard offer={offer()} selected={false} onSelect={onSelect} />);
    fireEvent.press(screen.getByLabelText('Plus, monthly, ₹399 a month'));
    expect(onSelect).toHaveBeenCalled();
  });

  it('cannot be chosen while billing is switched off', () => {
    render(<OfferCard offer={offer()} selected={false} onSelect={jest.fn()} disabled />);
    const option = screen.getByLabelText('Plus, monthly, ₹399 a month');
    expect(option.props.accessibilityState.disabled).toBe(true);
  });
});

// --- Checkout honesty -------------------------------------------------------------

describe('Checkout', () => {
  it('never implies a closed payment sheet granted anything', () => {
    render(<CheckoutNote simulated={false} />);
    expect(screen.getByText(/not the moment the payment screen closes/)).toBeTruthy();
  });

  it('says outright when no real provider is connected', () => {
    render(<CheckoutNote simulated />);
    expect(screen.getByText(/nothing will be charged/)).toBeTruthy();
  });

  it('explains that there is nothing to pay for during the beta', () => {
    render(<BillingUnavailableNote message="GlamGenius is in a private beta, so there is nothing to pay for yet." />);
    expect(screen.getByLabelText('Nothing to pay for yet')).toBeTruthy();
  });

  it('a busy button cannot be pressed twice', () => {
    const onPress = jest.fn();
    render(<PrimaryButton label="Continue" onPress={onPress} busy />);
    const button = screen.getByLabelText('Continue');
    fireEvent.press(button);
    expect(onPress).not.toHaveBeenCalled();
    expect(button.props.accessibilityState.busy).toBe(true);
  });

  it('offers a way back from an error', () => {
    const onRetry = jest.fn();
    render(<ErrorRecovery message="We could not start that payment. Nothing has been charged." onRetry={onRetry} />);
    expect(screen.getByText(/Nothing has been charged/)).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Try again'));
    expect(onRetry).toHaveBeenCalled();
  });
});

// --- Entitlement state --------------------------------------------------------------

describe('Entitlements', () => {
  it('says what is left, in words', () => {
    render(<EntitlementRow feature={feature()} />);
    expect(screen.getByLabelText('"Should I buy this?" checks: 2 of 3 left')).toBeTruthy();
  });

  it('says "included" rather than "unlimited" for an unmetered feature', () => {
    render(<EntitlementRow feature={feature({ limit: null, remaining: null, label: 'One plan for today' })} />);
    expect(screen.getByText('Included')).toBeTruthy();
    expect(screen.queryByText(/unlimited/i)).toBeNull();
  });

  it('shows the plan you are on', () => {
    render(<PlanBadge planKey="plus" label="Plus" />);
    expect(screen.getByLabelText('Your plan: Plus')).toBeTruthy();
  });

  it('says a failed payment has not cut you off', () => {
    render(<SubscriptionState snapshot={snapshot({
      subscription: {
        status: 'grace', plan_key: 'plus', current_period_end: '2026-09-01',
        grace_until: '2026-08-05', cancel_at_period_end: false,
      },
    })} />);
    expect(screen.getByText(/keeps working while we retry/)).toBeTruthy();
  });

  it('says a cancelled plan still runs to the end of what you paid for', () => {
    render(<SubscriptionState snapshot={snapshot({
      subscription: {
        status: 'cancelled', plan_key: 'plus', current_period_end: '2026-09-01',
        grace_until: null, cancel_at_period_end: true,
      },
    })} />);
    expect(screen.getByText(/you keep everything until 2026-09-01/)).toBeTruthy();
  });

  it('shows nothing at all when there is no subscription', () => {
    const { toJSON } = render(<SubscriptionState snapshot={snapshot({ subscription: null })} />);
    expect(toJSON()).toBeNull();
  });

  it('a reached limit explains the outcome, not the model', () => {
    render(<LimitReached feature={feature({ remaining: 0, used: 3 })} onUpgrade={jest.fn()} />);
    expect(screen.getByText(/starts again next month/)).toBeTruthy();
    expect(screen.queryByText(/token/i)).toBeNull();
    expect(screen.queryByText(/quota/i)).toBeNull();
  });
});

// --- Reduced motion -----------------------------------------------------------------------

describe('Reduced motion', () => {
  it('is asked about before anything animates', async () => {
    const spy = jest.spyOn(AccessibilityInfo, 'isReduceMotionEnabled');
    render(<OfferCard offer={offer()} selected onSelect={jest.fn()} />);
    // The offer card itself does not animate; the FadeIn wrapper does, and the
    // hook it uses is what asks. Rendering it exercises that path.
    render(<FadeIn><EntitlementRow feature={feature()} /></FadeIn>);
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });
});
