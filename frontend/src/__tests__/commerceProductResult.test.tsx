/**
 * Step 16 on the real Product Result screen: one disclosed link, after the
 * decision, only for exactly the pack and decision on screen.
 *
 * The purchase check and the handoff are mocked at the device-scoped client,
 * exactly where the screen reads them; everything else is the real screen.
 */
import React from 'react';
import { Linking } from 'react-native';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react-native';

import VerdictScreen from '../../app/verdict';
import { REFERENCE_ALTERNATIVE } from '../components/verdict/BetterOption';
import { COMMERCE } from '../strings/commerce';
import { PURCHASE_OS } from '../strings/purchaseOs';
import { S } from '../strings/verdict';
import type { PurchaseOsCheck } from '../services/apiV2';

let mockParams: Record<string, string> = { barcode: '8901058000191' };
jest.mock('expo-router', () => ({
  useLocalSearchParams: () => mockParams,
  useRouter: () => ({ push: jest.fn(), replace: jest.fn(), back: jest.fn(), dismissTo: jest.fn() }),
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
const mockReadHandoff = jest.fn();
jest.mock('../services/productScan', () => ({
  readScanPurchaseCheck: (...args: unknown[]) => mockReadPurchaseCheck(...args),
  readScanCommerceHandoff: (...args: unknown[]) => mockReadHandoff(...args),
  readProductWatch: jest.fn(async () => ({ watching: false, watchable: false, watching_this_pack: false })),
  watchProduct: jest.fn(),
  unwatchProduct: jest.fn(),
}));

const mockPost = jest.fn(async () => ({ data: { recorded: true } }));
jest.mock('../services/api', () => ({
  api: { post: (...args: unknown[]) => mockPost(...(args as [])) },
}));
jest.mock('../services/growth', () => ({
  ensureShareReferralCode: jest.fn(async () => null),
  recordGrowthEvent: jest.fn(async () => undefined),
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
  getNotificationPreferences: jest.fn(),
  patchNotificationPreferences: jest.fn(),
  registerNotificationDevice: jest.fn(),
  unregisterNotificationDevice: jest.fn(),
}));

let mockRegistrationState = 'registered';
jest.mock('../store/userStore', () => ({
  useUserStore: (selector: (state: { registrationState: string }) => unknown) =>
    selector({ registrationState: mockRegistrationState }),
}));

const BARCODE = '8901058000191';
const OTHER = '8901058000214';
const FINGERPRINT = 'f'.repeat(64);
const PRODUCT = 'Morning Oats';
const ALTERNATIVE_NAME = 'Sunfield Oat Porridge';
const link = (barcode: string) => `https://www.amazon.in/s?k=${barcode}&tag=glamgenius-21`;

const alternative = {
  policyVersion: 'comparable-food-alternative-v1',
  status: 'available' as const,
  reasonKey: 'comparable_option_found',
  candidate: {
    barcode: OTHER, productName: ALTERNATIVE_NAME, brand: 'Sunfield', grade: 'A' as const,
    band: 'green' as const, decision: 'buy' as const,
    comparison: {
      categoryMatch: 'exact_source_leaf' as const, categorySource: 'open_food_facts' as const,
      currentGrade: 'D' as const, candidateGrade: 'A' as const, basis: 'per_100g' as const,
    },
    attributionText: 'Contains information from Open Food Facts, made available under the Open Database License (ODbL)',
  },
};

const source = (action: 'buy' | 'wait' | 'skip', overrides: Record<string, unknown> = {}) => ({
  outcome: 'graded', grade: action === 'buy' ? 'A' : 'D', productName: PRODUCT,
  totalSugarG: 1, saltG: 0.01, totalFatG: 2, proteinG: 10, packSizeG: 500,
  decision: { action, reasonKey: 'label_facts' },
  negatives: [], positives: [], components: [], ingredients: [],
  officialRecords: null, communityObservations: null, attribution: null,
  physicalPackContext: true, factsProvenance: 'confirmed_label_snapshot',
  labelVersion: { id: 'snapshot-internal', versionNumber: 3, contentFingerprint: FINGERPRINT },
  comparableAlternative: null,
  ...overrides,
});

function check(verdict: 'buy' | 'wait' | 'skip', decision: Record<string, unknown> = {}): PurchaseOsCheck {
  return {
    contract_version: 'step-14-v1',
    context: { kind: 'scan', strategy: 'scan_product', category: 'food' },
    subject: { household_subject_id: null, is_account_holder: true },
    identity: {
      state: 'exact', barcode: BARCODE, label_version: 3, content_fingerprint: FINGERPRINT,
      physical_pack_context: true, reference_view: false,
    },
    decision: {
      state: 'decided', verdict, primary_reason_code: 'label_facts',
      primary_reason_authority: 'product_result', decision_fingerprint: 'a'.repeat(64), ...decision,
    },
    authorities: [
      { authority: 'product_result', status: 'applied', action: verdict, reason_key: 'label_facts' },
      { authority: 'official_records', status: 'no_governed_match' },
    ],
    memory: null, ownership: null,
    alternative: { status: 'not_enough_information', reason_key: null, candidate: null },
    value: null, boundary: null, missing_information: [],
  } as PurchaseOsCheck;
}

const handoff = (overrides: Record<string, unknown> = {}) => ({
  contract_version: 'commerce-handoff-v1', state: 'available', target: 'current_product',
  reason_code: 'decided_buy', decision: 'buy',
  identity: { barcode: BARCODE, label_version: 3, content_fingerprint: FINGERPRINT },
  target_barcode: BARCODE,
  partner: { key: 'amazon_in', display_name: 'Amazon.in', url: link(BARCODE), affiliate: true },
  pack_notice: 'rescan_received_pack',
  ...overrides,
});
const alternativeHandoff = (decision: 'wait' | 'skip') => handoff({
  target: 'alternative', reason_code: 'canonical_alternative', decision, target_barcode: OTHER,
  partner: { key: 'amazon_in', display_name: 'Amazon.in', url: link(OTHER), affiliate: true },
});
const OFF = {
  contract_version: 'commerce-handoff-v1', state: 'unavailable', target: null,
  reason_code: 'partner_not_configured', decision: null, identity: null, target_barcode: null,
  partner: null, pack_notice: null,
};

async function renderWith(
  sourceValue: Record<string, unknown>, purchase: PurchaseOsCheck | null, commerce: unknown,
) {
  mockGetProductVerdict.mockResolvedValue(sourceValue);
  mockReadPurchaseCheck.mockResolvedValue(purchase);
  if (commerce instanceof Error) mockReadHandoff.mockRejectedValue(commerce);
  else mockReadHandoff.mockResolvedValue(commerce);
  const view = render(<VerdictScreen />);
  // Generous on purpose: with a cold transform cache the first screen render
  // compiles half the app, which can outlast the default one-second wait.
  await waitFor(() => expect(screen.getAllByText(PRODUCT).length).toBeGreaterThan(0), { timeout: 15000 });
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  return view;
}

/** Every text node, top to bottom. */
function orderedText(node: unknown = screen.toJSON(), found: string[] = []): string[] {
  if (typeof node === 'string') found.push(node);
  else if (Array.isArray(node)) node.forEach((child) => orderedText(child, found));
  else if (node && typeof node === 'object') orderedText((node as { children?: unknown }).children, found);
  return found;
}

const COMMERCE_LINES = [
  COMMERCE.disclosure, COMMERCE.action.current_product, COMMERCE.action.alternative,
  'Opens a search for this barcode on Amazon.in. GlamGenius does not check what it lists.',
  COMMERCE.packNotice, COMMERCE.openFailed,
];

beforeEach(() => {
  jest.clearAllMocks();
  mockParams = { barcode: BARCODE };
  mockRegistrationState = 'registered';
});

describe('Step 16 — a BUY', () => {
  it('shows one disclosed link after the decision, its evidence and the purchase context', async () => {
    await renderWith(source('buy'), check('buy'), handoff());
    const block = await screen.findByTestId('commerce-current-product');
    const text = orderedText();
    const decision = text.indexOf('BUY');
    const context = text.indexOf(PURCHASE_OS.context.title);
    const disclosure = text.indexOf(COMMERCE.disclosure);
    const action = text.indexOf(COMMERCE.action.current_product);
    expect(decision).toBeGreaterThanOrEqual(0);
    expect(context).toBeGreaterThan(decision);
    expect(disclosure).toBeGreaterThan(context);
    expect(disclosure).toBeGreaterThan(text.indexOf(S.factors.negatives));
    expect(disclosure).toBeGreaterThan(text.indexOf(S.communityObservations.reportAction));
    // Disclosure first, and in the same block as the action.
    expect(action).toBe(disclosure + 1);
    expect(within(block).getByText(COMMERCE.disclosure)).toBeTruthy();
    expect(within(block).getByText(COMMERCE.packNotice)).toBeTruthy();
    expect(text.filter((line) => line === COMMERCE.disclosure)).toHaveLength(1);
    expect(screen.queryByText(COMMERCE.action.alternative)).toBeNull();
    expect(mockReadHandoff).toHaveBeenCalledWith(BARCODE, { physicalPackContext: true });
  });

  it('opens the platform link, keeps the disclosure, and records one closed event', async () => {
    const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(true);
    await renderWith(source('buy'), check('buy'), handoff());
    const block = await screen.findByTestId('commerce-current-product');
    await act(async () => { fireEvent.press(within(block).getByRole('link')); });
    expect(open).toHaveBeenLastCalledWith(link(BARCODE));
    expect(screen.getByText(COMMERCE.disclosure)).toBeTruthy();
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith(
      '/api/v2/commerce/events',
      expect.objectContaining({
        name: 'commerce.outbound_open',
        properties: { surface: 'product_result', target: 'current_product', decision: 'buy', partner: 'amazon_in', affiliate: true },
      }),
      expect.anything(),
    ));
  });

  it('records nothing for an anonymous visitor, who still sees the disclosed link', async () => {
    mockRegistrationState = 'anonymous';
    jest.spyOn(Linking, 'openURL').mockResolvedValue(true);
    await renderWith(source('buy'), check('buy'), handoff());
    const block = await screen.findByTestId('commerce-current-product');
    await act(async () => { fireEvent.press(within(block).getByRole('link')); });
    expect(mockPost).not.toHaveBeenCalled();
  });
});

describe('Step 16 — the official-record ceiling', () => {
  it('drops a BUY link when the canonical decision on screen is WAIT', async () => {
    const ceiling = check('wait', {
      primary_reason_code: 'official_record_matches_pack', primary_reason_authority: 'official_records',
    });
    await renderWith(source('buy'), ceiling, handoff());
    await waitFor(() => expect(mockReadHandoff).toHaveBeenCalled());
    expect(screen.queryByTestId('commerce-current-product')).toBeNull();
    expect(screen.queryByText(COMMERCE.disclosure)).toBeNull();
  });
});

describe('Step 16 — a WAIT or SKIP', () => {
  it.each(['wait', 'skip'] as const)('attaches "Find this alternative" inside the one alternative card for %s', async (verdict) => {
    await renderWith(source(verdict, { comparableAlternative: alternative }), check(verdict), alternativeHandoff(verdict));
    const block = await screen.findByTestId('commerce-alternative');
    expect(within(block).getByText(COMMERCE.action.alternative)).toBeTruthy();
    expect(screen.queryByText(COMMERCE.action.current_product)).toBeNull();
    expect(screen.queryByTestId('commerce-current-product')).toBeNull();
    const text = orderedText();
    expect(text.filter((line) => line === S.betterOption.heading)).toHaveLength(1);
    // Inside the card: after its heading and the alternative's name.
    expect(text.indexOf(COMMERCE.disclosure)).toBeGreaterThan(text.indexOf(ALTERNATIVE_NAME));
    expect(text.indexOf(COMMERCE.disclosure)).toBeGreaterThan(text.indexOf(S.betterOption.heading));
  });

  it('never links the scanned SKIP product, even when an answer claims it', async () => {
    await renderWith(source('skip'), check('skip'), handoff({ decision: 'skip' }));
    await waitFor(() => expect(mockReadHandoff).toHaveBeenCalled());
    expect(screen.queryByText(COMMERCE.disclosure)).toBeNull();
  });

  it('never links an alternative the card is not showing', async () => {
    const shown = { ...alternative, candidate: { ...alternative.candidate, barcode: '4006381333931' } };
    await renderWith(source('skip', { comparableAlternative: shown }), check('skip'), alternativeHandoff('skip'));
    await waitFor(() => expect(mockReadHandoff).toHaveBeenCalled());
    expect(screen.queryByText(COMMERCE.disclosure)).toBeNull();
  });
});

describe('Step 16 — nothing unless everything matches', () => {
  it.each([
    ['Commerce off', OFF],
    ['another label version', handoff({ identity: { barcode: BARCODE, label_version: 2, content_fingerprint: FINGERPRINT } })],
    ['a forged address', handoff({ partner: { key: 'amazon_in', display_name: 'Amazon.in', url: 'https://evil.example/s', affiliate: true } })],
    ['a failed read', new Error('offline')],
    ['nothing', null],
  ])('shows no card, no button and no disclosure for %s', async (_label, answer) => {
    await renderWith(source('buy'), check('buy'), answer);
    await waitFor(() => expect(mockReadHandoff).toHaveBeenCalled());
    expect(screen.queryByTestId('commerce-current-product')).toBeNull();
    expect(orderedText().filter((line) => COMMERCE_LINES.includes(line))).toEqual([]);
    expect(screen.getByLabelText('Grade A. BUY.')).toBeTruthy();
  });

  it('never asks while the purchase check has not decided', async () => {
    await renderWith(source('buy'), check('buy', { state: 'not_enough_information', verdict: null }), handoff());
    expect(mockReadHandoff).not.toHaveBeenCalled();
  });

  it('never asks without a purchase check', async () => {
    await renderWith(source('buy'), null, handoff());
    expect(mockReadHandoff).not.toHaveBeenCalled();
  });

  it('never asks in a reference view', async () => {
    mockParams = { barcode: BARCODE, reference: REFERENCE_ALTERNATIVE };
    const reference = check('buy');
    reference.identity = { ...reference.identity, physical_pack_context: false, reference_view: true };
    await renderWith(source('buy', { physicalPackContext: false }), reference, handoff());
    expect(mockReadHandoff).not.toHaveBeenCalled();
    expect(screen.queryByText(COMMERCE.disclosure)).toBeNull();
  });
});

describe('Step 16 — the rest of the Product Result is unchanged', () => {
  it.each([
    ['BUY', () => source('buy'), () => check('buy'), () => handoff()],
    ['SKIP', () => source('skip', { comparableAlternative: alternative }), () => check('skip'), () => alternativeHandoff('skip')],
  ])('%s renders every other line identically, in the same order, with or without a link', async (_label, src, purchase, answer) => {
    const off = await renderWith(src(), purchase(), OFF);
    await waitFor(() => expect(mockReadHandoff).toHaveBeenCalled());
    const without = orderedText();
    off.unmount();
    await renderWith(src(), purchase(), answer());
    await screen.findByText(COMMERCE.disclosure);
    const withLink = orderedText().filter((line) => !COMMERCE_LINES.includes(line));
    expect(withLink).toEqual(without);
  });
});
