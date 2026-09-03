/**
 * Where the comparable alternative sits on the real verdict screen.
 *
 * The component tests proved the card renders. They could not prove the screen
 * puts it in the right place, and placement is the whole of the constitutional
 * question here: an alternative that visually competes with the verdict, the
 * regulator's record or the negatives has been promoted above evidence it must
 * never outrank.
 *
 * It also proves the one behaviour a shopper could mistake for a scan: opening
 * the alternative is ordinary navigation, and nothing about the physical pack
 * in anybody's hand is manufactured for it.
 */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import VerdictScreen from '../../app/verdict';
import { REFERENCE_ALTERNATIVE } from '../components/verdict/BetterOption';
import { S } from '../strings/verdict';

const mockPush = jest.fn();
let mockParams: Record<string, string> = { barcode: '8901000000001' };
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
  getProductVerdict: (barcode: string, options?: unknown) =>
    mockGetProductVerdict(barcode, options),
}));

// Every write the scan flow could make. None of them may fire from opening an
// alternative: reading about a pack is not holding one.
const mockRecordScan = jest.fn();
const mockSubmitObservation = jest.fn();
jest.mock('../services/apiV2', () => ({
  readCommunityPackContext: jest.fn(async () => null),
  submitCommunityObservation: (...args: unknown[]) => mockSubmitObservation(...args),
  withdrawCommunityObservation: jest.fn(),
  readOwnCommunityReports: jest.fn(async () => []),
  uploadMedia: jest.fn(),
  recordScanEvent: (...args: unknown[]) => mockRecordScan(...args),
}));

jest.mock('../store/userStore', () => ({
  useUserStore: (selector: (state: { registrationState: string }) => unknown) =>
    selector({ registrationState: 'anonymous' }),
}));

const comparableAlternative = {
  policyVersion: 'comparable-food-alternative-v1',
  status: 'available' as const,
  reasonKey: 'comparable_option_found',
  candidate: {
    barcode: '8901000000002',
    productName: 'Sunfield Oat Porridge',
    brand: 'Sunfield',
    grade: 'B' as const,
    band: 'green' as const,
    decision: 'buy' as const,
    comparison: {
      categoryMatch: 'exact_source_leaf' as const,
      categorySource: 'open_food_facts' as const,
      currentGrade: 'C' as const,
      candidateGrade: 'B' as const,
      basis: 'per_100g' as const,
    },
    attributionText: 'Contains information from Open Food Facts, made available under the Open Database License (ODbL)',
  },
};

const source = (overrides: Record<string, unknown> = {}) => ({
  outcome: 'graded', grade: 'C', productName: 'Northstar Corn Flakes',
  totalSugarG: 8, saltG: 0.5, totalFatG: 4, proteinG: 7, packSizeG: 200,
  decision: { action: 'wait', reasonKey: 'processing' },
  negatives: [], positives: [], components: [], ingredients: [],
  officialRecords: null, communityObservations: null,
  attribution: 'Contains information from Open Food Facts, made available under the Open Database License (ODbL)',
  ...overrides,
});

beforeEach(() => {
  jest.clearAllMocks();
  mockParams = { barcode: '8901000000001' };
});

async function renderScreen(overrides: Record<string, unknown> = {}) {
  mockGetProductVerdict.mockResolvedValue(source(overrides));
  render(<VerdictScreen />);
  await waitFor(() => expect(screen.getByText('Northstar Corn Flakes')).toBeTruthy());
}

/** The order the words appear in the rendered tree, top to bottom. */
function orderedText(): string[] {
  const found: string[] = [];
  const walk = (node: unknown): void => {
    if (typeof node === 'string') { found.push(node); return; }
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (node && typeof node === 'object') {
      const element = node as { children?: unknown; props?: { children?: unknown } };
      walk(element.children ?? element.props?.children);
    }
  };
  walk(screen.toJSON());
  return found;
}

describe('where the alternative sits', () => {
  it('renders below every evidence layer and above the closing actions', async () => {
    await renderScreen({ comparableAlternative });
    const text = orderedText();
    const negatives = text.indexOf(S.factors.negatives);
    const positives = text.indexOf(S.factors.positives);
    const reportWhatYouSaw = text.indexOf(S.communityObservations.reportAction);
    const heading = text.indexOf(S.betterOption.heading);
    // "Why?" is the first of the closing actions on this tab.
    const closingActions = text.indexOf(S.primary.why);

    for (const index of [negatives, positives, reportWhatYouSaw, heading, closingActions]) {
      expect(index).toBeGreaterThan(-1);
    }
    // Below the negatives and the positives — it never competes with them.
    expect(heading).toBeGreaterThan(negatives);
    expect(heading).toBeGreaterThan(positives);
    // Below the shopper layer, which is itself below the evidence layers.
    expect(heading).toBeGreaterThan(reportWhatYouSaw);
    // And above the closing actions.
    expect(heading).toBeLessThan(closingActions);
  });

  it('leaves the verdict, the negatives and the closing actions unchanged', async () => {
    await renderScreen({ comparableAlternative });
    // The primary verdict still reads as it did.
    expect(screen.getByText('WAIT')).toBeTruthy();
    expect(screen.getByText(S.factors.negatives)).toBeTruthy();
    expect(screen.getByText(S.factors.positives)).toBeTruthy();
    // Scan another is untouched and still reachable by its own control.
    expect(screen.getByLabelText(S.primary.scanAnother)).toBeTruthy();
    // As are the other closing actions.
    expect(screen.getByText(S.primary.why)).toBeTruthy();
    expect(screen.getByText(S.primary.ingredients)).toBeTruthy();
  });

  it('shows the honest missing line when there is no candidate', async () => {
    await renderScreen({
      comparableAlternative: {
        policyVersion: 'comparable-food-alternative-v1',
        status: 'not_enough_information',
        reasonKey: 'no_comparable_candidate_in_cached_data',
        candidate: null,
      },
    });
    expect(screen.getByText(S.betterOption.notEnoughInformation)).toBeTruthy();
    expect(screen.queryByText('Sunfield Oat Porridge')).toBeNull();
  });

  it('renders no alternative surface at all on a response without the envelope', async () => {
    await renderScreen();
    expect(screen.queryByText(S.betterOption.heading)).toBeNull();
    expect(screen.queryByText(S.betterOption.notEnoughInformation)).toBeNull();
  });
});

describe('opening the alternative', () => {
  it('navigates to that product\'s Product Result, marked as a reference view', async () => {
    await renderScreen({ comparableAlternative });
    fireEvent.press(screen.getByLabelText('View Sunfield Oat Porridge'));
    expect(mockPush).toHaveBeenCalledWith({
      pathname: '/verdict',
      params: { barcode: '8901000000002', reference: REFERENCE_ALTERNATIVE },
    });
  });

  it('records no scan of a pack this phone has never held', async () => {
    await renderScreen({ comparableAlternative });
    fireEvent.press(screen.getByLabelText('View Sunfield Oat Porridge'));
    // Navigation and nothing else. A synthetic scan event would give the
    // candidate a physical-pack context nobody captured, and Step 4 and Step 5
    // would then have a batch to match against that does not exist.
    expect(mockRecordScan).not.toHaveBeenCalled();
    expect(mockSubmitObservation).not.toHaveBeenCalled();
    expect(mockPush).toHaveBeenCalledTimes(1);
  });
});

describe('a reference view', () => {
  it('asks the server to withhold every physical-pack layer', async () => {
    mockParams = { barcode: '8901000000002', reference: REFERENCE_ALTERNATIVE };
    await renderScreen();
    expect(mockGetProductVerdict).toHaveBeenCalledWith(
      '8901000000002', { physicalPackContext: false },
    );
  });

  it('asks for the ordinary physical read when it was reached by scanning', async () => {
    await renderScreen();
    expect(mockGetProductVerdict).toHaveBeenCalledWith(
      '8901000000001', { physicalPackContext: true },
    );
  });

  it('does not offer to report what the shopper saw', async () => {
    mockParams = { barcode: '8901000000002', reference: REFERENCE_ALTERNATIVE };
    await renderScreen();
    // The action would be a claim to have held this packet, and it would only
    // fail later. It is replaced by the thing that would earn it.
    expect(screen.queryByLabelText(S.communityObservations.reportAction)).toBeNull();
    expect(screen.getByLabelText(S.referenceView.scanFirstAction)).toBeTruthy();
  });

  it('sends the shopper to the real scanner rather than inventing a scan', async () => {
    mockParams = { barcode: '8901000000002', reference: REFERENCE_ALTERNATIVE };
    await renderScreen();
    fireEvent.press(screen.getByLabelText(S.referenceView.scanFirstAction));
    expect(mockPush).toHaveBeenCalledWith({
      pathname: '/scan-product', params: { barcode: '8901000000002' },
    });
    expect(mockRecordScan).not.toHaveBeenCalled();
    expect(mockSubmitObservation).not.toHaveBeenCalled();
  });

  it('leaves the ordinary scanned screen offering its Community action', async () => {
    await renderScreen();
    expect(screen.getByLabelText(S.communityObservations.reportAction)).toBeTruthy();
    expect(screen.queryByLabelText(S.referenceView.scanFirstAction)).toBeNull();
  });

  it('says the same thing about the product either way', async () => {
    // Reference mode removes authority; it never changes the science.
    await renderScreen({ comparableAlternative });
    const scanned = orderedText();
    screen.unmount();

    mockParams = { barcode: '8901000000001', reference: REFERENCE_ALTERNATIVE };
    await renderScreen({ comparableAlternative });
    const referenced = orderedText();

    for (const shown of ['WAIT', S.factors.negatives, S.betterOption.heading]) {
      expect(scanned).toContain(shown);
      expect(referenced).toContain(shown);
    }
  });
});
