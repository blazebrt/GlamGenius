import React from 'react';
import { render, screen } from '@testing-library/react-native';

import { PurchaseMemoryCard } from '../components/shopping/PurchaseMemoryCard';
import { PurchaseGuard } from '../services/apiV2';

const guard = (state: PurchaseGuard['guard_state']): PurchaseGuard => ({
  purchase_guard_version: 'step-9a-v2',
  candidate_id: 'candidate-1',
  identity: { version: 'step-9a-v2', state: 'exact', fingerprint: 'not-rendered' },
  history_coverage: { state: 'step_9a_events_only', legacy_current_decisions_included: false },
  prior_consideration_count: 1,
  most_recent: {
    id: 'event-1', candidate_id: 'candidate-1', category: 'beauty', strategy: 'care_purchase',
    candidate_display_name: 'Cleanser', identity: { version: 'step-9a-v2', state: 'exact', fingerprint: 'not-rendered' },
    recommendation_at_decision: { verdict: 'wait', version: 'v1', fingerprint: null },
    decision: 'waiting', followed_recommendation: true, occurred_at: '2026-09-01T00:00:00Z',
  },
  guard_state: state,
  owned_redundancy: null,
});

describe('PurchaseMemoryCard', () => {
  it.each([
    ['exact_prior_bought', 'You previously marked this exact product as bought.'],
    ['exact_prior_waiting', 'Last time, you chose to wait.'],
    ['exact_prior_skipped', 'Last time, you chose to skip this.'],
    ['exact_prior_consideration', 'You have considered this exact product before.'],
  ] as const)('renders the exact prior decision for %s', (state, copy) => {
    render(<PurchaseMemoryCard guard={guard(state)} />);
    expect(screen.getByText(copy)).toBeTruthy();
    expect(screen.getByLabelText(`Purchase memory. ${copy}`)).toBeTruthy();
    expect(screen.queryByText('not-rendered')).toBeNull();
  });

  it('keeps incomplete history and insufficient identity separate from a prior-decision claim', () => {
    const { rerender } = render(<PurchaseMemoryCard guard={guard('historical_context_incomplete')} />);
    expect(screen.getByText('Some older purchase history may not be available here.')).toBeTruthy();
    rerender(<PurchaseMemoryCard guard={guard('identity_insufficient')} />);
    expect(screen.getByText('Confirm the product details to compare it with your purchase memory.')).toBeTruthy();
    expect(screen.queryByText(/previously marked/i)).toBeNull();
  });

  it('renders nothing when Step 9A has no prior event', () => {
    const { toJSON } = render(<PurchaseMemoryCard guard={guard('no_step9a_prior_event')} />);
    expect(toJSON()).toBeNull();
  });

  it('renders a deterministic count and date without exposing internal guard data', () => {
    const value = guard('exact_prior_waiting');
    value.prior_consideration_count = 3;
    render(<PurchaseMemoryCard guard={value} />);
    expect(screen.getByText('You have considered this exact product 3 times.')).toBeTruthy();
    expect(screen.getByText('Last decision: 1 Sept 2026')).toBeTruthy();
    expect(screen.queryByText(/fingerprint|step-9a-v2|event-1|care_purchase|followed/i)).toBeNull();
    expect(screen.queryByText(/own this|already owned|already in your inventory/i)).toBeNull();
  });
});
