/**
 * Step 14 on the real Product Result screen: one dominant decision, and a
 * purchase context beneath it that never repeats it.
 *
 * The purchase check is mocked at the device-scoped client, exactly where the
 * screen reads it; everything else on the screen is the real screen.
 */
import React from 'react';
import { Linking } from 'react-native';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react-native';

import VerdictScreen from '../../app/verdict';
import { REFERENCE_ALTERNATIVE } from '../components/verdict/BetterOption';
import { PURCHASE_OS } from '../strings/purchaseOs';
import type { PurchaseOsAuthority, PurchaseOsCheck } from '../services/apiV2';

const mockPush = jest.fn();
let mockParams: Record<string, string> = { barcode: '8901058000191' };
jest.mock('expo-router', () => ({
  useLocalSearchParams: () => mockParams,
  useRouter: () => ({ push: mockPush, replace: jest.fn(), back: jest.fn() }),
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

// Every write the screen could make. A purchase context is a read.
const mockAddToShelf = jest.fn();
const mockSaveDecision = jest.fn();
jest.mock('../services/apiV2', () => ({
  readScanMemory: jest.fn(async () => null),
  saveScanDecision: (...args: unknown[]) => mockSaveDecision(...args),
  readScanShelfStatus: jest.fn(async () => ({ status: 'not_eligible' })),
  addScanProductToShelf: (...args: unknown[]) => mockAddToShelf(...args),
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
const FINGERPRINT = 'f'.repeat(64);
const FOSCOS = 'https://foscos.fssai.gov.in/food-recall';
const PRODUCT = 'Morning Oats';

const source = (overrides: Record<string, unknown> = {}) => ({
  outcome: 'graded', grade: 'A', productName: PRODUCT,
  totalSugarG: 1, saltG: 0.01, totalFatG: 2, proteinG: 10, packSizeG: 500,
  decision: { action: 'buy', reasonKey: 'label_facts' },
  negatives: [], positives: [], components: [], ingredients: [],
  officialRecords: null, communityObservations: null, attribution: null,
  physicalPackContext: true, factsProvenance: 'confirmed_label_snapshot',
  labelVersion: { id: 'snapshot-internal', versionNumber: 3, contentFingerprint: FINGERPRINT },
  ...overrides,
});

const authorities = (extra: PurchaseOsAuthority[] = []): PurchaseOsAuthority[] => [
  { authority: 'product_result', status: 'applied', action: 'buy', reason_key: 'label_facts' },
  ...extra,
];

function check(overrides: Partial<PurchaseOsCheck> = {}): PurchaseOsCheck {
  return {
    contract_version: 'step-14-v1',
    context: { kind: 'scan', strategy: 'scan_product', category: 'food' },
    subject: { household_subject_id: null, is_account_holder: true },
    identity: {
      state: 'exact', barcode: BARCODE, label_version: 3, content_fingerprint: FINGERPRINT,
      physical_pack_context: true, reference_view: false,
    },
    decision: {
      state: 'decided', verdict: 'buy', primary_reason_code: 'label_facts',
      primary_reason_authority: 'product_result', decision_fingerprint: 'a'.repeat(64),
    },
    authorities: authorities([
      { authority: 'official_records', status: 'no_governed_match' },
      { authority: 'label_change', status: 'first_observed_version', effect: 'context_only' },
    ]),
    memory: { kind: 'scan_decision', fidelity: 'user_outcome_only', state: 'no_prior_exact_decision', current_decision: null, earlier_version_decision: null, history_complete: true },
    ownership: { authority: 'inventory_product_link', effect: 'context_only', state: 'not_owned' },
    alternative: { status: 'not_enough_information', reason_key: null, candidate: null },
    value: { state: 'missing', missing: ['current_price'] },
    boundary: null,
    missing_information: [],
    ...overrides,
  };
}

const guarded = (): PurchaseOsCheck => check({
  decision: {
    state: 'decided', verdict: 'wait', primary_reason_code: 'official_record_matches_pack',
    primary_reason_authority: 'official_records', decision_fingerprint: 'b'.repeat(64),
  },
  authorities: authorities([
    { authority: 'official_records', status: 'applied', matched_record_count: 1, source: { name: 'FSSAI / FoSCoS', url: FOSCOS } },
    { authority: 'label_change', status: 'first_observed_version', effect: 'context_only' },
  ]),
});

async function renderWith(payload: PurchaseOsCheck | null | Error, sourceOverrides: Record<string, unknown> = {}) {
  mockGetProductVerdict.mockResolvedValue(source(sourceOverrides));
  if (payload instanceof Error) mockReadPurchaseCheck.mockRejectedValue(payload);
  else mockReadPurchaseCheck.mockResolvedValue(payload);
  render(<VerdictScreen />);
  await waitFor(() => expect(screen.getByText(PRODUCT)).toBeTruthy());
  await act(async () => { await Promise.resolve(); });
}

/** Every text node, top to bottom. */
function orderedText(node: unknown = screen.toJSON(), found: string[] = []): string[] {
  if (typeof node === 'string') found.push(node);
  else if (Array.isArray(node)) node.forEach((child) => orderedText(child, found));
  else if (node && typeof node === 'object') {
    const element = node as { children?: unknown };
    orderedText(element.children, found);
  }
  return found;
}

const VERDICT_WORDS = ['BUY', 'WAIT', 'SKIP'];

beforeEach(() => {
  jest.clearAllMocks();
  mockParams = { barcode: BARCODE };
  mockRegistrationState = 'registered';
});

describe('Step 14 — the purchase context on the Product Result', () => {
  it('keeps the Product Result verdict dominant and never repeats it in the context', async () => {
    await renderWith(check());
    const section = await screen.findByTestId('purchase-context');
    expect(screen.getByLabelText('Grade A. BUY.')).toBeTruthy();
    const text = orderedText();
    expect(text.indexOf('BUY')).toBeGreaterThanOrEqual(0);
    expect(text.indexOf('BUY')).toBeLessThan(text.indexOf(PURCHASE_OS.context.title));
    // Inside the section no line is a bare verdict word: it explains, it does not re-decide.
    const inside = orderedText(section.children);
    expect(inside.filter((line) => VERDICT_WORDS.includes(line.trim()))).toEqual([]);
    expect(within(section).getByText(PURCHASE_OS.context.why.productResult)).toBeTruthy();
    expect(mockReadPurchaseCheck).toHaveBeenCalledWith(BARCODE, { physicalPackContext: true });
  });

  it('shows the governed official-record ceiling as the one decision, with its openable source', async () => {
    const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(true);
    await renderWith(guarded());
    await screen.findByTestId('purchase-context-official');
    // The dominant block now says WAIT; the grade is still the label's grade.
    expect(screen.getByLabelText('Grade A. WAIT.')).toBeTruthy();
    expect(screen.queryByLabelText('Grade A. BUY.')).toBeNull();
    const text = orderedText();
    expect(text.indexOf('WAIT')).toBeLessThan(text.indexOf(PURCHASE_OS.context.title));
    expect(within(screen.getByTestId('purchase-context-official')).getByText(PURCHASE_OS.context.official.ceiling)).toBeTruthy();
    const link = screen.getByRole('link', { name: 'Open the source: FSSAI / FoSCoS' });
    fireEvent.press(link);
    expect(open).toHaveBeenCalledWith(FOSCOS);
    open.mockRestore();
  });

  it('says the answer and its reason in words, never only in colour', async () => {
    await renderWith(guarded());
    await screen.findByTestId('purchase-context');
    expect(screen.getByLabelText('Purchase answer: WAIT. An official record matches this exact pack.')).toBeTruthy();
    // The dominant block's own label carries the decision word too.
    expect(screen.getByLabelText('Grade A. WAIT.')).toBeTruthy();
  });

  it('shows ownership only when an inventory link proves it', async () => {
    await renderWith(check());
    await screen.findByTestId('purchase-context');
    expect(screen.queryByTestId('purchase-context-owned')).toBeNull();
    expect(screen.queryByText(PURCHASE_OS.context.owned.exactVersion)).toBeNull();
  });

  it('shows the exact-version shelf link when it exists, as context', async () => {
    await renderWith(check({ ownership: { authority: 'inventory_product_link', effect: 'context_only', state: 'owned_exact_version' } }));
    expect(await screen.findByText(PURCHASE_OS.context.owned.exactVersion)).toBeTruthy();
    expect(screen.getByLabelText('Grade A. BUY.')).toBeTruthy();
  });

  it('never presents a decision about an earlier label version as current', async () => {
    await renderWith(check({
      memory: {
        kind: 'scan_decision', fidelity: 'user_outcome_only', state: 'no_prior_exact_decision', current_decision: null,
        earlier_version_decision: { decision: 'SKIP', label_version: 2, occurred_at: '2026-09-01T00:00:00Z', applies_to_current_version: false },
        history_complete: true,
      },
    }));
    const prior = await screen.findByTestId('purchase-context-prior');
    expect(within(prior).getByText(/label version 2\. This pack is a different version/)).toBeTruthy();
    expect(screen.queryByText(/for this exact version\./)).toBeNull();
  });

  it('states this exact version’s own prior decision when there is one', async () => {
    await renderWith(check({
      memory: {
        kind: 'scan_decision', fidelity: 'user_outcome_only', state: 'prior_exact_decision',
        current_decision: { decision: 'WAIT', occurred_at: '2026-09-02T00:00:00Z' }, earlier_version_decision: null, history_complete: true,
      },
    }));
    expect(await screen.findByText('You chose WAIT for this exact version.')).toBeTruthy();
    expect(screen.queryByText(PURCHASE_OS.context.prior.historyIncomplete)).toBeNull();
  });

  it('shows an exact decision and incomplete older history together', async () => {
    await renderWith(check({
      memory: {
        kind: 'scan_decision', fidelity: 'user_outcome_only', state: 'prior_exact_decision',
        current_decision: { decision: 'WAIT', occurred_at: '2026-09-02T00:00:00Z' }, earlier_version_decision: null, history_complete: false,
      },
    }));
    const prior = await screen.findByTestId('purchase-context-prior');
    expect(within(prior).getByText('You chose WAIT for this exact version.')).toBeTruthy();
    expect(within(prior).getByText(PURCHASE_OS.context.prior.historyIncomplete)).toBeTruthy();
  });

  it('says older history is incomplete when there is no exact decision', async () => {
    await renderWith(check({
      memory: {
        kind: 'scan_decision', fidelity: 'user_outcome_only', state: 'history_incomplete',
        current_decision: null, earlier_version_decision: null, history_complete: false,
      },
    }));
    const prior = await screen.findByTestId('purchase-context-prior');
    expect(within(prior).getByText(PURCHASE_OS.context.prior.historyIncomplete)).toBeTruthy();
    expect(within(prior).queryByText(/for this exact version\./)).toBeNull();
  });

  it('points at the one comparable option instead of listing candidates', async () => {
    await renderWith(check({
      alternative: {
        status: 'available', reason_key: 'comparable_option_found',
        candidate: { barcode: '8901058000214', product_name: 'Other Oats', brand: 'B', grade: 'A', decision: 'buy', attribution: null },
      },
    }));
    const better = await screen.findByTestId('purchase-context-better');
    expect(within(better).getByText(PURCHASE_OS.context.better.shownBelow)).toBeTruthy();
    expect(within(screen.getByTestId('purchase-context')).queryByText(/Other Oats/)).toBeNull();
  });

  it('asks for a reference view and shows nothing pack-specific in one', async () => {
    mockParams = { barcode: BARCODE, reference: REFERENCE_ALTERNATIVE };
    // Even a payload that wrongly carried pack context must not surface it.
    const leaked = guarded();
    leaked.identity = { ...leaked.identity, reference_view: true, physical_pack_context: false };
    leaked.ownership = { state: 'owned_exact_version' };
    await renderWith(leaked, { physicalPackContext: false });
    const section = await screen.findByTestId('purchase-context');
    expect(mockReadPurchaseCheck).toHaveBeenCalledWith(BARCODE, { physicalPackContext: false });
    expect(within(section).getByText(PURCHASE_OS.context.why.referenceView)).toBeTruthy();
    for (const row of ['official', 'owned', 'prior', 'changes', 'better']) {
      expect(screen.queryByTestId(`purchase-context-${row}`)).toBeNull();
    }
    // And the ceiling never becomes the dominant decision in a reference view.
    expect(screen.getByLabelText('Grade A. BUY.')).toBeTruthy();
    expect(screen.queryByLabelText('Grade A. WAIT.')).toBeNull();
  });

  it('drops a check that describes another label version', async () => {
    await renderWith(check({ identity: { state: 'exact', barcode: BARCODE, label_version: 2, content_fingerprint: 'e'.repeat(64), physical_pack_context: true, reference_view: false } }));
    expect(screen.queryByTestId('purchase-context')).toBeNull();
  });

  it('never lets a failed purchase check hide the verdict', async () => {
    await renderWith(new Error('network'));
    expect(screen.queryByTestId('purchase-context')).toBeNull();
    expect(screen.getByLabelText('Grade A. BUY.')).toBeTruthy();
  });

  it('shows verified changes as context that does not change the answer', async () => {
    await renderWith(check({
      authorities: authorities([
        { authority: 'official_records', status: 'no_governed_match' },
        { authority: 'label_change', status: 'changed', effect: 'context_only' },
      ]),
    }));
    const changes = await screen.findByTestId('purchase-context-changes');
    expect(within(changes).getByText(PURCHASE_OS.context.changes.label)).toBeTruthy();
    expect(within(changes).getByText(PURCHASE_OS.context.changes.contextOnly)).toBeTruthy();
    expect(screen.getByLabelText('Grade A. BUY.')).toBeTruthy();
  });

  it('makes no write of any kind while reading the purchase context', async () => {
    await renderWith(guarded());
    await screen.findByTestId('purchase-context');
    expect(mockAddToShelf).not.toHaveBeenCalled();
    expect(mockSaveDecision).not.toHaveBeenCalled();
  });
});
