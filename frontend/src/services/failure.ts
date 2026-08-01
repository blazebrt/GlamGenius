/**
 * Turning a caught error into the right thing to show.
 *
 * One place decides which trust state a failure maps to, so every screen
 * reacts the same way and the reasoning is reviewable in one file.
 */
import { StructuredError, structuredError } from './apiV2';

export type FailureKind =
  | 'low_quality'
  | 'provider_unavailable'
  | 'rate_limited'
  | 'allowance_exhausted'
  | 'consent_required'
  | 'generic';

export interface Failure {
  kind: FailureKind;
  message: string;
  guidance: string[];
  /** Only true when the server explicitly confirmed no allowance was used. */
  allowancePreserved: boolean;
  retryable: boolean;
  requestId?: string;
}

const GENERIC_MESSAGE =
  'Something went wrong and we could not finish that. Please try again.';

export function classifyFailure(err: any): Failure {
  const detail: StructuredError | null = structuredError(err);
  const status = err?.response?.status;

  if (detail?.code === 'ANALYSIS_UNAVAILABLE') {
    // The server distinguishes "your photo" from "our provider". So do we —
    // telling someone to retake a perfectly good photo because our provider is
    // down wastes their time and blames them for our problem.
    const ourFault =
      detail.failure_type === 'provider_not_configured' ||
      detail.failure_type === 'provider_timeout' ||
      detail.failure_type === 'provider_error' ||
      detail.failure_type === 'invalid_json' ||
      detail.failure_type === 'schema_validation_failed';

    return {
      kind: ourFault ? 'provider_unavailable' : 'low_quality',
      message: detail.message,
      guidance: detail.guidance ?? [],
      allowancePreserved: detail.allowance_consumed === false,
      retryable: detail.retryable !== false,
      requestId: detail.request_id,
    };
  }

  if (detail?.code === 'CONSENT_REQUIRED') {
    return {
      kind: 'consent_required',
      message: detail.message,
      guidance: [],
      allowancePreserved: true,
      retryable: false,
      requestId: detail.request_id,
    };
  }

  if (status === 429 || detail?.code === ('AI_RATE_LIMIT' as any)) {
    return {
      kind: 'rate_limited',
      message:
        detail?.message ??
        'You have run several checks in a short time. Please try again shortly.',
      guidance: [],
      // A rate limit stops the call before it happens, so nothing was spent.
      allowancePreserved: true,
      retryable: true,
      requestId: detail?.request_id,
    };
  }

  if (status === 402 || detail?.code === ('SCAN_LIMIT' as any)) {
    return {
      kind: 'allowance_exhausted',
      message:
        detail?.message ??
        'You have used your checks for this month. Your allowance resets next month.',
      guidance: [],
      allowancePreserved: false,
      retryable: false,
      requestId: detail?.request_id,
    };
  }

  return {
    kind: 'generic',
    message: detail?.message ?? GENERIC_MESSAGE,
    guidance: detail?.guidance ?? [],
    // Unknown means unknown. Never claim a refund we cannot confirm.
    allowancePreserved: detail?.allowance_consumed === false,
    retryable: detail?.retryable !== false,
    requestId: detail?.request_id,
  };
}
