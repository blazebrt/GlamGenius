import { isRateLimited, isRegistrationRequired } from '../services/api';

describe('Typed API error handling', () => {
  it('interprets rate limit correctly', () => {
    const error = { response: { status: 429 } };
    expect(isRateLimited(error)).toBe(true);
  });

  it('interprets registration required correctly', () => {
    const error = { response: { status: 403, data: { detail: { code: 'REGISTRATION_REQUIRED' } } } };
    expect(isRegistrationRequired(error)).toBe(true);
  });

});
