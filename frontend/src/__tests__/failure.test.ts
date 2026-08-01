/**
 * Classifying a failure decides what the user is told, so it is tested
 * directly rather than only through a screen.
 */
import { classifyFailure } from '../services/failure';
import { allowanceWasPreserved, structuredError } from '../services/apiV2';

const withDetail = (status: number, detail: any) => ({
  response: { status, data: { detail } },
});

describe('classifyFailure', () => {
  it('treats a provider outage as our problem, not the photo', () => {
    const failure = classifyFailure(
      withDetail(503, {
        code: 'ANALYSIS_UNAVAILABLE',
        message: 'We could not analyse this image reliably.',
        retryable: true,
        allowance_consumed: false,
        failure_type: 'provider_error',
        guidance: ['This is a problem on our side, not with your photo'],
      })
    );

    expect(failure.kind).toBe('provider_unavailable');
    expect(failure.allowancePreserved).toBe(true);
    expect(failure.retryable).toBe(true);
  });

  it.each([
    'provider_not_configured',
    'provider_timeout',
    'invalid_json',
    'schema_validation_failed',
  ])('treats %s as our problem', (failureType) => {
    const failure = classifyFailure(
      withDetail(503, {
        code: 'ANALYSIS_UNAVAILABLE',
        message: 'x',
        retryable: true,
        allowance_consumed: false,
        failure_type: failureType,
      })
    );
    expect(failure.kind).toBe('provider_unavailable');
  });

  it('treats a rejected image as something the user can fix', () => {
    const failure = classifyFailure(
      withDetail(503, {
        code: 'ANALYSIS_UNAVAILABLE',
        message: 'We could not read that photo.',
        retryable: true,
        allowance_consumed: false,
        failure_type: 'image_rejected',
        guidance: ['Face a window'],
      })
    );

    expect(failure.kind).toBe('low_quality');
    expect(failure.guidance).toEqual(['Face a window']);
  });

  it('never claims an allowance was preserved unless the server said so', () => {
    const unknown = classifyFailure(new Error('network down'));
    expect(unknown.allowancePreserved).toBe(false);

    const silent = classifyFailure(
      withDetail(500, { code: 'INTERNAL_ERROR', message: 'x', retryable: true })
    );
    expect(silent.allowancePreserved).toBe(false);
  });

  it('recognises a monthly allowance that is genuinely used up', () => {
    const failure = classifyFailure(
      withDetail(402, {
        code: 'SCAN_LIMIT',
        message: "You've used your checks for this month.",
      })
    );

    expect(failure.kind).toBe('allowance_exhausted');
    expect(failure.retryable).toBe(false);
    // This one really did consume the allowance, so it must not be claimed back.
    expect(failure.allowancePreserved).toBe(false);
  });

  it('recognises the hourly speed limit and knows nothing was spent', () => {
    const failure = classifyFailure(withDetail(429, { code: 'AI_RATE_LIMIT', message: 'slow down' }));

    expect(failure.kind).toBe('rate_limited');
    expect(failure.allowancePreserved).toBe(true);
  });

  it('recognises a missing consent', () => {
    const failure = classifyFailure(
      withDetail(403, {
        code: 'CONSENT_REQUIRED',
        message: 'Please agree to photo analysis.',
      })
    );
    expect(failure.kind).toBe('consent_required');
    expect(failure.retryable).toBe(false);
  });

  it('falls back to something sayable when the server sends nothing useful', () => {
    const failure = classifyFailure({});
    expect(failure.kind).toBe('generic');
    expect(failure.message.length).toBeGreaterThan(0);
  });
});

describe('structuredError', () => {
  it('ignores the old plain-string detail shape', () => {
    expect(structuredError(withDetail(400, 'Email and password required'))).toBeNull();
  });

  it('returns the object shape', () => {
    const detail = { code: 'NOT_FOUND', message: 'nope', retryable: false };
    expect(structuredError(withDetail(404, detail))).toEqual(detail);
  });
});

describe('allowanceWasPreserved', () => {
  it('is only true on an explicit false from the server', () => {
    expect(
      allowanceWasPreserved(
        withDetail(503, { code: 'ANALYSIS_UNAVAILABLE', message: 'x', allowance_consumed: false })
      )
    ).toBe(true);
    expect(
      allowanceWasPreserved(withDetail(503, { code: 'ANALYSIS_UNAVAILABLE', message: 'x' }))
    ).toBe(false);
    expect(allowanceWasPreserved(new Error('boom'))).toBe(false);
  });
});
