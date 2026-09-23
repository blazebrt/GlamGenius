/**
 * Step 15 — the growth client fails quietly and never re-counts a retry.
 */
import { api } from '../services/api';
import { ensureShareReferralCode, recordGrowthEvent, REFERRAL_TIMEOUT_MS } from '../services/growth';

let postSpy: jest.SpyInstance;

beforeEach(() => {
  postSpy = jest.spyOn(api, 'post');
});

afterEach(() => {
  postSpy.mockRestore();
});

const referral = (state: string, code: string | null) => ({
  data: { program_version: 'consumer-referral-v1', state, referral: code ? { code, expires_at: null, remaining_uses: 1 } : null },
});

describe('ensureShareReferralCode', () => {
  it('returns the code only when one is available, within a bounded wait', async () => {
    postSpy.mockResolvedValueOnce(referral('available', 'ABCDEFGH23'));
    await expect(ensureShareReferralCode()).resolves.toBe('ABCDEFGH23');
    expect(postSpy).toHaveBeenCalledWith('/api/v2/growth/referral', undefined, { timeout: REFERRAL_TIMEOUT_MS });
    expect(REFERRAL_TIMEOUT_MS).toBeLessThanOrEqual(5000);
  });

  it.each([
    ['not_activated', null], ['ready', null], ['exhausted', null], ['withdrawn', null], ['unavailable', null],
    ['available', 'abc'], ['available', 'ABC DEF 123'], ['available', 'ABCDEFGH23\nhttps://x'],
  ])('returns nothing for %s / %s', async (state, code) => {
    postSpy.mockResolvedValueOnce(referral(state, code));
    await expect(ensureShareReferralCode()).resolves.toBeNull();
  });

  it('never throws', async () => {
    postSpy.mockRejectedValueOnce(new Error('offline'));
    await expect(ensureShareReferralCode()).resolves.toBeNull();
  });
});

describe('recordGrowthEvent', () => {
  const event = { name: 'growth.scan_again' as const, properties: { surface: 'product_result' as const } };

  it('retries once with the same operation id when the first attempt never arrived', async () => {
    postSpy.mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce({ data: { recorded: true } });
    await recordGrowthEvent(event);
    expect(postSpy).toHaveBeenCalledTimes(2);
    const [first, second] = postSpy.mock.calls.map(([, body]) => body as { client_event_id: string });
    expect(first.client_event_id).toBe(second.client_event_id);
  });

  it('does not retry an answered request, and never throws', async () => {
    postSpy.mockRejectedValue({ response: { status: 422 } });
    await expect(recordGrowthEvent(event)).resolves.toBeUndefined();
    expect(postSpy).toHaveBeenCalledTimes(1);
  });

  it('sends exactly the name, the operation id and the whitelisted properties', async () => {
    postSpy.mockResolvedValueOnce({ data: { recorded: true } });
    await recordGrowthEvent(event, 'a1b2c3d4-0000-4000-8000-000000000000');
    expect(postSpy.mock.calls[0][1]).toEqual({
      name: 'growth.scan_again', client_event_id: 'a1b2c3d4-0000-4000-8000-000000000000',
      properties: { surface: 'product_result' },
    });
  });
});
