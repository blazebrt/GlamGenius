/**
 * Step 10B: the manager card shows one decision and answers honestly.
 *
 * What is held up here:
 *
 * * the card writes none of the words it shows — they arrive from the server;
 * * a failed manager call renders nothing and leaves the screen alone;
 * * the busy state belongs to a decision, not to the component, so the
 *   previous decision's spinner can never be committed under the next
 *   decision's words;
 * * one submission key per decision, reused on a retry and never shared;
 * * accepting a navigation records the answer and then navigates, and accepting
 *   a state change does not navigate at all;
 * * an override is one tap and says nothing about the person.
 */
import React, { Profiler } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import { ShelfManagerCard } from '../components/shelf/ShelfManagerCard';
import * as apiV2 from '../services/apiV2';
import { S } from '../strings/shelfManager';

jest.mock('../services/apiV2');

// The project-wide mock returns a fresh spy from every `useRouter()` call,
// which cannot be asserted on. Where the card sends somebody is part of the
// contract here, so this file keeps one stable spy.
const mockRouterPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({
    push: mockRouterPush, replace: jest.fn(), back: jest.fn(), canGoBack: () => true,
  }),
}));

const mocked = apiV2 as jest.Mocked<typeof apiV2>;

function decision(values: Partial<apiV2.ShelfManagerDecision> = {}): apiV2.ShelfManagerDecision {
  return {
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
    ...values,
  };
}

function queue(values: Partial<apiV2.ShelfManagerQueue> = {}): apiV2.ShelfManagerQueue {
  return {
    contract_version: 'step-10b-v1',
    primary: decision(),
    remaining_count: 2,
    counts: { active: 3, overridden: 0, give_back: 0 },
    message: null,
    disclaimer: 'A routine built from products you told us you own.',
    ...values,
  };
}

function response(values: Partial<apiV2.ShelfManagerResponse> = {}): apiV2.ShelfManagerResponse {
  return {
    ...queue({ primary: null, remaining_count: 0, counts: { active: 0, overridden: 0, give_back: 0 } }),
    applied: {
      choice: 'accepted', action_kind: 'pause_product', action_applied: true, replayed: false,
    },
    ...values,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

beforeEach(() => {
  jest.clearAllMocks();
  mocked.getShelfManager.mockResolvedValue(queue());
  mocked.respondToShelfManager.mockResolvedValue(response());
});

// ---------------------------------------------------------------------------
// What it shows
// ---------------------------------------------------------------------------

describe('what the manager card shows', () => {
  it('shows the decision, the reason, the action and the override from the server', async () => {
    render(<ShelfManagerCard />);

    expect(await screen.findByText('Pause this until you replace it.')).toBeTruthy();
    expect(screen.getByText('Expired Cleanser is past its date')).toBeTruthy();
    expect(screen.getByText('Pause it')).toBeTruthy();
    expect(screen.getByText('Not now')).toBeTruthy();
    expect(screen.getByText(S.heading)).toBeTruthy();
  });

  it('counts what is behind the one on screen', async () => {
    render(<ShelfManagerCard />);

    expect(await screen.findByText('2 more things after this')).toBeTruthy();
  });

  it('says "1 more thing" rather than "1 more things"', async () => {
    mocked.getShelfManager.mockResolvedValue(queue({ remaining_count: 1 }));
    render(<ShelfManagerCard />);

    expect(await screen.findByText('1 more thing after this')).toBeTruthy();
  });

  it('says nothing about what is behind when there is nothing behind', async () => {
    mocked.getShelfManager.mockResolvedValue(queue({ remaining_count: 0 }));
    render(<ShelfManagerCard />);

    await screen.findByText('Pause it');
    expect(screen.queryByText(/more thing/)).toBeNull();
  });

  it('renders nothing at all when the manager has decided nothing', async () => {
    mocked.getShelfManager.mockResolvedValue(queue({ primary: null }));
    const view = render(<ShelfManagerCard />);

    await waitFor(() => expect(mocked.getShelfManager).toHaveBeenCalled());
    expect(view.toJSON()).toBeNull();
  });

  it('uses the give-back heading and the give-back override for an offer back', async () => {
    mocked.getShelfManager.mockResolvedValue(queue({
      primary: decision({
        decision_key: 'manager.give_back:item:item-1',
        kind: 'give_back',
        decision: 'Bring it back.',
        reason: 'Expired Cleanser can return.',
        action: {
          kind: 'resume_product', label: 'Bring it back',
          inventory_item_id: 'item-1', mutates: true,
        },
        override: { label: 'Keep paused', choice: 'override' },
      }),
      counts: { active: 1, overridden: 0, give_back: 1 },
      remaining_count: 0,
    }));
    render(<ShelfManagerCard />);

    expect(await screen.findByText(S.headingGiveBack)).toBeTruthy();
    expect(screen.getByText('Expired Cleanser can return.')).toBeTruthy();
    expect(screen.getByText('Keep paused')).toBeTruthy();
    expect(screen.queryByText(S.heading)).toBeNull();
  });

  it('writes none of the words in the decision itself', async () => {
    const custom = queue({
      primary: decision({
        decision: 'A server sentence.',
        reason: 'A server reason.',
        action: { kind: 'open_routine', label: 'A server button', inventory_item_id: null, mutates: false },
        override: { label: 'A server override', choice: 'override' },
      }),
    });
    mocked.getShelfManager.mockResolvedValue(custom);
    render(<ShelfManagerCard />);

    expect(await screen.findByText('A server sentence.')).toBeTruthy();
    expect(screen.getByText('A server reason.')).toBeTruthy();
    expect(screen.getByText('A server button')).toBeTruthy();
    expect(screen.getByText('A server override')).toBeTruthy();
  });

  it('never shows a rule id, a fingerprint or an item id to the person', async () => {
    render(<ShelfManagerCard />);
    await screen.findByText('Pause it');

    const rendered = JSON.stringify(screen.toJSON());
    expect(rendered).not.toContain('rule.product_expired');
    expect(rendered).not.toContain('a'.repeat(64));
    expect(rendered).not.toContain('item-1');
  });
});

// ---------------------------------------------------------------------------
// It cannot break the screen
// ---------------------------------------------------------------------------

describe('when the manager cannot answer', () => {
  it('renders nothing and does not throw', async () => {
    mocked.getShelfManager.mockRejectedValue(new Error('network'));
    const view = render(<ShelfManagerCard />);

    await waitFor(() => expect(mocked.getShelfManager).toHaveBeenCalled());
    expect(view.toJSON()).toBeNull();
  });

  it('re-reads rather than guessing when an answer does not land', async () => {
    mocked.respondToShelfManager.mockRejectedValue(new Error('conflict'));
    const second = queue({
      primary: decision({ decision_key: 'rule.no_expiry_recorded:item:item-2', decision: 'Add the date on this pack.' }),
    });
    mocked.getShelfManager.mockResolvedValueOnce(queue()).mockResolvedValue(second);
    render(<ShelfManagerCard />);
    fireEvent.press(await screen.findByText('Pause it'));

    expect(await screen.findByText('Add the date on this pack.')).toBeTruthy();
    expect(mocked.getShelfManager).toHaveBeenCalledTimes(2);
  });
});

// ---------------------------------------------------------------------------
// Answering
// ---------------------------------------------------------------------------

describe('answering a decision', () => {
  it('sends exactly the four fields the server accepts', async () => {
    render(<ShelfManagerCard />);
    fireEvent.press(await screen.findByText('Pause it'));

    await waitFor(() => expect(mocked.respondToShelfManager).toHaveBeenCalled());
    const sent = mocked.respondToShelfManager.mock.calls[0][0];
    expect(Object.keys(sent).sort()).toEqual([
      'choice', 'client_mutation_id', 'decision_fingerprint', 'decision_key',
    ]);
    expect(sent.decision_key).toBe('rule.product_expired:item:item-1');
    expect(sent.decision_fingerprint).toBe('a'.repeat(64));
    expect(sent.choice).toBe('accept');
  });

  it('sends "override" for the second button and applies nothing locally', async () => {
    render(<ShelfManagerCard />);
    fireEvent.press(await screen.findByText('Not now'));

    await waitFor(() => expect(mocked.respondToShelfManager).toHaveBeenCalled());
    expect(mocked.respondToShelfManager.mock.calls[0][0].choice).toBe('override');
    expect(mockRouterPush).not.toHaveBeenCalled();
  });

  it('shows what the server compiled next rather than working it out itself', async () => {
    mocked.respondToShelfManager.mockResolvedValue(response({
      primary: decision({
        decision_key: 'rule.low_use_product:item:item-9',
        decision: 'Use this before replacing it.',
        reason: 'Unused Toner. Using them beats replacing them.',
      }),
      remaining_count: 0,
      counts: { active: 1, overridden: 1, give_back: 0 },
    }));
    render(<ShelfManagerCard />);
    fireEvent.press(await screen.findByText('Pause it'));

    expect(await screen.findByText('Use this before replacing it.')).toBeTruthy();
    // One read on mount and nothing more: the answer already carried the queue.
    expect(mocked.getShelfManager).toHaveBeenCalledTimes(1);
  });

  it('tells the screen something changed so the rest of it can refresh', async () => {
    const onChanged = jest.fn();
    render(<ShelfManagerCard onChanged={onChanged} />);
    fireEvent.press(await screen.findByText('Pause it'));

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  });

  it('does not navigate when the action changes stored state', async () => {
    render(<ShelfManagerCard />);
    fireEvent.press(await screen.findByText('Pause it'));

    await waitFor(() => expect(mocked.respondToShelfManager).toHaveBeenCalled());
    expect(mockRouterPush).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// A navigation is recorded, then taken
// ---------------------------------------------------------------------------

describe('a decision that only opens a screen', () => {
  const routeCases: readonly [apiV2.ShelfManagerActionKind, string | null, unknown][] = [
    ['record_date', 'item-1', '/inventory-item?id=item-1'],
    ['confirm_label', 'item-1', '/inventory-item?id=item-1'],
    ['open_inventory_item', 'item-1', '/inventory-item?id=item-1'],
    ['open_routine', null, '/improve'],
    ['add_owned_product', null, { pathname: '/inventory-add', params: { category: 'beauty' } }],
  ];

  it.each(routeCases)('records the answer and then opens the right screen for %s', async (
    kind, itemId, expected,
  ) => {
    mocked.getShelfManager.mockResolvedValue(queue({
      primary: decision({
        action: { kind, label: 'Go there', inventory_item_id: itemId, mutates: false },
      }),
    }));
    mocked.respondToShelfManager.mockResolvedValue(response({
      applied: { choice: 'accepted', action_kind: kind, action_applied: false, replayed: false },
    }));
    render(<ShelfManagerCard />);
    fireEvent.press(await screen.findByText('Go there'));

    await waitFor(() => expect(mockRouterPush).toHaveBeenCalledWith(expected));
    expect(mocked.respondToShelfManager).toHaveBeenCalledTimes(1);
  });

  it('sends somebody adding a hair product to the hair category', async () => {
    mocked.getShelfManager.mockResolvedValue(queue({
      primary: decision({
        category: 'hair',
        action: { kind: 'add_owned_product', label: 'Add one you own', inventory_item_id: null, mutates: false },
      }),
    }));
    render(<ShelfManagerCard />);
    fireEvent.press(await screen.findByText('Add one you own'));

    await waitFor(() => expect(mockRouterPush).toHaveBeenCalledWith({
      pathname: '/inventory-add', params: { category: 'hair' },
    }));
  });

  it('does not navigate when the answer was refused', async () => {
    mocked.getShelfManager.mockResolvedValue(queue({
      primary: decision({
        action: { kind: 'record_date', label: 'Add the date', inventory_item_id: 'item-1', mutates: false },
      }),
    }));
    // The product was deleted between reading the queue and answering, so the
    // server refuses. Opening its screen would send somebody nowhere real.
    mocked.respondToShelfManager.mockRejectedValue(new Error('stale'));
    render(<ShelfManagerCard />);
    fireEvent.press(await screen.findByText('Add the date'));

    await waitFor(() => expect(mocked.getShelfManager).toHaveBeenCalledTimes(2));
    expect(mockRouterPush).not.toHaveBeenCalled();
  });

  it('does not navigate when the person says not now', async () => {
    mocked.getShelfManager.mockResolvedValue(queue({
      primary: decision({
        action: { kind: 'open_routine', label: 'Open your routine', inventory_item_id: null, mutates: false },
      }),
    }));
    render(<ShelfManagerCard />);
    fireEvent.press(await screen.findByText('Not now'));

    await waitFor(() => expect(mocked.respondToShelfManager).toHaveBeenCalled());
    expect(mockRouterPush).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// The submission key
// ---------------------------------------------------------------------------

describe('the submission key', () => {
  it('is reused when the same decision is answered again', async () => {
    const pending = deferred<apiV2.ShelfManagerResponse>();
    mocked.respondToShelfManager.mockReturnValueOnce(pending.promise);
    mocked.respondToShelfManager.mockResolvedValue(response({ primary: decision() }));
    render(<ShelfManagerCard />);

    fireEvent.press(await screen.findByText('Pause it'));
    pending.reject(new Error('timeout'));
    await waitFor(() => expect(screen.queryByText('Pause it')).toBeTruthy());
    fireEvent.press(screen.getByText('Pause it'));

    await waitFor(() => expect(mocked.respondToShelfManager).toHaveBeenCalledTimes(2));
    const [first, second] = mocked.respondToShelfManager.mock.calls;
    expect(second[0].client_mutation_id).toBe(first[0].client_mutation_id);
  });

  it('is never shared between two different decisions', async () => {
    const next = decision({
      decision_key: 'rule.no_expiry_recorded:item:item-2',
      decision_fingerprint: 'b'.repeat(64),
      action: { kind: 'pause_product', label: 'Pause it', inventory_item_id: 'item-2', mutates: true },
    });
    mocked.respondToShelfManager.mockResolvedValueOnce(response({ primary: next, remaining_count: 0 }));
    render(<ShelfManagerCard />);

    fireEvent.press(await screen.findByText('Pause it'));
    await waitFor(() => expect(mocked.respondToShelfManager).toHaveBeenCalledTimes(1));
    fireEvent.press(await screen.findByText('Pause it'));

    await waitFor(() => expect(mocked.respondToShelfManager).toHaveBeenCalledTimes(2));
    const [first, second] = mocked.respondToShelfManager.mock.calls;
    expect(second[0].decision_key).not.toBe(first[0].decision_key);
    expect(second[0].client_mutation_id).not.toBe(first[0].client_mutation_id);
  });
});

// ---------------------------------------------------------------------------
// The busy state belongs to a decision, not to the component
// ---------------------------------------------------------------------------

describe('the busy state', () => {
  /**
   * Record the tree as committed, before passive effects run.
   *
   * `render` and `fireEvent` both wrap in `act()`, which flushes effects before
   * returning, so an assertion made afterwards can only see the tree an effect
   * left behind. A `Profiler`'s `onRender` fires during the commit phase, which
   * is the only place the intermediate frame is visible.
   */
  function renderWithCommits() {
    const commits: string[] = [];
    const holder: { current: null | { toJSON: () => unknown } } = { current: null };
    const onRender = () => { if (holder.current) commits.push(JSON.stringify(holder.current.toJSON())); };
    const view = render(
      <Profiler id="shelf-manager-boundary" onRender={onRender}>
        <ShelfManagerCard />
      </Profiler>,
    );
    holder.current = view;
    return { commits, view };
  }

  it('disables the button only while that decision is being answered', async () => {
    const pending = deferred<apiV2.ShelfManagerResponse>();
    mocked.respondToShelfManager.mockReturnValue(pending.promise);
    render(<ShelfManagerCard />);
    const button = await screen.findByLabelText('Pause it');
    expect(button.props.accessibilityState.disabled).toBe(false);

    fireEvent.press(button);

    await waitFor(() => {
      expect(screen.getByLabelText('Pause it').props.accessibilityState.disabled).toBe(true);
    });
    pending.resolve(response({ primary: decision(), remaining_count: 0 }));
    await waitFor(() => {
      expect(screen.getByLabelText('Pause it').props.accessibilityState.disabled).toBe(false);
    });
  });

  it('never commits one decision\'s spinner under the next decision\'s words', async () => {
    const pending = deferred<apiV2.ShelfManagerResponse>();
    mocked.respondToShelfManager.mockReturnValue(pending.promise);
    const { commits } = renderWithCommits();

    fireEvent.press(await screen.findByText('Pause it'));
    pending.resolve(response({
      primary: decision({
        decision_key: 'rule.low_use_product:item:item-9',
        decision_fingerprint: 'c'.repeat(64),
        decision: 'Use this before replacing it.',
        action: { kind: 'open_routine', label: 'Open your routine', inventory_item_id: null, mutates: false },
      }),
      remaining_count: 0,
    }));
    await screen.findByText('Use this before replacing it.');

    const nextWords = commits.filter((frame) => frame.includes('Use this before replacing it.'));
    expect(nextWords.length).toBeGreaterThan(0);
    for (const frame of nextWords) {
      // The next decision's button is readable, not stuck behind the previous
      // decision's spinner.
      expect(frame).toContain('Open your routine');
      expect(frame).not.toContain('"disabled":true');
    }
  });
});

// ---------------------------------------------------------------------------
// Reloading
// ---------------------------------------------------------------------------

describe('reloading', () => {
  it('re-reads the queue when the screen says to look again', async () => {
    const view = render(<ShelfManagerCard reloadToken={0} />);
    await waitFor(() => expect(mocked.getShelfManager).toHaveBeenCalledTimes(1));

    view.rerender(<ShelfManagerCard reloadToken={1} />);

    await waitFor(() => expect(mocked.getShelfManager).toHaveBeenCalledTimes(2));
  });

  it('does not re-read when nothing has asked it to', async () => {
    const view = render(<ShelfManagerCard reloadToken={3} />);
    await waitFor(() => expect(mocked.getShelfManager).toHaveBeenCalledTimes(1));

    view.rerender(<ShelfManagerCard reloadToken={3} />);

    expect(mocked.getShelfManager).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// Accessibility and tone
// ---------------------------------------------------------------------------

describe('how the card reads', () => {
  it('labels both buttons and the card itself', async () => {
    render(<ShelfManagerCard />);

    expect(await screen.findByLabelText('Pause it')).toBeTruthy();
    expect(screen.getByLabelText('Not now')).toBeTruthy();
    expect(screen.getByLabelText(
      'YOUR MANAGER. Pause this until you replace it. Expired Cleanser is past its date',
    )).toBeTruthy();
  });

  it('carries no colour-only meaning: severity is never rendered as a colour alone', async () => {
    mocked.getShelfManager.mockResolvedValue(queue({ primary: decision({ severity: 'avoid' }) }));
    const avoid = render(<ShelfManagerCard />);
    await screen.findByText('Pause it');
    const avoidTree = JSON.stringify(avoid.toJSON());
    avoid.unmount();

    mocked.getShelfManager.mockResolvedValue(queue({ primary: decision({ severity: 'info' }) }));
    render(<ShelfManagerCard />);
    await screen.findByText('Pause it');

    // The same decision at two severities renders identically, so no colour is
    // carrying meaning that a text label does not.
    expect(JSON.stringify(screen.toJSON())).toBe(avoidTree);
  });

  it('says nothing that blames anybody for what they own', () => {
    const words = [S.heading, S.headingGiveBack, S.remaining(1), S.remaining(4)].join(' ').toLowerCase();
    for (const banned of [
      'wasted', 'failed', 'ugly', 'unattractive', 'poor', 'should have', 'buy', 'shop',
      'score', 'streak', 'missed',
    ]) {
      expect(words).not.toContain(banned);
    }
  });
});
