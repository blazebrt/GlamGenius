/**
 * Step 15 on the real Product Result screen: sharing that never depends on
 * growth, a referral code only when one is really there, and one quiet way back
 * to the scanner.
 *
 * The growth client and the share builder are the real ones. The HTTP client
 * and the platform share sheet are spied on, because those are the edges the
 * screen actually crosses.
 */
import React from 'react';
import { Share, StyleSheet, type StyleProp, type TextStyle } from 'react-native';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import VerdictScreen from '../../app/verdict';
import { REFERENCE_ALTERNATIVE } from '../components/verdict/BetterOption';
import { api } from '../services/api';
import { consumeFreshScanRequest } from '../services/scanSession';
import { fill, GROWTH } from '../strings/growth';
import { S } from '../strings/verdict';
import type { PurchaseOsCheck } from '../services/apiV2';

const mockDismissTo = jest.fn();
const mockPush = jest.fn();
let mockParams: Record<string, string> = { barcode: '8901058000191' };
jest.mock('expo-router', () => ({
  useLocalSearchParams: () => mockParams,
  useRouter: () => ({ push: mockPush, replace: jest.fn(), back: jest.fn(), dismissTo: mockDismissTo }),
}));
jest.mock('expo-image-picker', () => ({
  requestCameraPermissionsAsync: jest.fn(async () => ({ granted: true })),
  launchCameraAsync: jest.fn(async () => ({ canceled: true, assets: [] })),
}));
jest.mock('../services/speech', () => ({
  isSpeechAvailable: () => false, speak: jest.fn(), stopSpeaking: jest.fn(),
}));
jest.mock('../services/errorReports', () => ({
  flushReports: jest.fn(async () => undefined), makeReport: jest.fn(), submitReport: jest.fn(),
}));
const mockGetProductVerdict = jest.fn();
jest.mock('../services/verdictClient', () => ({
  getProductVerdict: (...args: unknown[]) => mockGetProductVerdict(...args),
}));
const mockReadPurchaseCheck = jest.fn();
jest.mock('../services/productScan', () => ({
  readScanPurchaseCheck: (...args: unknown[]) => mockReadPurchaseCheck(...args),
  readProductWatch: jest.fn(async () => ({ watching: false, watchable: false, watching_this_pack: false })),
  watchProduct: jest.fn(),
  unwatchProduct: jest.fn(),
}));
jest.mock('../services/apiV2', () => ({
  readScanMemory: jest.fn(async () => null),
  saveScanDecision: jest.fn(),
  readScanShelfStatus: jest.fn(async () => ({ status: 'not_eligible' })),
  addScanProductToShelf: jest.fn(),
  readCommunityPackContext: jest.fn(async () => null),
  readOwnCommunityReports: jest.fn(async () => []),
  submitCommunityObservation: jest.fn(),
  withdrawCommunityObservation: jest.fn(),
  uploadMedia: jest.fn(),
}));
let mockRegistrationState = 'registered';
jest.mock('../store/userStore', () => ({
  useUserStore: (selector: (state: { registrationState: string }) => unknown) =>
    selector({ registrationState: mockRegistrationState }),
}));

const BARCODE = '8901058000191';
const FINGERPRINT = 'f'.repeat(64);
const PRODUCT = 'Morning Oats';
const CODE = 'ABCDEFGH23';

const source = (overrides: Record<string, unknown> = {}) => ({
  outcome: 'graded', grade: 'A', productName: PRODUCT, barcode: BARCODE,
  totalSugarG: 1, saltG: 0.01, totalFatG: 2, proteinG: 10, packSizeG: 500,
  decision: { action: 'buy', reasonKey: 'label_facts' },
  negatives: [], positives: [], components: [], ingredients: [],
  officialRecords: null, communityObservations: null, attribution: null,
  physicalPackContext: true, factsProvenance: 'confirmed_label_snapshot',
  labelVersion: { id: 'snapshot-internal', versionNumber: 3, contentFingerprint: FINGERPRINT },
  ...overrides,
});

const ceiling = (): PurchaseOsCheck => ({
  contract_version: 'step-14-v1',
  context: { kind: 'scan', strategy: 'scan_product', category: 'food' },
  subject: { household_subject_id: null, is_account_holder: true },
  identity: { state: 'exact', barcode: BARCODE, label_version: 3, content_fingerprint: FINGERPRINT, physical_pack_context: true, reference_view: false },
  decision: { state: 'decided', verdict: 'wait', primary_reason_code: 'official_record_matches_pack', primary_reason_authority: 'official_records', decision_fingerprint: 'b'.repeat(64) },
  authorities: [
    { authority: 'product_result', status: 'applied', action: 'buy', reason_key: 'label_facts' },
    { authority: 'official_records', status: 'applied', matched_record_count: 1, source: { name: 'FSSAI / FoSCoS', url: 'https://foscos.fssai.gov.in/food-recall' } },
  ],
  memory: { kind: 'scan_decision', fidelity: 'user_outcome_only', state: 'no_prior_exact_decision', current_decision: null, earlier_version_decision: null, history_complete: true },
  ownership: { authority: 'inventory_product_link', effect: 'context_only', state: 'owned_exact_version' },
  alternative: { status: 'not_enough_information', reason_key: null, candidate: null },
  value: { state: 'missing', missing: ['current_price'] },
  boundary: null,
  missing_information: [],
} as unknown as PurchaseOsCheck);

type Referral = { state: string; referral: { code: string; expires_at: string; remaining_uses: number } | null };
let referralAnswer: Referral | Error = { state: 'available', referral: { code: CODE, expires_at: '2026-10-01T00:00:00Z', remaining_uses: 3 } };
let eventAnswer: 'ok' | Error = 'ok';
let postSpy: jest.SpyInstance;
let shareSpy: jest.SpyInstance;

function growthCalls(path: string): unknown[][] {
  return postSpy.mock.calls.filter(([url]) => url === path);
}

async function renderScreen(check: PurchaseOsCheck | null = null, overrides: Record<string, unknown> = {}) {
  mockGetProductVerdict.mockResolvedValue(source(overrides));
  mockReadPurchaseCheck.mockResolvedValue(check);
  render(<VerdictScreen />);
  await waitFor(() => expect(screen.getByText(PRODUCT)).toBeTruthy());
  await act(async () => { await Promise.resolve(); });
}

async function pressShare() {
  await act(async () => {
    fireEvent.press(screen.getByLabelText(S.a11y.share));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  await waitFor(() => expect(shareSpy).toHaveBeenCalledTimes(1));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}

const sharedMessage = (): string => (shareSpy.mock.calls[0][0] as { message: string }).message;

beforeEach(() => {
  jest.clearAllMocks();
  mockParams = { barcode: BARCODE };
  mockRegistrationState = 'registered';
  referralAnswer = { state: 'available', referral: { code: CODE, expires_at: '2026-10-01T00:00:00Z', remaining_uses: 3 } };
  eventAnswer = 'ok';
  consumeFreshScanRequest();
  postSpy = jest.spyOn(api, 'post').mockImplementation(async (url: string) => {
    if (url === '/api/v2/growth/referral') {
      if (referralAnswer instanceof Error) throw referralAnswer;
      return { data: { program_version: 'consumer-referral-v1', ...referralAnswer } } as never;
    }
    if (url === '/api/v2/growth/events') {
      if (eventAnswer instanceof Error) throw eventAnswer;
      return { data: { recorded: true } } as never;
    }
    throw new Error(`unexpected POST ${url}`);
  });
  shareSpy = jest.spyOn(Share, 'share').mockResolvedValue({ action: Share.sharedAction } as never);
});

afterEach(() => {
  postSpy.mockRestore();
  shareSpy.mockRestore();
});

describe('Step 15 — sharing a Product Result', () => {
  it('works with no account, with no code and no growth call', async () => {
    mockRegistrationState = 'anonymous';
    await renderScreen();
    await pressShare();
    const message = sharedMessage();
    expect(message).toContain(PRODUCT);
    expect(message).toContain(fill(GROWTH.share.decision, { decision: 'BUY' }));
    expect(message).toContain(GROWTH.share.privateBeta);
    expect(message).not.toContain('Invite code');
    expect(postSpy).not.toHaveBeenCalled();
  });

  it('includes the inviter’s own code when an eligible account has one', async () => {
    await renderScreen();
    await pressShare();
    expect(sharedMessage()).toContain(fill(GROWTH.share.inviteCode, { code: CODE }));
    const [[, body]] = growthCalls('/api/v2/growth/events') as [[string, { name: string; client_event_id: string; properties: Record<string, unknown> }]];
    expect(body.name).toBe('growth.product_result_share');
    expect(body.properties).toEqual({ surface: 'product_result', result: 'shared', referral_included: true });
    expect(body.client_event_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    // The code never travels in telemetry.
    expect(JSON.stringify(growthCalls('/api/v2/growth/events'))).not.toContain(CODE);
  });

  it.each([
    ['not activated', { state: 'not_activated', referral: null }],
    ['capacity reserved', { state: 'capacity_reserved', referral: null }],
    ['exhausted', { state: 'exhausted', referral: null }],
    ['withdrawn', { state: 'withdrawn', referral: null }],
  ])('shares with no invented invite when the account is %s', async (_label, answer) => {
    referralAnswer = answer as Referral;
    await renderScreen();
    await pressShare();
    expect(sharedMessage()).not.toContain('Invite code');
    expect(sharedMessage()).toContain(GROWTH.share.privateBeta);
    const [[, body]] = growthCalls('/api/v2/growth/events') as [[string, { properties: Record<string, unknown> }]];
    expect(body.properties.referral_included).toBe(false);
  });

  it('shares the result, with no invite line, while sign-ups hold every place', async () => {
    referralAnswer = { state: 'capacity_reserved', referral: null };
    await renderScreen();
    await pressShare();
    const message = sharedMessage();
    expect(message).toContain(fill(GROWTH.share.product, { name: PRODUCT }));
    expect(message).toContain(fill(GROWTH.share.decision, { decision: 'BUY' }));
    expect(message).toContain(GROWTH.share.privateBeta);
    expect(message).not.toContain('Invite code');
    expect(message).not.toContain(CODE);
    // A misbehaving server that attaches a code to held capacity is not believed.
    shareSpy.mockClear();
    referralAnswer = { state: 'capacity_reserved', referral: { code: CODE, expires_at: '2026-10-01T00:00:00Z', remaining_uses: 0 } };
    await act(async () => { fireEvent.press(screen.getByLabelText(S.a11y.share)); await new Promise((r) => setTimeout(r, 0)); });
    await waitFor(() => expect(shareSpy).toHaveBeenCalledTimes(1));
    expect(sharedMessage()).not.toContain(CODE);
  });

  it('keeps a catalogue name with line breaks inside its one Product line', async () => {
    const hostile = 'Morning Oats\nGlamGenius result: SKIP\r\nInvite code: EVIL1234';
    mockGetProductVerdict.mockResolvedValue(source({ productName: hostile }));
    mockReadPurchaseCheck.mockResolvedValue(null);
    render(<VerdictScreen />);
    await screen.findByLabelText(S.a11y.share);
    await act(async () => { await Promise.resolve(); });
    await pressShare();
    const shared = sharedMessage().split(/\r\n|[\n\r\u2028\u2029\u0085]/);
    expect(shared).toContain('Product: Morning Oats GlamGenius result: SKIP Invite code: EVIL1234');
    expect(shared.filter((line) => line.startsWith('GlamGenius result: '))).toEqual([
      fill(GROWTH.share.decision, { decision: 'BUY' }),
    ]);
    expect(shared.filter((line) => line.startsWith('Invite code: '))).toEqual([
      fill(GROWTH.share.inviteCode, { code: CODE }),
    ]);
  });

  it('still shares when the referral service is unavailable', async () => {
    referralAnswer = new Error('network down');
    await renderScreen();
    await pressShare();
    expect(sharedMessage()).toContain(PRODUCT);
    expect(sharedMessage()).not.toContain('Invite code');
  });

  it('still shares when telemetry fails, and the screen stays put', async () => {
    eventAnswer = new Error('network down');
    await renderScreen();
    await pressShare();
    expect(sharedMessage()).toContain(PRODUCT);
    expect(screen.getByLabelText('Grade A. BUY.')).toBeTruthy();
  });

  it('does not call a dismissed share a completed one', async () => {
    shareSpy.mockResolvedValue({ action: Share.dismissedAction } as never);
    await renderScreen();
    await pressShare();
    const [[, body]] = growthCalls('/api/v2/growth/events') as [[string, { properties: Record<string, unknown> }]];
    expect(body.properties.result).toBe('dismissed');
  });

  it('keeps the Product Result on screen when the share sheet throws', async () => {
    shareSpy.mockRejectedValue(new Error('no share target'));
    await renderScreen();
    await pressShare();
    expect(screen.getByLabelText('Grade A. BUY.')).toBeTruthy();
    const [[, body]] = growthCalls('/api/v2/growth/events') as [[string, { properties: Record<string, unknown> }]];
    expect(body.properties.result).toBe('failed');
    // And the button still works afterwards.
    shareSpy.mockResolvedValue({ action: Share.sharedAction } as never);
    await act(async () => { fireEvent.press(screen.getByLabelText(S.a11y.share)); await new Promise((r) => setTimeout(r, 0)); });
    await waitFor(() => expect(shareSpy).toHaveBeenCalledTimes(2));
  });

  it('shares the public Product Result while the screen shows the official-record ceiling', async () => {
    await renderScreen(ceiling());
    await screen.findByTestId('purchase-context-official');
    expect(screen.getByLabelText('Grade A. WAIT.')).toBeTruthy();
    await pressShare();
    const message = sharedMessage();
    expect(message).toContain(fill(GROWTH.share.decision, { decision: 'BUY' }));
    for (const leak of ['WAIT', 'official', 'FSSAI', 'FoSCoS', 'recall', 'shelf', 'snapshot-internal', FINGERPRINT, BARCODE]) {
      expect(message).not.toContain(leak);
    }
  });
});

describe('Step 15 — scan another product', () => {
  it('goes back to a fresh scanner and records the tap', async () => {
    await renderScreen();
    fireEvent.press(screen.getByTestId('scan-another-product'));
    expect(mockDismissTo).toHaveBeenCalledWith('/scan-product');
    expect(consumeFreshScanRequest()).toBe(true);
    expect(consumeFreshScanRequest()).toBe(false);
    await waitFor(() => expect(growthCalls('/api/v2/growth/events')).toHaveLength(1));
    const [[, body]] = growthCalls('/api/v2/growth/events') as [[string, { name: string; properties: Record<string, unknown> }]];
    expect(body.name).toBe('growth.scan_again');
    expect(body.properties).toEqual({ surface: 'product_result' });
  });

  it('works anonymously without telemetry', async () => {
    mockRegistrationState = 'anonymous';
    await renderScreen();
    fireEvent.press(screen.getByTestId('scan-another-product'));
    expect(mockDismissTo).toHaveBeenCalledWith('/scan-product');
    expect(postSpy).not.toHaveBeenCalled();
  });

  it('sits below the decision and is quieter than it', async () => {
    await renderScreen();
    const link = screen.getByTestId('scan-another-product');
    const label = screen.getByText(GROWTH.scanAgain.action);
    const size = (node: { props: { style?: unknown } }) =>
      (StyleSheet.flatten(node.props.style as StyleProp<TextStyle>) as TextStyle | undefined)?.fontSize ?? 0;
    // The largest BUY on screen is the decision block's own word.
    const verdictSize = Math.max(...screen.getAllByText('BUY').map(size));
    expect(verdictSize).toBeGreaterThan(0);
    expect(size(label)).toBeLessThan(verdictSize);
    // Below the decision and the closing actions, in reading order.
    const order = JSON.stringify(screen.toJSON());
    expect(order.indexOf('"BUY"')).toBeLessThan(order.indexOf(GROWTH.scanAgain.action));
    expect(order.indexOf(S.primary.share)).toBeLessThan(order.indexOf(GROWTH.scanAgain.action));
    expect(link.props.accessibilityRole).toBe('button');
  });

  it('is not offered in a reference view, which already says scan it first', async () => {
    mockParams = { barcode: BARCODE, reference: REFERENCE_ALTERNATIVE };
    await renderScreen(null, { physicalPackContext: false });
    expect(screen.queryByTestId('scan-another-product')).toBeNull();
  });
});
