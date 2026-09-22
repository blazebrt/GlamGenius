/**
 * Step 12C — Product Watch on the phone.
 *
 * The control is small and secondary, and these tests hold it to that: it
 * appears only for a signed-in person holding a confirmed pack, it never asks
 * for notification permission, it never shows one product's state under
 * another, and a failed request never pretends to have succeeded.
 */
import React from 'react';
import * as fs from 'fs';
import * as path from 'path';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import { ProductWatch } from '../components/verdict/ProductWatch';
import { S } from '../strings/productWatch';
import { notificationTarget } from '../navigation/notifications';

jest.mock('expo-notifications', () => ({
  __esModule: true,
  AndroidImportance: { DEFAULT: 3 },
  requestPermissionsAsync: jest.fn(),
  getPermissionsAsync: jest.fn(),
  setNotificationChannelAsync: jest.fn(),
  getExpoPushTokenAsync: jest.fn(),
}));

const mockRead = jest.fn();
const mockWatch = jest.fn();
const mockUnwatch = jest.fn();
jest.mock('../services/productScan', () => ({
  readProductWatch: (...args: unknown[]) => mockRead(...args),
  watchProduct: (...args: unknown[]) => mockWatch(...args),
  unwatchProduct: (...args: unknown[]) => mockUnwatch(...args),
}));

const BARCODE = '8901058000191';
const OTHER = '8901058000214';

const DELIVERY_ON = { notifications_enabled: true, product_watch_enabled: true, native_push_enabled: true };

const state = (overrides: Record<string, unknown> = {}) => ({
  contract_version: 'step-12c-v1',
  barcode: BARCODE,
  watching: false,
  label_version: null,
  started_at: null,
  watchable: true,
  anchorable_label_version: 1,
  watching_this_pack: false,
  reason: null,
  delivery: DELIVERY_ON,
  ...overrides,
});

const watching = (overrides: Record<string, unknown> = {}) => state({
  watching: true, label_version: 1, started_at: '2026-09-01T00:00:00+00:00', watching_this_pack: true,
  ...overrides,
});

const mockNotifications = jest.requireMock('expo-notifications') as { requestPermissionsAsync: jest.Mock };

beforeEach(() => {
  jest.clearAllMocks();
});

describe('Product Watch control', () => {
  it('offers the explicit watch action for a pack the server says is watchable', async () => {
    mockRead.mockResolvedValue(state());
    render(<ProductWatch barcode={BARCODE} />);

    expect(await screen.findByText(S.watch)).toBeTruthy();
    expect(screen.getByText(S.explain)).toBeTruthy();
    expect(mockRead).toHaveBeenCalledWith(BARCODE);
    expect(mockWatch).not.toHaveBeenCalled();
  });

  it('watches with the label version the server offered, and then says so', async () => {
    mockRead.mockResolvedValue(state({ anchorable_label_version: 3 }));
    mockWatch.mockResolvedValue(watching({ label_version: 3 }));
    render(<ProductWatch barcode={BARCODE} />);

    fireEvent.press(await screen.findByLabelText(S.a11y.watch));

    expect(await screen.findByText(S.watching)).toBeTruthy();
    expect(mockWatch).toHaveBeenCalledTimes(1);
    expect(mockWatch).toHaveBeenCalledWith(BARCODE, 3);
  });

  it('sends one request however quickly the button is tapped', async () => {
    mockRead.mockResolvedValue(state());
    let resolve: (value: unknown) => void = () => undefined;
    mockWatch.mockReturnValue(new Promise((r) => { resolve = r; }));
    render(<ProductWatch barcode={BARCODE} />);
    const button = await screen.findByLabelText(S.a11y.watch);

    fireEvent.press(button);
    fireEvent.press(button);
    fireEvent.press(button);
    await act(async () => { resolve(watching()); });

    expect(mockWatch).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(S.watching)).toBeTruthy();
  });

  it('stops watching', async () => {
    mockRead.mockResolvedValue(watching());
    mockUnwatch.mockResolvedValue(state());
    render(<ProductWatch barcode={BARCODE} />);

    fireEvent.press(await screen.findByLabelText(S.a11y.stop));

    expect(await screen.findByText(S.watch)).toBeTruthy();
    expect(mockUnwatch).toHaveBeenCalledWith(BARCODE);
    expect(screen.queryByText(S.watching)).toBeNull();
  });

  it('a failed watch request leaves the screen showing what the server actually holds', async () => {
    mockRead.mockResolvedValue(state());
    mockWatch.mockRejectedValue(new Error('network'));
    render(<ProductWatch barcode={BARCODE} />);

    fireEvent.press(await screen.findByLabelText(S.a11y.watch));

    expect(await screen.findByText(S.failed)).toBeTruthy();
    expect(screen.queryByText(S.watching)).toBeNull();
    expect(screen.getByText(S.watch)).toBeTruthy();
    // It asked the server again rather than guessing.
    expect(mockRead).toHaveBeenCalledTimes(2);
  });

  it('a failed stop leaves the watch shown as still active', async () => {
    mockRead.mockResolvedValue(watching());
    mockUnwatch.mockRejectedValue(new Error('network'));
    render(<ProductWatch barcode={BARCODE} />);

    fireEvent.press(await screen.findByLabelText(S.a11y.stop));

    expect(await screen.findByText(S.failed)).toBeTruthy();
    expect(screen.getByText(S.watching)).toBeTruthy();
  });

  it('shows nothing at all when the watch state cannot be read', async () => {
    mockRead.mockRejectedValue(new Error('offline'));
    const view = render(<ProductWatch barcode={BARCODE} />);
    await waitFor(() => expect(mockRead).toHaveBeenCalled());

    expect(view.toJSON()).toBeNull();
  });

  it('explains, without a button, when this phone holds no confirmed pack', async () => {
    mockRead.mockResolvedValue(state({ watchable: false, anchorable_label_version: null, reason: 'confirmed_pack_required' }));
    render(<ProductWatch barcode={BARCODE} />);

    expect(await screen.findByText(S.needsConfirmedPack)).toBeTruthy();
    expect(screen.queryByLabelText(S.a11y.watch)).toBeNull();
  });

  it('offers to move the watch to the pack in hand when an earlier pack is watched', async () => {
    mockRead.mockResolvedValue(watching({ watching_this_pack: false, anchorable_label_version: 2 }));
    mockWatch.mockResolvedValue(watching({ label_version: 2 }));
    render(<ProductWatch barcode={BARCODE} />);

    expect(await screen.findByText(S.otherPack)).toBeTruthy();
    fireEvent.press(screen.getByLabelText(S.a11y.watchThisPack));

    await waitFor(() => expect(mockWatch).toHaveBeenCalledWith(BARCODE, 2));
    await waitFor(() => expect(screen.queryByText(S.otherPack)).toBeNull());
  });

  it('keeps watching useful when push is off, and says delivery is a separate setting', async () => {
    mockRead.mockResolvedValue(watching({ delivery: { ...DELIVERY_ON, native_push_enabled: false } }));
    render(<ProductWatch barcode={BARCODE} />);

    expect(await screen.findByText(S.watching)).toBeTruthy();
    expect(screen.getByText(S.deliveryOff)).toBeTruthy();
  });

  it('never asks for notification permission', async () => {
    mockRead.mockResolvedValue(state());
    mockWatch.mockResolvedValue(watching({ delivery: { ...DELIVERY_ON, native_push_enabled: false } }));
    render(<ProductWatch barcode={BARCODE} />);

    fireEvent.press(await screen.findByLabelText(S.a11y.watch));
    await screen.findByText(S.watching);

    expect(mockNotifications.requestPermissionsAsync).not.toHaveBeenCalled();
    const source = fs.readFileSync(path.join(__dirname, '../components/verdict/ProductWatch.tsx'), 'utf8');
    expect(source).not.toMatch(/expo-notifications/);
    expect(source).not.toMatch(/requestPermissions/);
  });

  it('never shows one product\'s watch state under another', async () => {
    let resolveOther: (value: unknown) => void = () => undefined;
    mockRead.mockImplementation((barcode: string) => (
      barcode === BARCODE ? Promise.resolve(watching()) : new Promise((r) => { resolveOther = r; })
    ));
    const view = render(<ProductWatch barcode={BARCODE} />);
    expect(await screen.findByText(S.watching)).toBeTruthy();

    view.rerender(<ProductWatch barcode={OTHER} />);

    // B has not answered yet: A's "Watching" must not be rendered under B.
    expect(screen.queryByText(S.watching)).toBeNull();
    await act(async () => { resolveOther(state({ barcode: OTHER })); });
    expect(screen.getByText(S.watch)).toBeTruthy();
  });

  it('ignores an answer that is about a different product', async () => {
    mockRead.mockResolvedValue(watching({ barcode: OTHER }));
    const view = render(<ProductWatch barcode={BARCODE} />);
    await waitFor(() => expect(mockRead).toHaveBeenCalled());

    expect(view.toJSON()).toBeNull();
  });
});

describe('Product Watch copy', () => {
  const FORBIDDEN = [
    'danger', 'unsafe', 'safe', 'cleared', 'warning', 'alert', 'urgent', 'improved', 'worse',
    'better', 'reformulated', 'fixed', 'healthier', 'new recall', 'newly', 'recalled',
  ];

  const allStrings = (value: unknown): string[] => (
    typeof value === 'string' ? [value]
      : value && typeof value === 'object' ? Object.values(value).flatMap(allStrings) : []
  );

  it('states what the control does and characterises nothing', () => {
    const words = allStrings(S).map((text) => text.toLowerCase());
    expect(words.length).toBeGreaterThan(10);
    for (const text of words) {
      for (const forbidden of FORBIDDEN) {
        expect(text).not.toContain(forbidden);
      }
    }
  });

  it('accessibility labels describe state without alarm language', () => {
    expect(S.a11y.watching).toBe('Watching verified changes for this product');
    for (const label of Object.values(S.a11y)) {
      expect(label).toMatch(/watch/i);
    }
  });

  it('the component writes no customer copy of its own', () => {
    const source = fs.readFileSync(path.join(__dirname, '../components/verdict/ProductWatch.tsx'), 'utf8');
    // Only the rendered markup: from the component's JSX return to its styles.
    const start = source.indexOf('return (\n    <View');
    const end = source.indexOf('const styles');
    expect(start).toBeGreaterThan(0);
    const markup = source.slice(start, end);
    // JSX text children and quoted attribute copy would both show up here.
    const jsxText = markup.match(/>\s*[A-Za-z][^<>{}]*</g) ?? [];
    expect(jsxText).toEqual([]);
    expect(markup).not.toMatch(/accessibilityLabel="[^"]+"/);
    expect(markup).toMatch(/S\.watch\b/);
  });
});

describe('Product Watch deep link', () => {
  it('opens the watched product\'s verdict with a validated barcode only', () => {
    expect(notificationTarget({ destination: '/verdict', barcode: BARCODE }))
      .toEqual({ destination: '/verdict', params: { barcode: BARCODE } });
    expect(notificationTarget({ destination: '/verdict', barcode: '12345678' }))
      .toEqual({ destination: '/verdict', params: { barcode: '12345678' } });
    expect(notificationTarget({
      destination: '/verdict', barcode: BARCODE, reference: 'alternative', source_url: 'https://evil.invalid',
      delivery_id: 'x',
    })).toEqual({ destination: '/verdict', params: { barcode: BARCODE } });
  });

  it('falls back to the scanner for anything else', () => {
    for (const barcode of [undefined, '', '1234567', '123456789012345', 'ABCDEFGH', `${BARCODE}\n`, `${BARCODE}/x`, 8901058000191, [BARCODE]]) {
      expect(notificationTarget({ destination: '/verdict', barcode })).toEqual({ destination: '/scan' });
    }
    expect(notificationTarget({ destination: '/verdict/8901058000191' })).toEqual({ destination: '/scan' });
    expect(notificationTarget({ destination: '/verdict?barcode=8901058000191' })).toEqual({ destination: '/scan' });
    expect(notificationTarget(null)).toEqual({ destination: '/scan' });
  });

  it('adds no parameter to any other destination', () => {
    expect(notificationTarget({ destination: '/improve', barcode: BARCODE })).toEqual({ destination: '/improve' });
  });
});
