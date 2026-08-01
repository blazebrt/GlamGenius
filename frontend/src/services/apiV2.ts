/**
 * Typed client for the V2 API.
 *
 * The audit found the existing client untyped — screens indexed into
 * `res.data.analysis` with no contract. Everything V2 goes through here, with
 * real types, so a shape change is a compile error instead of a blank screen.
 *
 * Uses the same axios instance as V1 so tokens, 401 handling and the base URL
 * are shared. Only the path prefix differs.
 */
import { api } from './api';

export const V2 = '/v2';

// --- Error contract ---------------------------------------------------------

export type ErrorCode =
  | 'ANALYSIS_UNAVAILABLE'
  | 'CONSENT_REQUIRED'
  | 'UNSUPPORTED_MEDIA_TYPE'
  | 'MEDIA_TOO_LARGE'
  | 'SUBSCRIPTIONS_UNAVAILABLE'
  | 'FEATURE_UNAVAILABLE'
  | 'VALIDATION_FAILED'
  | 'NOT_FOUND'
  | 'INTERNAL_ERROR';

export type AIFailureType =
  | 'provider_not_configured'
  | 'provider_timeout'
  | 'provider_error'
  | 'empty_response'
  | 'invalid_json'
  | 'schema_validation_failed'
  | 'image_rejected';

export interface StructuredError {
  code: ErrorCode;
  message: string;
  retryable: boolean;
  request_id?: string;
  /** Only present on ANALYSIS_UNAVAILABLE. */
  allowance_consumed?: boolean;
  failure_type?: AIFailureType;
  guidance?: string[];
  max_bytes?: number;
  allowed_types?: string[];
}

/** Pull the structured error out of a failed request, if there is one. */
export const structuredError = (err: any): StructuredError | null => {
  const detail = err?.response?.data?.detail;
  if (detail && typeof detail === 'object' && typeof detail.code === 'string') {
    return detail as StructuredError;
  }
  return null;
};

export const isAnalysisFailure = (err: any): boolean =>
  structuredError(err)?.code === 'ANALYSIS_UNAVAILABLE';

/**
 * True when the server has confirmed the user was not charged.
 *
 * Deliberately strict: only an explicit `false` counts. If we cannot tell, we
 * do not claim it, because telling someone their check was refunded when it was
 * not is worse than saying nothing.
 */
export const allowanceWasPreserved = (err: any): boolean =>
  structuredError(err)?.allowance_consumed === false;

export const failureGuidance = (err: any): string[] =>
  structuredError(err)?.guidance ?? [];

// --- Config -----------------------------------------------------------------

export interface AppConfig {
  api_version: string;
  billing: {
    subscriptions_available: boolean;
    invite_only: boolean;
    beta_message: string;
  };
  analysis: {
    provider_configured: boolean;
    consent_required: boolean;
    consent_version: string;
  };
  media: {
    max_bytes: number;
    allowed_types: string[];
    face_photos_stored: boolean;
    storage_note: string;
  };
  features: Record<string, boolean>;
}

export const getConfig = async (): Promise<AppConfig> => {
  const response = await api.get<AppConfig>(`${V2}/config`);
  return response.data;
};

// --- Me ---------------------------------------------------------------------

export interface ConsentState {
  granted: boolean;
  recorded_at: string | null;
  version: string | null;
  needs_refresh: boolean;
}

export interface ConsentSummary {
  consent_version: string;
  required: boolean;
  photo_analysis: ConsentState;
}

export interface MeResponse {
  profile: Record<string, any>;
  account: {
    id: string;
    status: 'active' | 'deletion_requested' | 'deleted';
    deletion_requested_at: string | null;
  };
  consent: ConsentSummary;
  usage: { period: string; recorded: Record<string, number> };
  media: { active_count: number };
}

export const getMe = async (): Promise<MeResponse> => {
  const response = await api.get<MeResponse>(`${V2}/me`);
  return response.data;
};

// --- Consent ----------------------------------------------------------------

export const getConsent = async (): Promise<ConsentSummary> => {
  const response = await api.get<ConsentSummary>(`${V2}/consent`);
  return response.data;
};

export const setConsent = async (granted: boolean): Promise<ConsentSummary> => {
  const response = await api.post<ConsentSummary>(`${V2}/consent`, {
    consent_type: 'photo_analysis',
    granted,
  });
  return response.data;
};

// --- Media ------------------------------------------------------------------

// Face/person analysis photos are transient request data and never enter media storage.
export type MediaPurpose = 'inventory_item';

export interface MediaAsset {
  id: string;
  content_type: string;
  byte_size: number;
  width: number | null;
  height: number | null;
  purpose: MediaPurpose;
  status: 'active' | 'deleted';
  created_at: string | null;
  deleted_at: string | null;
  content_url: string;
}

export const uploadMedia = async (
  file: { uri: string; name: string; type: string },
  purpose: MediaPurpose = 'inventory_item'
): Promise<MediaAsset> => {
  const form = new FormData();
  // React Native's FormData takes this shape for a file; the cast is the
  // standard workaround for the DOM typings not matching.
  form.append('file', file as unknown as Blob);
  form.append('purpose', purpose);

  const response = await api.post<MediaAsset>(`${V2}/media/upload`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return response.data;
};

export const getMedia = async (id: string): Promise<MediaAsset> => {
  const response = await api.get<MediaAsset>(`${V2}/media/${id}`);
  return response.data;
};

export interface MediaDeletion {
  id: string;
  status: string;
  deleted_at: string | null;
  message: string;
}

export const deleteMedia = async (id: string): Promise<MediaDeletion> => {
  const response = await api.delete<MediaDeletion>(`${V2}/media/${id}`);
  return response.data;
};

// --- Jobs -------------------------------------------------------------------

export interface JobStatus {
  id: string;
  kind: 'ai_run';
  feature: string;
  status: 'succeeded' | 'failed';
  succeeded: boolean;
  failure_type: AIFailureType | null;
  latency_ms: number | null;
  allowance_consumed: boolean;
  created_at: string | null;
  completed_at: string | null;
}

export const getJob = async (id: string): Promise<JobStatus> => {
  const response = await api.get<JobStatus>(`${V2}/jobs/${id}`);
  return response.data;
};

// --- Privacy ----------------------------------------------------------------

export const exportMyData = async (): Promise<Record<string, any>> => {
  const response = await api.get(`${V2}/privacy/export`);
  return response.data;
};

export interface AccountDeletion {
  status: string;
  requested_at: string;
  completed_now: {
    media_deleted: number;
    photo_analysis_consent_withdrawn: boolean;
  };
  pending: Record<string, string>;
  message: string;
}

export const requestAccountDeletion = async (): Promise<AccountDeletion> => {
  const response = await api.delete<AccountDeletion>(`${V2}/account`);
  return response.data;
};

// --- Appearance digital twin ------------------------------------------------

export type AttributeSource =
  | 'user_declared' | 'photo_observed' | 'inventory_inferred'
  | 'behavior_inferred' | 'integration' | 'stylist_verified';

export interface ProfileAttribute {
  id: string;
  key: string;
  label: string;
  section: string;
  value: string | number | string[];
  source: AttributeSource;
  confidence: number;
  verification_state: 'unverified' | 'confirmed' | 'rejected' | 'not_sure' | 'superseded';
  created_at: string | null;
  updated_at: string | null;
  last_reviewed_at: string | null;
  review_due_at: string | null;
  expires_at: string | null;
  source_ai_run_id: string | null;
}

export interface ProfileObservation {
  id: string;
  key: string;
  label: string;
  value: string | string[];
  source: AttributeSource;
  confidence: number;
  why: string;
  verification_state: 'unverified' | 'confirmed' | 'rejected' | 'not_sure';
  source_ai_run_id: string | null;
  created_at: string | null;
  reviewed_at: string | null;
}

export interface ReadinessItem { area: string; ready: boolean; message: string }

export interface AppearanceProfile {
  id: string;
  version: number;
  baseline_status: string;
  attributes: ProfileAttribute[];
  readiness: ReadinessItem[];
  weight_required: false;
  change_history?: Record<string, any>[];
}

export const getAppearanceProfile = async (): Promise<AppearanceProfile> =>
  (await api.get<AppearanceProfile>(`${V2}/profile`)).data;

export const patchAppearanceProfile = async (
  attributes: { key: string; value: string | number | string[] }[]
): Promise<AppearanceProfile> =>
  (await api.patch<AppearanceProfile>(`${V2}/profile`, { attributes })).data;

export const getProfileObservations = async (): Promise<ProfileObservation[]> =>
  (await api.get<{ observations: ProfileObservation[] }>(`${V2}/profile/observations`)).data.observations;

export const confirmProfileObservation = async (id: string): Promise<ProfileObservation> =>
  (await api.post<ProfileObservation>(`${V2}/profile/observations/${id}/confirm`)).data;

export const rejectProfileObservation = async (id: string): Promise<ProfileObservation> =>
  (await api.post<ProfileObservation>(`${V2}/profile/observations/${id}/reject`)).data;

export const editProfileObservation = async (
  id: string, value: string | string[], verification_state?: 'unverified' | 'not_sure'
): Promise<ProfileObservation> =>
  (await api.patch<ProfileObservation>(`${V2}/profile/observations/${id}`, { value, verification_state })).data;

export interface BaselineResult {
  status: 'observations_ready' | 'low_quality';
  image_quality: string;
  message: string;
  guidance?: string[];
  observations: ProfileObservation[];
  colour_palette: { name: string; hex?: string; why: string }[];
  photo_stored: false;
}

export const runBaselineAnalysis = async (image_base64: string): Promise<BaselineResult> =>
  (await api.post<BaselineResult>(`${V2}/profile/baseline-analysis`, { image_base64 })).data;

export interface OnboardingStatus {
  id: string;
  status: 'in_progress' | 'completed';
  current_step: string;
  steps: string[];
  completed_steps: string[];
  skipped_steps: string[];
  answers: Record<string, Record<string, any>>;
  recommendation_preview: Record<string, string> | null;
  minimum_complete: boolean;
  weight_required: false;
  first_result?: Record<string, string>;
}

export const getOnboardingStatus = async (): Promise<OnboardingStatus> =>
  (await api.get<OnboardingStatus>(`${V2}/onboarding/status`)).data;

export const saveOnboardingStep = async (
  step: string, data: Record<string, any> = {}, skipped = false
): Promise<OnboardingStatus> =>
  (await api.post<OnboardingStatus>(`${V2}/onboarding/step`, { step, data, skipped })).data;

export const completeOnboarding = async (): Promise<OnboardingStatus> =>
  (await api.post<OnboardingStatus>(`${V2}/onboarding/complete`)).data;
