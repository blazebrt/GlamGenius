/**
 * Step 16: the app's second lock on the outbound link, and the link itself.
 *
 * The server decides whether a link exists. The app refuses any answer it
 * cannot vouch for, re-checks the address before opening it, keeps the
 * disclosure beside the action, and records one closed event only for a
 * signed-in person whose link actually opened.
 */
import React from 'react';
import { Linking } from 'react-native';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import { CommerceHandoff } from '../components/verdict/CommerceHandoff';
import {
  commerceHandoffMatches, commerceOpenEvent, isCommerceDestination, isExactGtin, newCommerceEventId,
  parseCommerceHandoff, readCommerceHandoff, recordCommerceOpen, type CommerceHandoff as Handoff,
} from '../services/commerce';
import type { PurchaseOsCheck } from '../services/apiV2';
import { COMMERCE, COMMERCE_COPY_VERSION } from '../strings/commerce';

const mockPost = jest.fn();
jest.mock('../services/api', () => ({
  api: { post: (...args: unknown[]) => mockPost(...args) },
}));
const mockReadWire = jest.fn();
jest.mock('../services/productScan', () => ({
  readScanCommerceHandoff: (...args: unknown[]) => mockReadWire(...args),
}));

const BARCODE = '8901058000191';
const OTHER = '8901058000214';
const FINGERPRINT = 'f'.repeat(64);
const url = (barcode: string, tag: string | null = 'glamgenius-21') =>
  `https://www.amazon.in/s?k=${barcode}${tag ? `&tag=${tag}` : ''}`;

function wire(overrides: Record<string, unknown> = {}) {
  return {
    contract_version: 'commerce-handoff-v1', state: 'available', target: 'current_product',
    reason_code: 'decided_buy', decision: 'buy',
    identity: { barcode: BARCODE, label_version: 3, content_fingerprint: FINGERPRINT },
    target_barcode: BARCODE,
    partner: { key: 'amazon_in', display_name: 'Amazon.in', url: url(BARCODE), affiliate: true },
    pack_notice: 'rescan_received_pack',
    ...overrides,
  };
}
const altWire = (decision = 'skip', overrides: Record<string, unknown> = {}) => wire({
  target: 'alternative', reason_code: 'canonical_alternative', decision, target_barcode: OTHER,
  partner: { key: 'amazon_in', display_name: 'Amazon.in', url: url(OTHER), affiliate: true },
  ...overrides,
});

function check(verdict: 'buy' | 'wait' | 'skip' = 'buy', identity: Record<string, unknown> = {}): PurchaseOsCheck {
  return {
    contract_version: 'step-14-v1',
    context: { kind: 'scan', strategy: 'scan_product', category: 'food' },
    subject: null,
    identity: {
      state: 'exact', barcode: BARCODE, label_version: 3, content_fingerprint: FINGERPRINT,
      physical_pack_context: true, reference_view: false, ...identity,
    },
    decision: {
      state: 'decided', verdict, primary_reason_code: 'label_facts',
      primary_reason_authority: 'product_result', decision_fingerprint: 'a'.repeat(64),
    },
    authorities: [], memory: null, ownership: null, alternative: null, value: null, boundary: null,
    missing_information: [],
  } as PurchaseOsCheck;
}

beforeEach(() => {
  mockPost.mockReset();
  mockReadWire.mockReset();
  // React Native's preset already makes ``Linking.openURL`` a mock, so a spy
  // on it is the same function: clear its calls, then restore any wrapper.
  jest.clearAllMocks();
  jest.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// The exact barcode
// ---------------------------------------------------------------------------
describe('an exact barcode', () => {
  it.each(['96385074', '036000291452', BARCODE, OTHER, '4006381333931', '10012345678902'])('accepts %s', (value) => {
    expect(isExactGtin(value)).toBe(true);
  });

  it.each([
    '8901058000192', '89010580001', '123456789012345', '1234567', '00000000', '0000000000000',
    `${BARCODE}\n`, ` ${BARCODE}`, `${BARCODE}&tag=x`, '٨٩٠١٠٥٨٠٠٠١٩١', '８９０１０５８０００１９１',
    '890105800019A', '8'.repeat(64), '', null, 8901058000191, undefined,
  ])('refuses %p', (value) => {
    expect(isExactGtin(value)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// The address
// ---------------------------------------------------------------------------
describe('the address', () => {
  it('accepts exactly the registry address, with and without the tag', () => {
    expect(isCommerceDestination(url(BARCODE), 'amazon_in', BARCODE)).toBe(true);
    expect(isCommerceDestination(url(BARCODE, null), 'amazon_in', BARCODE)).toBe(true);
  });

  it.each([
    ['http', `http://www.amazon.in/s?k=${BARCODE}&tag=glamgenius-21`],
    ['javascript', 'javascript:alert(1)'],
    ['android intent', `intent://www.amazon.in/s?k=${BARCODE}#Intent;scheme=https;end`],
    ['upper-case scheme', `HTTPS://www.amazon.in/s?k=${BARCODE}`],
    ['upper-case host', `https://WWW.AMAZON.IN/s?k=${BARCODE}`],
    ['user-info', `https://user:pass@www.amazon.in/s?k=${BARCODE}`],
    ['host as user-info', `https://www.amazon.in@evil.example/s?k=${BARCODE}`],
    ['host suffix', `https://www.amazon.in.evil.example/s?k=${BARCODE}`],
    ['host in path', `https://evil.example/www.amazon.in/s?k=${BARCODE}`],
    ['bare domain', `https://amazon.in/s?k=${BARCODE}`],
    ['other subdomain', `https://m.amazon.in/s?k=${BARCODE}`],
    ['port', `https://www.amazon.in:443/s?k=${BARCODE}`],
    ['fragment', `https://www.amazon.in/s?k=${BARCODE}#x`],
    ['redirect parameter', `https://www.amazon.in/s?k=${BARCODE}&tag=glamgenius-21&redirect=https://evil.example`],
    ['person in a parameter', `https://www.amazon.in/s?k=${BARCODE}&tag=glamgenius-21&ref=account-123`],
    ['reordered', `https://www.amazon.in/s?tag=glamgenius-21&k=${BARCODE}`],
    ['repeated barcode', `https://www.amazon.in/s?k=${BARCODE}&k=${OTHER}`],
    ['encoded digit', `https://www.amazon.in/s?k=%38901058000191`],
    ['double encoding', `https://www.amazon.in/s?k=${BARCODE}%2526x%253D1`],
    ['encoded newline', `https://www.amazon.in/s?k=${BARCODE}&tag=glamgenius-21%0d%0aSet-Cookie:x`],
    ['raw newline', `https://www.amazon.in/s?k=${BARCODE}&tag=glamgenius-21\r\nSet-Cookie:x`],
    ['space', `https://www.amazon.in/s?k=${BARCODE} &tag=glamgenius-21`],
    ['backslash host', `https://www.amazon.in\\@evil.example/s?k=${BARCODE}`],
    ['backslash path', `https://www.amazon.in/s\\?k=${BARCODE}`],
    ['other path', `https://www.amazon.in/gp/redirect?k=${BARCODE}`],
    ['trailing slash', `https://www.amazon.in/s/?k=${BARCODE}`],
    ['bidi override', `https://www.amazon.in/s?k=${BARCODE}‮`],
    ['homoglyph host', `https://www.аmazon.in/s?k=${BARCODE}`],
    ['oversized tag', `https://www.amazon.in/s?k=${BARCODE}&tag=${'a'.repeat(300)}`],
    ['protocol-relative', `//www.amazon.in/s?k=${BARCODE}`],
    ['another barcode', url(OTHER)],
    ['empty', ''],
  ])('refuses %s', (_label, value) => {
    expect(isCommerceDestination(value, 'amazon_in', BARCODE)).toBe(false);
  });

  it('refuses an unknown partner and a malicious barcode', () => {
    expect(isCommerceDestination(url(BARCODE), 'flipkart', BARCODE)).toBe(false);
    expect(isCommerceDestination(url(BARCODE), '__proto__', BARCODE)).toBe(false);
    expect(isCommerceDestination(url(BARCODE), 'amazon_in', `${BARCODE}&x=1`)).toBe(false);
    expect(isCommerceDestination(42, 'amazon_in', BARCODE)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// The answer
// ---------------------------------------------------------------------------
describe('the handoff answer', () => {
  it('reads a BUY and an alternative answer the server can produce', () => {
    expect(parseCommerceHandoff(wire())).toEqual({
      target: 'current_product', decision: 'buy', targetBarcode: BARCODE,
      identity: { barcode: BARCODE, labelVersion: 3, contentFingerprint: FINGERPRINT },
      partner: { key: 'amazon_in', url: url(BARCODE), affiliate: true },
    });
    for (const decision of ['wait', 'skip']) {
      expect(parseCommerceHandoff(altWire(decision))?.targetBarcode).toBe(OTHER);
    }
  });

  it.each([
    ['not available', wire({ state: 'not_applicable' })],
    ['unavailable', wire({ state: 'unavailable' })],
    ['another contract', wire({ contract_version: 'commerce-handoff-v2' })],
    ['no pack notice', wire({ pack_notice: null })],
    ['SKIP linking the current product', wire({ decision: 'skip' })],
    ['WAIT linking the current product', wire({ decision: 'wait' })],
    ['not enough information', wire({ decision: null })],
    ['an alternative under BUY', altWire('buy')],
    ['BUY naming another barcode', wire({ target_barcode: OTHER, partner: { key: 'amazon_in', url: url(OTHER), affiliate: true } })],
    ['an alternative that is the scanned pack', altWire('skip', { target_barcode: BARCODE, partner: { key: 'amazon_in', url: url(BARCODE), affiliate: true } })],
    ['an address for another barcode', wire({ partner: { key: 'amazon_in', url: url(OTHER), affiliate: true } })],
    ['an address on another host', wire({ partner: { key: 'amazon_in', url: `https://evil.example/s?k=${BARCODE}`, affiliate: true } })],
    ['an unknown partner', wire({ partner: { key: 'shop_x', url: url(BARCODE), affiliate: true } })],
    ['a tag the server did not disclose', wire({ partner: { key: 'amazon_in', url: url(BARCODE), affiliate: false } })],
    ['a disclosure without a tag', wire({ partner: { key: 'amazon_in', url: url(BARCODE, null), affiliate: true } })],
    ['no identity', wire({ identity: null })],
    ['an unusable barcode', wire({ target_barcode: '8901058000192' })],
    ['a list', [wire()]],
    ['nothing', null],
  ])('refuses %s', (_label, raw) => {
    expect(parseCommerceHandoff(raw)).toBeNull();
  });

  it('matches only the pack, version and canonical decision on screen', () => {
    const buy = parseCommerceHandoff(wire())!;
    const shown = { barcode: BARCODE, purchaseCheck: check('buy'), alternativeBarcode: null };
    expect(commerceHandoffMatches(buy, shown)).toBe(true);
    // The official-record ceiling turned BUY into WAIT: a stale BUY link is dropped.
    expect(commerceHandoffMatches(buy, { ...shown, purchaseCheck: check('wait') })).toBe(false);
    expect(commerceHandoffMatches(buy, { ...shown, purchaseCheck: check('buy', { label_version: 4 }) })).toBe(false);
    expect(commerceHandoffMatches(buy, { ...shown, purchaseCheck: check('buy', { content_fingerprint: 'e'.repeat(64) }) })).toBe(false);
    expect(commerceHandoffMatches(buy, { ...shown, purchaseCheck: check('buy', { reference_view: true }) })).toBe(false);
    expect(commerceHandoffMatches(buy, { ...shown, purchaseCheck: check('buy', { physical_pack_context: false }) })).toBe(false);
    expect(commerceHandoffMatches(buy, { ...shown, barcode: OTHER })).toBe(false);
    expect(commerceHandoffMatches(buy, { ...shown, purchaseCheck: null })).toBe(false);

    const alternative = parseCommerceHandoff(altWire('skip'))!;
    const skip = { barcode: BARCODE, purchaseCheck: check('skip'), alternativeBarcode: OTHER };
    expect(commerceHandoffMatches(alternative, skip)).toBe(true);
    expect(commerceHandoffMatches(alternative, { ...skip, alternativeBarcode: '4006381333931' })).toBe(false);
    expect(commerceHandoffMatches(alternative, { ...skip, alternativeBarcode: null })).toBe(false);
    expect(commerceHandoffMatches(alternative, { ...skip, purchaseCheck: check('wait') })).toBe(false);
  });

  it('reads the answer through the device-scoped reader and fails soft', async () => {
    mockReadWire.mockResolvedValueOnce(wire());
    expect((await readCommerceHandoff(BARCODE))?.target).toBe('current_product');
    expect(mockReadWire).toHaveBeenCalledWith(BARCODE, { physicalPackContext: true });
    mockReadWire.mockRejectedValueOnce(new Error('offline'));
    expect(await readCommerceHandoff(BARCODE)).toBeNull();
    mockReadWire.mockResolvedValueOnce(null);
    expect(await readCommerceHandoff(BARCODE)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// The link on screen
// ---------------------------------------------------------------------------
/** Every text node, top to bottom. */
function texts(node: unknown = screen.toJSON(), found: string[] = []): string[] {
  if (typeof node === 'string') found.push(node);
  else if (Array.isArray(node)) node.forEach((child) => texts(child, found));
  else if (node && typeof node === 'object') texts((node as { children?: unknown }).children, found);
  return found;
}

describe('the link on screen', () => {
  const buy = () => parseCommerceHandoff(wire())!;

  it('shows the disclosure first, then the action, the destination and the pack notice', () => {
    render(<CommerceHandoff handoff={buy()} signedIn />);
    const order = texts();
    expect(order).toEqual([
      COMMERCE.disclosure,
      COMMERCE.action.current_product,
      'Opens a search for this barcode on Amazon.in. GlamGenius does not check what it lists.',
      COMMERCE.packNotice,
    ]);
    expect(COMMERCE.disclosure).toBe('Affiliate · GlamGenius may earn a commission. This does not affect our decisions.');
  });

  it('names the alternative action for an alternative', () => {
    render(<CommerceHandoff handoff={parseCommerceHandoff(altWire('wait'))!} signedIn />);
    expect(screen.getByText(COMMERCE.action.alternative)).toBeTruthy();
    expect(screen.queryByText(COMMERCE.action.current_product)).toBeNull();
  });

  it('opens exactly the checked address with the platform, keeps the disclosure, and records one closed event', async () => {
    const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(true);
    mockPost.mockResolvedValue({ data: { recorded: true } });
    render(<CommerceHandoff handoff={buy()} signedIn />);
    await act(async () => { fireEvent.press(screen.getByRole('link')); });
    expect(open).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith(url(BARCODE));
    expect(screen.getByText(COMMERCE.disclosure)).toBeTruthy();
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
    const [path, body] = mockPost.mock.calls[0];
    expect(path).toBe('/api/v2/commerce/events');
    expect(Object.keys(body).sort()).toEqual(['client_event_id', 'name', 'properties']);
    expect(body.name).toBe('commerce.outbound_open');
    expect(body.properties).toEqual({
      surface: 'product_result', target: 'current_product', decision: 'buy', partner: 'amazon_in', affiliate: true,
    });
    expect(JSON.stringify(body)).not.toMatch(new RegExp(`${BARCODE}|amazon\\.in/|glamgenius-21`));
  });

  it('records nothing for an anonymous open', async () => {
    jest.spyOn(Linking, 'openURL').mockResolvedValue(true);
    render(<CommerceHandoff handoff={buy()} signedIn={false} />);
    await act(async () => { fireEvent.press(screen.getByRole('link')); });
    expect(mockPost).not.toHaveBeenCalled();
  });

  it('never opens an address changed after it was checked, and says so', async () => {
    const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(true);
    const tampered: Handoff = { ...buy(), partner: { ...buy().partner, url: 'https://evil.example/s?k=8901058000191' } };
    render(<CommerceHandoff handoff={tampered} signedIn />);
    await act(async () => { fireEvent.press(screen.getByRole('link')); });
    expect(open).not.toHaveBeenCalled();
    expect(mockPost).not.toHaveBeenCalled();
    expect(screen.getByText(COMMERCE.openFailed)).toBeTruthy();
    expect(screen.getByText(COMMERCE.disclosure)).toBeTruthy();
  });

  it('records nothing when the platform could not open the link', async () => {
    jest.spyOn(Linking, 'openURL').mockRejectedValue(new Error('no handler'));
    render(<CommerceHandoff handoff={buy()} signedIn />);
    await act(async () => { fireEvent.press(screen.getByRole('link')); });
    expect(mockPost).not.toHaveBeenCalled();
    expect(screen.getByText(COMMERCE.openFailed)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Telemetry
// ---------------------------------------------------------------------------
describe('telemetry', () => {
  it('builds the one event from the validated handoff alone', () => {
    const event = commerceOpenEvent(parseCommerceHandoff(altWire('skip', {
      partner: { key: 'amazon_in', url: url(OTHER, null), affiliate: false },
    }))!);
    expect(event).toEqual({
      name: 'commerce.outbound_open',
      properties: { surface: 'product_result', target: 'alternative', decision: 'skip', partner: 'amazon_in', affiliate: false },
    });
  });

  it('mints a random version-4 id per open', () => {
    const ids = new Set(Array.from({ length: 50 }, () => newCommerceEventId()));
    expect(ids.size).toBe(50);
    for (const id of ids) expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });

  it('retries once with the same id only when the server was never reached, and never throws', async () => {
    const event = commerceOpenEvent(parseCommerceHandoff(wire())!);
    mockPost.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ data: { recorded: true } });
    await recordCommerceOpen(event, 'id-1');
    expect(mockPost).toHaveBeenCalledTimes(2);
    expect(mockPost.mock.calls[0][1].client_event_id).toBe('id-1');
    expect(mockPost.mock.calls[1][1].client_event_id).toBe('id-1');
    mockPost.mockReset();
    mockPost.mockRejectedValue({ response: { status: 422 } });
    await expect(recordCommerceOpen(event)).resolves.toBeUndefined();
    expect(mockPost).toHaveBeenCalledTimes(1);
  });
});

describe('the copy', () => {
  it('is versioned', () => {
    expect(COMMERCE_COPY_VERSION).toBe('commerce-copy.v1');
  });
});
