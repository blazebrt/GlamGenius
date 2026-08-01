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
  | 'CONFLICT'
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

// --- Complete appearance inventory ----------------------------------------

export const INVENTORY_CATEGORIES = [
  'wardrobe', 'shoes', 'accessories', 'beauty', 'hair', 'perfumes', 'supplements',
] as const;
export type InventoryCategory = typeof INVENTORY_CATEGORIES[number];

export interface InventoryAttribute {
  key: string;
  value: string | number | string[];
  source: 'user_declared' | 'photo_extracted';
  confidence: number;
  verification_state: 'draft' | 'confirmed' | 'rejected';
  model_version?: string | null;
  prompt_version?: string | null;
  schema_version?: string | null;
  source_ai_run_id?: string | null;
}

export interface InventoryItem {
  id: string;
  category: InventoryCategory;
  subcategory: string | null;
  display_name: string;
  brand: string | null;
  source: 'user_declared' | 'photo_extracted';
  verification_state: 'draft' | 'confirmed' | 'rejected';
  confidence: number;
  status: string;
  purchase_date: string | null;
  purchase_price: number | null;
  currency: string;
  usage_count: number;
  last_used_at: string | null;
  condition: string;
  replacement_priority: string;
  version: number;
  details: Record<string, any>;
  effective_expiry: string | null;
  low_use: boolean;
  image_ids: string[];
  attributes: InventoryAttribute[];
  created_at: string | null;
  updated_at: string | null;
  history?: { event_type: string; actor: string; payload: Record<string, any>; created_at: string | null }[];
}

export interface InventoryList {
  items: InventoryItem[];
  pagination: { page: number; page_size: number; total: number; pages: number };
}

export interface InventorySummary {
  total_items: number;
  categories: Record<InventoryCategory, number>;
  low_use_products: number;
  products_expiring_soon: number;
  products_needing_attention: number;
  duplicate_candidates: number;
  at_risk_value: number;
  currency: string;
  inventory_balance: { metric_version: string; visible_inputs: Record<string, number>; explanation: string };
  purchase_efficiency: { metric_version: string; items_used: number; items_with_price: number; explanation: string };
}

export interface InventoryItemInput {
  category: InventoryCategory;
  display_name: string;
  brand?: string;
  subcategory?: string;
  purchase_price?: number;
  currency?: string;
  condition?: string;
  details?: Record<string, any>;
  attributes?: { key: string; value: string | number | string[] }[];
  image_ids?: string[];
  client_mutation_id?: string;
}

export interface InventoryFilters {
  q?: string; category?: InventoryCategory; brand?: string; colour?: string;
  ingredient?: string; occasion?: string; season?: string; condition?: string;
  expiry_status?: 'missing' | 'expired' | 'expiring_soon' | 'current';
  usage_level?: 'unused' | 'low' | 'regular'; verification_state?: 'draft' | 'confirmed';
  sort?: 'newest' | 'oldest' | 'name' | 'most_used' | 'least_used' | 'recently_used';
  page?: number; page_size?: number;
}

export const getInventorySummary = async (): Promise<InventorySummary> =>
  (await api.get<InventorySummary>(`${V2}/inventory/summary`)).data;

export const getInventoryItems = async (filters: InventoryFilters = {}): Promise<InventoryList> =>
  (await api.get<InventoryList>(`${V2}/inventory/items`, { params: filters })).data;

export const searchInventory = async (filters: InventoryFilters = {}): Promise<InventoryList> =>
  (await api.get<InventoryList>(`${V2}/inventory/search`, { params: filters })).data;

export const getInventoryItem = async (id: string): Promise<InventoryItem> =>
  (await api.get<InventoryItem>(`${V2}/inventory/items/${id}`)).data;

export const createInventoryItem = async (body: InventoryItemInput): Promise<InventoryItem> =>
  (await api.post<InventoryItem>(`${V2}/inventory/items`, body)).data;

export const patchInventoryItem = async (
  id: string, body: Partial<InventoryItemInput> & { expected_version?: number }
): Promise<InventoryItem> =>
  (await api.patch<InventoryItem>(`${V2}/inventory/items/${id}`, body)).data;

export const deleteInventoryItem = async (id: string): Promise<{ id: string; status: string; message: string }> =>
  (await api.delete(`${V2}/inventory/items/${id}`)).data;

export const confirmInventoryItem = async (id: string): Promise<InventoryItem> =>
  (await api.post<InventoryItem>(`${V2}/inventory/items/${id}/confirm`)).data;

export const logInventoryUsage = async (id: string, used_on: string): Promise<InventoryItem> =>
  (await api.post<InventoryItem>(`${V2}/inventory/items/${id}/usage`, { used_on, quantity: 1 })).data;

export const setInventoryCondition = async (id: string, condition: string): Promise<InventoryItem> =>
  (await api.post<InventoryItem>(`${V2}/inventory/items/${id}/condition`, { condition })).data;

export interface InventoryExtraction {
  job_id: string;
  status: string;
  capture_type: string;
  batch_accuracy_claimed: false;
  item: InventoryItem;
  uncertain_fields: string[];
  photo_quality_notes: string;
  message: string;
}

export const extractInventoryItem = async (
  media_asset_id: string, category_hint?: InventoryCategory, capture_type: 'item_photo' | 'screenshot' = 'item_photo'
): Promise<InventoryExtraction> =>
  (await api.post<InventoryExtraction>(`${V2}/inventory/extract`, { media_asset_id, category_hint, capture_type })).data;

export interface DuplicateCandidate {
  id: string; confidence: number; reason: string; status: string;
  item_a: InventoryItem; item_b: InventoryItem;
}

export const getInventoryDuplicates = async (): Promise<DuplicateCandidate[]> =>
  (await api.get<{ candidates: DuplicateCandidate[] }>(`${V2}/inventory/duplicates`)).data.candidates;

export const resolveInventoryDuplicate = async (
  id: string, resolution: 'keep_both' | 'not_duplicate' | 'merge', canonical_item_id?: string
): Promise<void> => { await api.post(`${V2}/inventory/duplicates/${id}/resolve`, { resolution, canonical_item_id }); };

export const getExpiringInventory = async (): Promise<InventoryItem[]> =>
  (await api.get<{ items: InventoryItem[] }>(`${V2}/inventory/expiring`)).data.items;

export const getLowUseInventory = async (): Promise<InventoryItem[]> =>
  (await api.get<{ items: InventoryItem[] }>(`${V2}/inventory/low-use`)).data.items;

export interface ValueToRecover {
  label: 'Value to Recover'; estimated_total: number; currency: string; is_estimate: true;
  metric_version: string; explanation: string;
  items: { item_id: string; display_name: string; estimated_value: number | null; missing_inputs: string[]; explanation: string }[];
}

export const getValueToRecover = async (): Promise<ValueToRecover> =>
  (await api.get<ValueToRecover>(`${V2}/inventory/value-to-recover`)).data;
