/**
 * Where the manager sits on the Shelf screen, and what happens when it cannot
 * answer.
 *
 * Three placement rules, each of which a later refactor could quietly break:
 *
 * * it is inside the existing Skin & Hair Care tab, not a new tab of its own;
 * * it is above the counts, because one decision matters more than three
 *   numbers;
 * * it is not on the Perfumes or Supplements tabs, which are out of its scope.
 *
 * And the isolation rule: a manager that cannot answer shows nothing, and the
 * rest of the shelf is exactly as it was.
 */
// Jest refuses a mock factory that reaches an out-of-scope name unless the name
// is prefixed with "mock". The focus hook is imported under that name for that
// reason, in the same statement as the default import.
import React, { useEffect as mockUseEffect } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import ShelfScreen from '../../app/shelf';
import * as apiV2 from '../services/apiV2';
import { S } from '../strings/shelfManager';

jest.mock('../services/apiV2');

jest.mock('expo-router', () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn(), back: jest.fn(), canGoBack: () => true }),
  useFocusEffect: (callback: () => void) => {
    // The real hook runs the callback when the screen gains focus. A mounted
    // screen in a test process is a focused one.
    mockUseEffect(() => { callback(); }, [callback]);
  },
}));

jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mocked = apiV2 as jest.Mocked<typeof apiV2>;

const summary: apiV2.ShelfSummary = {
  categories: {},
  counts: { products: 4, avoid: 0, caution: 1, needs_attention: 1, awaiting_confirmation: 0, drafts: 0 },
  needs_attention: [],
  draft_note: null,
};

const managerQueue: apiV2.ShelfManagerQueue = {
  contract_version: 'step-10b-v1',
  primary: {
    decision_key: 'rule.product_expired:item:item-1',
    decision_fingerprint: 'a'.repeat(64),
    kind: 'finding',
    category: 'beauty',
    rule_id: 'rule.product_expired',
    severity: 'caution',
    decision: 'Pause this until you replace it.',
    reason: 'Expired Cleanser is past its date',
    evidence_note: 'Computed from the dates you recorded.',
    item_ids: ['item-1'],
    slot: 'cleanser',
    action: { kind: 'pause_product', label: 'Pause it', inventory_item_id: 'item-1', mutates: true },
    override: { label: 'Not now', choice: 'override' },
  },
  remaining_count: 1,
  counts: { active: 2, overridden: 0, give_back: 0 },
  message: null,
  disclaimer: 'A routine built from products you told us you own.',
};

beforeEach(() => {
  jest.clearAllMocks();
  mocked.getShelfSummary.mockResolvedValue(summary);
  mocked.getPerfumeRecommendation.mockResolvedValue({
    recommendations: [], considered: 0, missing_information: [], note: '', message: null,
  });
  mocked.getSupplementsSummary.mockResolvedValue({
    supplements: [], we_do_not: [], disclaimer: '', message: null,
  } as never);
  mocked.getShelfManager.mockResolvedValue(managerQueue);
});

function textOrder(): string[] {
  const found: string[] = [];
  const walk = (node: unknown): void => {
    if (typeof node === 'string') { found.push(node); return; }
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (node && typeof node === 'object' && 'children' in node) {
      walk((node as { children: unknown }).children);
    }
  };
  walk(screen.toJSON());
  return found;
}

describe('the manager on the Shelf screen', () => {
  it('appears inside the Skin & Hair Care tab', async () => {
    render(<ShelfScreen />);

    expect(await screen.findByText(S.heading)).toBeTruthy();
    expect(screen.getByText('Pause this until you replace it.')).toBeTruthy();
  });

  it('does not add a tab of its own', async () => {
    render(<ShelfScreen />);
    await screen.findByText(S.heading);

    const tabs = screen.getAllByRole('tab').map((row) => row.props.accessibilityLabel);
    expect(tabs).toEqual(['Skin & Hair Care', 'Perfumes', 'Supplements']);
  });

  it('sits above the counts', async () => {
    render(<ShelfScreen />);
    await screen.findByText(S.heading);

    const order = textOrder();
    expect(order.indexOf(S.heading)).toBeGreaterThanOrEqual(0);
    expect(order.indexOf('products')).toBeGreaterThan(order.indexOf(S.heading));
    expect(order.indexOf('need attention')).toBeGreaterThan(order.indexOf(S.heading));
  });

  it('is not shown on the Perfumes or Supplements tabs', async () => {
    render(<ShelfScreen />);
    await screen.findByText(S.heading);

    fireEvent.press(screen.getByLabelText('Perfumes'));
    expect(screen.queryByText(S.heading)).toBeNull();

    fireEvent.press(screen.getByLabelText('Supplements'));
    expect(screen.queryByText(S.heading)).toBeNull();

    // Coming back remounts the card, so it reads the queue again rather than
    // showing whatever it had before the person went elsewhere.
    fireEvent.press(screen.getByLabelText('Skin & Hair Care'));
    expect(await screen.findByText(S.heading)).toBeTruthy();
  });
});

describe('when the manager cannot answer', () => {
  it('leaves the rest of the shelf working', async () => {
    mocked.getShelfManager.mockRejectedValue(new Error('network'));
    render(<ShelfScreen />);

    await waitFor(() => expect(mocked.getShelfManager).toHaveBeenCalled());
    expect(screen.queryByText(S.heading)).toBeNull();
    // The counts, the re-read button and the routines link are all still there.
    expect(screen.getByText('products')).toBeTruthy();
    expect(screen.getByLabelText('Read my labels again')).toBeTruthy();
    expect(screen.getByLabelText('Open routines and improvements')).toBeTruthy();
  });

  it('does not stop the shelf loading when the shelf itself is the one failing', async () => {
    mocked.getShelfSummary.mockRejectedValue(new Error('network'));
    render(<ShelfScreen />);

    expect(await screen.findByText(S.heading)).toBeTruthy();
  });
});

describe('after the manager acts', () => {
  it('asks the shelf to re-read itself', async () => {
    mocked.respondToShelfManager.mockResolvedValue({
      ...managerQueue, primary: null, remaining_count: 0,
      counts: { active: 0, overridden: 0, give_back: 0 },
      applied: {
        choice: 'accepted', action_kind: 'pause_product', action_applied: true, replayed: false,
      },
    });
    render(<ShelfScreen />);
    await screen.findByText(S.heading);
    const before = mocked.getShelfSummary.mock.calls.length;

    fireEvent.press(screen.getByLabelText('Pause it'));

    await waitFor(() => expect(mocked.getShelfSummary.mock.calls.length).toBe(before + 1));
  });
});
