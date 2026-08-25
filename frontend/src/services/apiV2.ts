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

// Every V2 call is prefixed with `/api/v2`. The shared axios instance in
// `./api` uses the backend origin as its baseURL and does not add `/api` on
// its own, so the prefix lives here.
export const V2 = '/api/v2';

// --- Error contract ---------------------------------------------------------

export type ErrorCode =
  | 'ANALYSIS_UNAVAILABLE'
  | 'CONSENT_REQUIRED'
  | 'UNSUPPORTED_MEDIA_TYPE'
  | 'MEDIA_TOO_LARGE'
  | 'FEATURE_UNAVAILABLE'
  | 'REGISTRATION_REQUIRED'
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
  supabase: {
    url: string;
    anon_key: string;
    configured: boolean;
  };
  access: {
    invite_required: boolean;
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

// --- Access: invite reservation + registration finalisation ---------------

export interface ReserveResponse {
  challenge: string;
  reservation_id: string;
  expires_at: string;
}

/** Step 1 of the invite-gated registration flow (§1 hardening spec). */
export const reserveInvite = async (
  invite_code: string,
  email: string
): Promise<ReserveResponse> =>
  (await api.post<ReserveResponse>(`${V2}/access/reserve`, { invite_code, email })).data;

export interface FinalizeRegistrationResponse {
  account: { id: string; status: string; created_at: string | null };
  invite_redeemed: boolean;
}

/** Step 3: finalise registration for the currently-authenticated Supabase user. */
export const finalizeRegistration = async (
  registration_challenge?: string
): Promise<FinalizeRegistrationResponse> =>
  (await api.post<FinalizeRegistrationResponse>(`${V2}/access/register`, {
    registration_challenge,
  })).data;

// --- Admin: reservation metrics -------------------------------------------

export interface ReservationStats {
  totals: {
    total: number;
    active: number;
    consumed: number;
    expired: number;
  };
  active_by_invite: {
    invite_id: string;
    code: string;
    label: string;
    max_uses: number;
    uses_count: number;
    live_reservations: number;
  }[];
  generated_at: string;
}

/** Admin-only: live vs consumed vs expired reservation counts + top invites. */
export const getReservationStats = async (): Promise<ReservationStats> =>
  (await api.get<ReservationStats>(`${V2}/access/admin/reservations/stats`)).data;

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

export type AccountDeletionState =
  | 'requested'
  | 'storage_deleting'
  | 'storage_complete'
  | 'integrations_deleting'
  | 'integrations_complete'
  | 'database_deleting'
  | 'database_complete'
  | 'auth_deleting'
  | 'failed_retryable'
  | 'failed_terminal'
  | 'complete';

export interface AccountDeletion {
  account_id: string;
  state: AccountDeletionState;
  attempts: number;
  retryable: boolean;
  next_retry_at: string | null;
  requested_at: string | null;
  completed_at: string | null;
  message: string;
}

export const requestPrivacyAccountDeletion = async (): Promise<AccountDeletion> => {
  const response = await api.delete<AccountDeletion>(`${V2}/privacy/account`);
  return response.data;
};

export const getPrivacyAccountDeletionStatus = async (): Promise<AccountDeletion> => {
  const response = await api.get<AccountDeletion>(`${V2}/privacy/account-deletion`);
  return response.data;
};

export const cancelPrivacyAccountDeletion = async (): Promise<AccountDeletion> => {
  const response = await api.post<AccountDeletion>(`${V2}/privacy/account-deletion/cancel`);
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

// --- Scan -------------------------------------------------------------------

export interface ScanAnalysisRequest {
  image_base64: string;
  scan_type: string;
  city?: string;
  diet?: string;
  budget_range?: string;
  height_cm?: string;
  body_type?: string;
  style_vibe?: string;
}

export const submitScan = async (body: ScanAnalysisRequest): Promise<{ analysis: any }> =>
  (await api.post<{ analysis: any }>(`${V2}/scan/analyse`, body)).data;

export const getScanHistory = async (): Promise<any[]> =>
  (await api.get<any[]>(`${V2}/scan/history`)).data;

// --- Quiz -------------------------------------------------------------------

export interface QuizAnswer {
  question_id: string;
  answer: string;
}

export interface QuizSubmitRequest {
  answers: QuizAnswer[];
  occasion: string;
  budget: string;
}

export const getQuizQuestions = async (): Promise<any[]> =>
  (await api.get<any[]>(`${V2}/quiz/questions`)).data;

export const submitQuiz = async (body: QuizSubmitRequest): Promise<{ plan: any }> =>
  (await api.post<{ plan: any }>(`${V2}/quiz/submit`, body)).data;

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

// --- Phase 4: occasion styling ---------------------------------------------

export const OCCASION_KEYS = [
  'everyday', 'office', 'wedding', 'festival', 'interview', 'date', 'party',
  'conference', 'business_meeting', 'vacation', 'travel', 'photoshoot',
  'birthday', 'college', 'gym', 'home',
] as const;
export type OccasionKey = typeof OCCASION_KEYS[number];

export type LookSlot = 'clothing' | 'shoes' | 'accessories' | 'perfume' | 'hair' | 'grooming';
export type LookVariant = 'recommended' | 'comfortable' | 'expressive';
export type Setting = 'indoor' | 'outdoor' | 'mixed';
export type TimeOfDay = 'morning' | 'afternoon' | 'evening' | 'night';
export type ComfortPreference = 'comfort_first' | 'balanced' | 'polish_first';
export type WeatherCondition =
  | 'hot' | 'warm' | 'mild' | 'cool' | 'cold' | 'humid' | 'rainy' | 'windy';

/** Where a look's wording came from. `deterministic` is a first-class answer. */
export type ExplanationSource = 'deterministic' | 'ai_validated';

export interface OccasionQuestion {
  key: string;
  label: string;
  options: string[];
  required: boolean;
}

export interface OccasionDefinition {
  key: OccasionKey;
  label: string;
  formality: number;
  dress_codes: string[];
  default_dress_code: string;
  default_setting: Setting;
  required_slots: LookSlot[];
  optional_slots: LookSlot[];
  questions: OccasionQuestion[];
  notes: string;
}

export interface OccasionTypes {
  occasions: OccasionDefinition[];
  dress_codes: { key: string; formality: number }[];
  note: string;
}

export interface OccasionInput {
  occasion_key: OccasionKey;
  title?: string;
  event_date?: string;
  time_of_day?: TimeOfDay;
  location?: string;
  setting?: Setting;
  dress_code?: string;
  weather?: WeatherCondition;
  comfort_preference?: ComfortPreference;
  optional_budget?: number;
  preparation_time?: string;
  notes?: string;
}

export interface OccasionRecord extends OccasionInput {
  id: string;
  status: string;
  version: number;
  currency: string;
  definition: OccasionDefinition;
  created_at: string | null;
  updated_at: string | null;
}

/**
 * One piece of a look.
 *
 * `owned` is the field the UI must branch on. When it is true there is always
 * an `inventory_item_id`; when it is false there never is.
 */
export interface LookPiece {
  id: string;
  slot: LookSlot;
  ownership: 'owned' | 'optional_addition';
  inventory_item_id: string | null;
  display_name: string;
  brand: string | null;
  category: InventoryCategory | null;
  subcategory: string | null;
  role_note: string;
  score: number;
  owned: boolean;
  label: string;
}

export interface LookShare {
  title: string;
  text: string;
  includes_personal_data: false;
  note: string;
}

export interface Look {
  id: string;
  run_id: string;
  variant: LookVariant;
  title: string;
  rank: number;
  score: number;
  confidence: number;
  why_it_works: string;
  weather_note: string;
  dress_code_note: string;
  preparation_steps: string[];
  missing_information: string[];
  factor_scores: Record<string, any>;
  explanation_source: ExplanationSource;
  status: string;
  saved: boolean;
  version: number;
  slots: Record<LookSlot, LookPiece[]>;
  owned_items: LookPiece[];
  optional_additions: LookPiece[];
  owned_item_count: number;
  optional_addition_count: number;
  unavailable_items: string[];
  share: LookShare;
  created_at: string | null;
  adjustments?: {
    id: string; adjustment_type: string; slot: string | null;
    from_item_id: string | null; to_item_id: string | null;
    reason: string | null; actor: string; created_at: string | null;
  }[];
  feedback?: { rating: string; reason: string | null; note: string | null; worn_on: string | null } | null;
}

export interface Entitlement {
  feature: string;
  period: string;
  included: number;
  used: number;
  remaining: number;
  source: string;
}

/**
 * `not_enough_inventory` is a real, expected outcome, not an error. It means
 * we refused to invent clothes rather than failing to think of any.
 */
export interface StyleResult {
  status: 'ready' | 'not_enough_inventory';
  run_id: string;
  style_request_id?: string;
  occasion: OccasionRecord;
  looks: Look[];
  explanation_source?: ExplanationSource;
  engine_version?: string;
  candidates_considered?: number;
  confirmed_item_count: number;
  unconfirmed_draft_count: number;
  missing_information: string[];
  entitlement: Entitlement;
  message?: string;
  guidance?: string[];
  disclaimer?: string;
}

export const getOccasionTypes = async (): Promise<OccasionTypes> =>
  (await api.get<OccasionTypes>(`${V2}/style/occasion-types`)).data;

export const createOccasion = async (body: OccasionInput): Promise<OccasionRecord> =>
  (await api.post<OccasionRecord>(`${V2}/occasions`, body)).data;

export const getOccasions = async (): Promise<OccasionRecord[]> =>
  (await api.get<{ occasions: OccasionRecord[] }>(`${V2}/occasions`)).data.occasions;

export const getOccasion = async (id: string): Promise<OccasionRecord> =>
  (await api.get<OccasionRecord>(`${V2}/occasions/${id}`)).data;

export const patchOccasion = async (id: string, body: Partial<OccasionInput>): Promise<OccasionRecord> =>
  (await api.patch<OccasionRecord>(`${V2}/occasions/${id}`, body)).data;

export const styleForOccasion = async (
  occasion: OccasionInput, preferred_item_ids: string[] = []
): Promise<StyleResult> =>
  (await api.post<StyleResult>(`${V2}/style/occasion`, { occasion, preferred_item_ids })).data;

export const styleForSavedOccasion = async (
  occasion_id: string, preferred_item_ids: string[] = []
): Promise<StyleResult> =>
  (await api.post<StyleResult>(`${V2}/style/occasion`, { occasion_id, preferred_item_ids })).data;

export const getLook = async (id: string): Promise<Look> =>
  (await api.get<Look>(`${V2}/looks/${id}`)).data;

export type ReviseReason =
  | 'too_formal' | 'too_casual' | 'too_warm' | 'too_cold'
  | 'not_my_style' | 'want_bolder' | 'want_simpler';

export const reviseLook = async (
  id: string, reason?: ReviseReason, avoid_item_ids: string[] = []
): Promise<Look & { revision: { widened_search: boolean; note: string } }> =>
  (await api.post(`${V2}/looks/${id}/revise`, { reason, avoid_item_ids })).data;

export const swapLookItem = async (
  id: string, slot: LookSlot, to_item_id: string | null, from_item_id?: string
): Promise<Look> =>
  (await api.post<Look>(`${V2}/looks/${id}/swap-item`, { slot, to_item_id, from_item_id })).data;

export type LookRating = 'loved' | 'worn' | 'saved' | 'not_for_me';

export const sendLookFeedback = async (
  id: string, rating: LookRating, reason?: string, note?: string
): Promise<Look> =>
  (await api.post<Look>(`${V2}/looks/${id}/feedback`, { rating, reason, note })).data;

// --- Phase 4: should I buy this? -------------------------------------------

export type Verdict = 'buy' | 'wait' | 'skip';

export interface ROIFactor {
  key: string;
  label: string;
  value: number;
  weight: number;
  contribution: number;
  explanation: string;
}

export interface AppearanceROI {
  score: number;
  version: string;
  formula: string;
  thresholds: { buy: number; wait: number };
  factors: ROIFactor[];
}

export interface ShoppingCandidate {
  id: string;
  source: string;
  category: InventoryCategory;
  subcategory: string | null;
  display_name: string;
  brand: string | null;
  colour: string | null;
  size: string | null;
  fabric: string | null;
  fit: string | null;
  formality: string | null;
  occasion_tags: string[];
  season_tags: string[];
  price: number | null;
  currency: string;
  product_url: string | null;
  extraction_confidence: number | null;
  uncertain_fields: string[];
  verification_state: string;
  media_asset_id: string | null;
  /** Always false. A thing you are considering is not a thing you own. */
  in_inventory: false;
  note: string;
  created_at: string | null;
}

export interface OwnedReference {
  inventory_item_id: string;
  display_name: string;
  brand: string | null;
  category: InventoryCategory;
}

export interface PurchaseEvaluation {
  id: string;
  run_id: string;
  candidate: ShoppingCandidate | null;
  verdict: Verdict;
  headline: string;
  appearance_roi: AppearanceROI;
  confidence: number;
  new_combinations: number;
  summary: string;
  explanation_source: ExplanationSource;
  similar_owned_products: OwnedReference[];
  existing_alternatives: OwnedReference[];
  fit_risks: string[];
  colour_risks: string[];
  climate_notes: string[];
  missing_information: string[];
  decision: { decision: string; note: string | null; followed_recommendation: boolean; created_at: string | null } | null;
  entitlement?: Entitlement;
  disclaimer?: string;
  created_at: string | null;
}

export interface ShoppingItemInput {
  category: InventoryCategory;
  display_name: string;
  brand?: string;
  subcategory?: string;
  colour?: string;
  size?: string;
  fabric?: string;
  fit?: string;
  formality?: string;
  occasion_tags?: string[];
  season_tags?: string[];
  price?: number;
  currency?: string;
  product_url?: string;
}

export type PurchaseStrategyState = 'active' | 'inactive' | 'prohibited';
export type PurchaseStrategyKey = 'style_purchase' | 'care_purchase' | 'fragrance_purchase' | 'supplement_purchase';

export interface PurchaseStrategyCategory {
  key: InventoryCategory;
  label: string;
}

export interface PurchaseStrategy {
  key: PurchaseStrategyKey;
  label: string;
  state: PurchaseStrategyState;
  categories: PurchaseStrategyCategory[];
}

export interface PurchaseStrategiesResponse {
  purchase_strategy_registry_version: string;
  strategies: PurchaseStrategy[];
  fragrance_context_options?: {
    occasions: { key: string; label: string }[];
    seasons: { key: string; label: string }[];
  };
}

export interface CarePurchaseItemInput {
  category: 'beauty' | 'hair';
  display_name: string;
  brand?: string;
  details?: CareCandidateDetails;
  price?: number;
  currency?: string;
  product_url?: string;
}

export interface FragrancePurchaseItemInput {
  category: 'perfumes';
  display_name: string;
  brand?: string;
  subcategory?: string;
  details?: FragranceCandidateDetails;
  price?: number;
  currency?: string;
  product_url?: string;
}

export interface FragranceCandidateDetails {
  fragrance_family?: string | null;
  concentration?: string | null;
  season?: string[];
  occasion?: string[];
  longevity_user_reported?: string | null;
}

export interface CareCandidateDetails {
  product_type?: string | null;
  size?: string | null;
  purpose?: string | null;
  ingredients_text?: string | null;
  active_ingredients?: string[];
}

export interface CareCandidateConfirmInput {
  display_name?: string | null;
  brand?: string | null;
  subcategory?: string | null;
  details?: CareCandidateDetails | null;
  price?: number | null;
  currency?: string | null;
  product_url?: string | null;
}

export interface CareCandidate {
  id: string;
  source: string;
  category: 'beauty' | 'hair';
  subcategory: string | null;
  display_name: string;
  brand: string | null;
  details: CareCandidateDetails;
  price: number | null;
  currency: string;
  product_url: string | null;
  media_asset_id: string | null;
  verification_state: string;
  uncertain_fields: string[];
  extraction_confidence: number | null;
  ai_run_id: string | null;
  model_version: string | null;
  prompt_version: string | null;
  schema_version: string | null;
  in_inventory: false;
}

export interface CareCandidateInspection {
  candidate_truth_version: string;
  care_purchase_candidate_schema_version: string;
  candidate: CareCandidate;
  review_required: boolean;
  facts_trusted: boolean;
  care_slot: string | null;
  missing_information: string[];
  recognised_ingredient_keys: string[];
  recognised_ingredient_families: string[];
  note: string;
}

export interface FragranceCandidate {
  id: string;
  source: string;
  category: 'perfumes';
  subcategory: string | null;
  display_name: string;
  brand: string | null;
  details: FragranceCandidateDetails;
  price: number | null;
  currency: string;
  product_url: string | null;
  media_asset_id: string | null;
  verification_state: string;
  uncertain_fields: string[];
  extraction_confidence: number | null;
  ai_run_id: string | null;
  model_version: string | null;
  prompt_version: string | null;
  schema_version: string | null;
  in_inventory: false;
}

export interface FragranceCandidateInspection {
  candidate_truth_version: string;
  fragrance_purchase_candidate_schema_version: 'v3-05.9';
  candidate: FragranceCandidate;
  review_required: boolean;
  facts_trusted: boolean;
  normalised_fragrance_family: string | null;
  missing_information: string[];
  note: string;
}

export interface CareAssessmentDimension {
  status?: string;
  care_slot?: string | null;
  required?: boolean;
  is_gap?: boolean;
  missing_information?: string[];
  eligible_owned_same_slot_count?: number;
  selected_owned_item_id?: string | null;
  eligible_owned_same_slot?: { owned_item_id: string; display_name: string; brand?: string | null }[];
  findings?: CareCompatibilityFinding[];
  [key: string]: unknown;
}

export interface CareCompatibilityFinding {
  rule_id?: string;
  severity?: string;
  headline?: string;
  guidance?: string;
  owned_item_display_name?: string | null;
  [key: string]: unknown;
}

export interface CareAssessment {
  assessment_version?: string;
  schema_version?: string;
  account_id?: string;
  candidate_id?: string;
  category?: 'beauty' | 'hair';
  plan_date: string;
  assessment_fingerprint: string;
  dimensions: {
    identity_confidence?: CareAssessmentDimension;
    role_utility?: CareAssessmentDimension;
    redundancy?: CareAssessmentDimension;
    compatibility?: CareAssessmentDimension;
    [key: string]: CareAssessmentDimension | undefined;
  };
  user_constraints?: CareAssessmentDimension;
  [key: string]: unknown;
}

export interface CareEvidenceSource {
  source_id?: string;
  title?: string | null;
  publisher?: string | null;
  [key: string]: unknown;
}

export interface CareEvidenceFinding {
  claim_key?: string;
  claim_summary?: string;
  evidence_strength?: string | null;
  claim_status?: string | null;
  sources: CareEvidenceSource[];
  [key: string]: unknown;
}

export interface CareEvidence {
  account_id?: string;
  candidate_id?: string;
  category?: 'beauty' | 'hair';
  plan_date?: string;
  assessment_fingerprint: string;
  evidence_support: {
    status?: string;
    reviewed_context?: boolean;
    findings: CareEvidenceFinding[];
    [key: string]: unknown;
  };
  ingredient_utility?: {
    status?: string;
    findings: CareEvidenceFinding[];
    [key: string]: unknown;
  };
  projection_fingerprint?: string;
  [key: string]: unknown;
}

export interface CareValueRecoveryItem {
  owned_item_id: string;
  display_name?: string;
  estimated_value?: string | number | null;
  currency?: string | null;
  explanation?: string | null;
  [key: string]: unknown;
}

export interface CareValue {
  account_id?: string;
  candidate_id?: string;
  category?: 'beauty' | 'hair';
  plan_date?: string;
  assessment_fingerprint: string;
  value_context: {
    status?: string;
    candidate_spend?: { status?: string; [key: string]: unknown };
    owned_value_recovery?: { status?: string; items: CareValueRecoveryItem[]; [key: string]: unknown };
    currency_context?: { status?: string; [key: string]: unknown };
    [key: string]: unknown;
  };
  value_fingerprint: string;
  [key: string]: unknown;
}

export interface CareVerdict {
  account_id?: string;
  candidate_id?: string;
  category?: 'beauty' | 'hair';
  plan_date?: string;
  assessment_fingerprint: string;
  evidence_projection_fingerprint?: string;
  value_fingerprint: string;
  verdict: Verdict;
  headline: string;
  explanation: string;
  primary_reason_code: string;
  reason_codes: string[];
  supporting_reason_codes: string[];
  decision_context: {
    candidate_spend_status?: string;
    owned_value_recovery_status?: string;
    currency_context_status?: string;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface CarePurchaseCheck {
  care_purchase_check_version: 'v3-05.7';
  strategy: 'care_purchase';
  candidate_truth: CareCandidateInspection;
  assessment: CareAssessment;
  evidence: CareEvidence;
  value: CareValue;
  verdict: CareVerdict;
  decision?: PurchaseDecisionMemory | null;
}

export type PurchaseDecisionValue = 'bought' | 'waiting' | 'skipped';

export interface PurchaseDecisionMemory {
  purchase_decision_memory_version: 'v3-05.8';
  id: string;
  candidate_id: string;
  strategy: 'style_purchase' | 'care_purchase' | 'fragrance_purchase';
  evaluation_id: string | null;
  recommendation_at_decision: {
    verdict: Verdict;
    version: string;
    fingerprint: string | null;
  };
  decision: PurchaseDecisionValue;
  note: string | null;
  followed_recommendation: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface FragranceOwnedOption {
  owned_item_id: string;
  display_name: string;
  brand: string | null;
  fragrance_family?: string | null;
  normalised_fragrance_family?: string | null;
  concentration?: string | null;
  season?: string[];
  occasion?: string[];
  remaining_percent?: number | null;
  usage_count?: number;
  last_used_at?: string | null;
}

export interface FragrancePurchaseVerdict {
  fragrance_purchase_verdict_version: 'v3-05.9';
  verdict: Verdict;
  headline: string;
  explanation: string;
  primary_reason_code: string;
  supporting_reason_codes: string[];
  decision_fingerprint: string;
  normalised_candidate_family: string | null;
  same_family_owned: FragranceOwnedOption[];
  owned_options_to_use_first: FragranceOwnedOption[];
  missing_information: string[];
}

export interface FragrancePurchaseCheck {
  fragrance_purchase_check_version: 'v3-05.9';
  strategy: 'fragrance_purchase';
  candidate_truth: FragranceCandidateInspection;
  collection_context: {
    owned_perfume_count: number;
    draft_perfume_count: number;
    normalised_candidate_family: string | null;
    exact_owned: FragranceOwnedOption[];
    same_family_owned: FragranceOwnedOption[];
    intended_use: { occasion: string[]; season: string[] };
    context_labels?: { occasion: Record<string, string>; season: Record<string, string> };
    coverage: { covered: string[]; unknown: string[]; uncovered: string[] };
    owned_options_to_use_first: FragranceOwnedOption[];
  };
  verdict: FragrancePurchaseVerdict;
  decision?: PurchaseDecisionMemory | null;
}

export const getROIModel = async (): Promise<{
  version: string; formula: string; note: string;
  thresholds: { buy: number; wait: number }; overrides: string[];
  factors: { key: string; label: string; weight: number }[];
}> => (await api.get(`${V2}/shopping/roi-model`)).data;

export const evaluateScreenshot = async (
  media_asset_id: string, occasion_key?: OccasionKey, price?: number
): Promise<PurchaseEvaluation> =>
  (await api.post<PurchaseEvaluation>(`${V2}/shopping/evaluate`, {
    media_asset_id, source: 'screenshot', occasion_key, price,
  })).data;

export const evaluateItemDetails = async (
  item: ShoppingItemInput, occasion_key?: OccasionKey, price?: number
): Promise<PurchaseEvaluation> =>
  (await api.post<PurchaseEvaluation>(`${V2}/shopping/evaluate`, {
    item, source: 'manual', occasion_key, price,
  })).data;

export const getPurchaseStrategies = async (): Promise<PurchaseStrategiesResponse> =>
  (await api.get<PurchaseStrategiesResponse>(`${V2}/shopping/strategies`)).data;

export const inspectPurchaseCandidate = async (body: {
  source: 'manual' | 'screenshot' | 'item_photo';
  item?: CarePurchaseItemInput | FragrancePurchaseItemInput;
  media_asset_id?: string;
  client_mutation_id?: string;
  expected_category?: InventoryCategory;
}): Promise<CareCandidateInspection | FragranceCandidateInspection> =>
  (await api.post<CareCandidateInspection | FragranceCandidateInspection>(`${V2}/shopping/candidates/inspect`, body)).data;

export const confirmPurchaseCandidate = async (
  id: string,
  body: CareCandidateConfirmInput | { display_name?: string | null; brand?: string | null; subcategory?: string | null; details?: FragranceCandidateDetails | null; price?: number | null; currency?: string | null; product_url?: string | null },
): Promise<CareCandidateInspection | FragranceCandidateInspection> =>
  (await api.post<CareCandidateInspection | FragranceCandidateInspection>(`${V2}/shopping/candidates/${id}/confirm`, body)).data;

export const getCarePurchaseCheck = async (id: string, on?: string): Promise<CarePurchaseCheck> =>
  (await api.get<CarePurchaseCheck>(`${V2}/shopping/candidates/${id}/care-check`, { params: on ? { on } : undefined })).data;

export const recordCarePurchaseDecision = async (
  id: string, decision: PurchaseDecisionValue, note?: string, on?: string
): Promise<PurchaseDecisionMemory> =>
    (await api.post<PurchaseDecisionMemory>(`${V2}/shopping/candidates/${id}/decision`, { decision, note }, { params: on ? { on } : undefined })).data;

export const getFragrancePurchaseCheck = async (id: string): Promise<FragrancePurchaseCheck> =>
  (await api.get<FragrancePurchaseCheck>(`${V2}/shopping/candidates/${id}/fragrance-check`)).data;

export const recordPurchaseCandidateDecision = async (
  id: string, decision: PurchaseDecisionValue, note?: string
): Promise<PurchaseDecisionMemory> =>
  (await api.post<PurchaseDecisionMemory>(`${V2}/shopping/candidates/${id}/decision`, { decision, note })).data;

export const getPurchaseDecision = async (id: string): Promise<{ purchase_decision_memory_version: 'v3-05.8'; decision: PurchaseDecisionMemory | null }> =>
  (await api.get(`${V2}/shopping/candidates/${id}/decision`)).data;

export const getEvaluation = async (id: string): Promise<PurchaseEvaluation> =>
  (await api.get<PurchaseEvaluation>(`${V2}/shopping/evaluations/${id}`)).data;

export const recordPurchaseDecision = async (
  id: string, decision: PurchaseDecisionValue, note?: string
): Promise<PurchaseEvaluation> =>
  (await api.post<PurchaseEvaluation>(`${V2}/shopping/evaluations/${id}/decision`, { decision, note })).data;

// --- Phase 5: the Today engine and the weekly planner ----------------------

export const PLAN_MODULES = [
  'outfit', 'skincare', 'hair', 'perfume', 'hydration', 'nutrition', 'shopping',
] as const;
export type PlanModule = typeof PLAN_MODULES[number];

/** `cache` is the good case: nothing material changed, so nothing was rebuilt. */
export type PlanSource = 'cache' | 'fresh';

export type LaundryState = 'clean' | 'worn' | 'in_wash' | 'unavailable';

export interface PlanAction {
  id: string;
  module: PlanModule;
  action_type: string;
  title: string;
  body: string;
  priority: number;
  /** Why this appeared today. Every optional module carries one. */
  relevance: string;
  inventory_item_id: string | null;
  completed: boolean;
  completed_at: string | null;
}

export interface WeatherSnapshot {
  id: string;
  for_date: string;
  condition: WeatherCondition;
  temp_min_c: number | null;
  temp_max_c: number | null;
  precipitation_chance: number | null;
  humidity: number | null;
  location: string | null;
  provider: string;
  source: string;
  attribution?: string;
}

export interface Clarification {
  key: string;
  question: string;
  why: string;
  options: { value: string; label: string }[];
}

export interface DailyPlan {
  plan_date: string;
  timezone: string;
  weekday: string;
  status: 'ready' | 'needs_inventory';
  headline: string;
  confidence: number;
  generated_from: PlanSource;
  engine_version: string;
  used_llm: boolean;
  locked: boolean;
  version: number;
  outfit: Look | null;
  weather: WeatherSnapshot | null;
  weather_note: string;
  event_note: string;
  /** The short list Today opens with. */
  primary: PlanAction[];
  /** Shown underneath, and only when they have something to say. */
  optional_modules: PlanAction[];
  needs_clarification: boolean;
  clarification: Clarification | null;
  missing_information: string[];
  worn: boolean;
  computed_at: string | null;
  disclaimer: string;
  recalculations?: { trigger: string; detail: string; recomputed: boolean; created_at: string | null }[];
}

export interface PlannerDay {
  plan_date: string;
  weekday: string;
  locked: boolean;
  note: string | null;
  headline: string | null;
  status: string;
  confidence: number | null;
  weather: WeatherSnapshot | null;
  owned_items: LookPiece[];
  optional_addition_count?: number;
  needs_clarification?: boolean;
  worn?: boolean;
}

export interface WeeklyPlan {
  week_start: string;
  week_end?: string;
  timezone?: string;
  status: string;
  version?: number;
  engine_version?: string;
  repetition_window_days?: number;
  days: PlannerDay[];
  repetition?: {
    repeated_items: { display_name: string; dates: string[]; times: number }[];
    note: string;
  };
  laundry?: { item_id: string; state: LaundryState; available_from: string | null }[];
  generated_at?: string | null;
  message?: string;
}

export interface CalendarEvent {
  id: string;
  title: string;
  starts_at: string;
  local_time: string;
  local_date: string;
  ends_at: string | null;
  all_day: boolean;
  location: string | null;
  occasion_key: OccasionKey | null;
  dress_code_hint: string | null;
  inference_confidence: number;
  user_confirmed: boolean;
  provider: string;
  source: string;
  status: string;
}

export interface CalendarEventInput {
  title: string;
  starts_at: string;
  ends_at?: string;
  all_day?: boolean;
  location?: string;
  occasion_key?: OccasionKey;
  dress_code_hint?: string;
}

export interface CalendarEventPatchInput {
  title?: string;
  occasion_key?: OccasionKey;
  dress_code_hint?: string;
  status?: 'active' | 'dismissed';
}

export interface UpcomingEvents {
  timezone: string;
  events: CalendarEvent[];
}

export type EventReadyStatus = 'not_generated' | 'needs_confirmation' | 'preparing' | 'event_day' | 'past';
export type EventReadyActionDomain = 'context' | 'style' | 'care' | 'preparation';
export type EventReadyActionTiming = 'now' | 'before_event' | 'event_day';
export type HairWashCadenceStatus = 'due' | 'not_due' | 'needs_anchor' | 'unscheduled';

export interface EventReadyAction {
  id: string;
  action_key: string;
  domain: EventReadyActionDomain;
  timing: EventReadyActionTiming;
  title: string;
  body: string;
  relevance: string;
  inventory_item_id: string | null;
  completed: boolean;
  completed_at: string | null;
}

export interface EventReadyCare {
  authority: 'care';
  decision_version: string;
  decision_fingerprint: string;
  routine_plan_version: string;
  routine_plan_fingerprint: string;
  resolved_effort: string;
  active_skin_slot_count: number;
  active_hair_slot_count: number;
  skin_gap_count: number;
  hair_gap_count: number;
  hair_wash: {
    version: string;
    status: HairWashCadenceStatus;
    reason: string;
    declared_frequency: string | null;
    last_wash_on: string | null;
    next_due_on: string | null;
    fingerprint: string;
  };
}

export interface EventReady {
  event_ready_version: 'vc-02-v1';
  event: CalendarEvent;
  status: EventReadyStatus;
  countdown: { days_until: number; event_local_date: string };
  context: { weather: { condition: string; temp_min_c: number | null; temp_max_c: number | null; precipitation_chance: number | null; humidity: number | null; location: string | null; attribution?: string } | null; air_quality: { aqi: number; index_system: string; category: string | null; location: string | null; attribution?: string } | null };
  style: { authority: 'style'; status: 'blocked_by_event_confirmation' | 'needs_look' | 'look_selected' | 'look_needs_review'; selected_look: { id: string; title: string; status: string } | null };
  care: EventReadyCare | null;
  timeline: EventReadyAction[];
  readiness: { completed_actions: number; total_actions: number; all_done: boolean };
  missing_information: string[];
  event_ready_fingerprint: string;
}

export interface CalendarStatus {
  connected: boolean;
  integrations: {
    id: string; provider: string; status: string; label: string | null;
    last_synced_at: string | null; revoked_at: string | null; last_error: string | null;
    /** Always false. No access token is ever held in the app database. */
    stores_credentials: false;
  }[];
  providers: { key: string; label: string; available: boolean }[];
  note: string;
  events_added?: number;
  duplicates_ignored?: number;
  revoked?: number;
  message?: string;
}

export interface GoogleCalendarAuthorization {
  authorization_url: string;
  expires_at: string;
}

export interface NotificationPreferences {
  enabled: boolean;
  daily_cap: number;
  quiet_hours: { start: number; end: number };
  preferred_hour: number;
  modules: Record<PlanModule, boolean>;
  timezone: string;
  note: string;
}

export const getToday = async (plan_date?: string): Promise<DailyPlan> =>
  (await api.get<DailyPlan>(`${V2}/today`, { params: plan_date ? { plan_date } : undefined })).data;

export const regenerateToday = async (
  reason?: 'weather_changed' | 'plans_changed' | 'not_my_style' | 'item_unavailable' | 'manual',
  plan_date?: string
): Promise<DailyPlan> =>
  (await api.post<DailyPlan>(`${V2}/today/regenerate`, { reason, plan_date })).data;

export const completePlanAction = async (id: string, completed = true): Promise<DailyPlan> =>
  (await api.post<DailyPlan>(`${V2}/today/actions/${id}/complete`, { completed })).data;

export const swapTodayItem = async (
  slot: LookSlot, to_item_id: string | null, from_item_id?: string, plan_date?: string
): Promise<DailyPlan> =>
  (await api.post<DailyPlan>(`${V2}/today/outfit/swap`, { slot, to_item_id, from_item_id, plan_date })).data;

export const sendTodayFeedback = async (
  rating: 'wore_it' | 'loved' | 'not_for_me' | 'changed_it', reason?: string, plan_date?: string
): Promise<DailyPlan> =>
  (await api.post<DailyPlan>(`${V2}/today/feedback`, { rating, reason, plan_date })).data;

export const answerClarification = async (
  question_key: string, answer: string, plan_date?: string
): Promise<DailyPlan> =>
  (await api.post<DailyPlan>(`${V2}/today/clarify`, { question_key, answer, plan_date })).data;

export const reportItemUnavailable = async (
  item_id: string, state: LaundryState = 'in_wash', available_from?: string
): Promise<DailyPlan> =>
  (await api.post<DailyPlan>(`${V2}/today/items/unavailable`, { item_id, state, available_from })).data;

export const setTodayWeather = async (
  for_date: string, condition: WeatherCondition, precipitation_chance?: number
): Promise<{ weather: WeatherSnapshot; plan: DailyPlan }> =>
  (await api.post(`${V2}/today/weather`, { for_date, condition, precipitation_chance })).data;

export const addPlannerEvent = async (
  title: string, starts_at: string, occasion_key?: OccasionKey
): Promise<{ event: CalendarEvent; created: boolean; plan: DailyPlan }> =>
  (await api.post(`${V2}/today/events`, { title, starts_at, occasion_key })).data;

export const addCalendarEvent = async (
  body: CalendarEventInput,
): Promise<{ event: CalendarEvent; created: boolean; plan: DailyPlan }> =>
  (await api.post<{ event: CalendarEvent; created: boolean; plan: DailyPlan }>(`${V2}/today/events`, body)).data;

export const patchCalendarEvent = async (
  eventId: string, body: CalendarEventPatchInput,
): Promise<CalendarEvent> =>
  (await api.patch<CalendarEvent>(`${V2}/integrations/calendar/events/${eventId}`, body)).data;

export const getWeek = async (week_start?: string): Promise<WeeklyPlan> =>
  (await api.get<WeeklyPlan>(`${V2}/planner/week`, { params: week_start ? { week_start } : undefined })).data;

export const getUpcomingEvents = async (
  days = 90, limit = 20,
): Promise<UpcomingEvents> =>
  (await api.get<UpcomingEvents>(`${V2}/planner/events/upcoming`, { params: { days, limit } })).data;

export const getEventReady = async (eventId: string): Promise<EventReady> =>
  (await api.get<EventReady>(`${V2}/planner/events/${eventId}/ready`)).data;

export const generateEventReady = async (eventId: string): Promise<EventReady> =>
  (await api.post<EventReady>(`${V2}/planner/events/${eventId}/ready/generate`)).data;

export const setEventReadyLook = async (eventId: string, lookId: string | null): Promise<EventReady> =>
  (await api.patch<EventReady>(`${V2}/planner/events/${eventId}/ready/look`, { look_id: lookId })).data;

export const setEventReadyActionComplete = async (eventId: string, actionId: string, completed: boolean): Promise<EventReady> =>
  (await api.post<EventReady>(`${V2}/planner/events/${eventId}/ready/actions/${actionId}/complete`, { completed })).data;

export const generateWeek = async (
  week_start?: string, repetition_window_days = 7
): Promise<WeeklyPlan> =>
  (await api.post<WeeklyPlan>(`${V2}/planner/week/generate`, { week_start, repetition_window_days })).data;

export const patchPlannerDay = async (
  plan_date: string, body: { swap_with_date?: string; regenerate?: boolean; note?: string }
): Promise<WeeklyPlan> =>
  (await api.patch<WeeklyPlan>(`${V2}/planner/day/${plan_date}`, body)).data;

export const lockPlannerDay = async (plan_date: string, locked = true): Promise<WeeklyPlan> =>
  (await api.post<WeeklyPlan>(`${V2}/planner/day/${plan_date}/lock`, { locked })).data;

export const getCalendarStatus = async (): Promise<CalendarStatus> =>
  (await api.get<CalendarStatus>(`${V2}/integrations/calendar/status`)).data;

export const connectCalendar = async (
  provider = 'manual', events: { title: string; starts_at: string }[] = [], label?: string
): Promise<CalendarStatus> =>
  (await api.post<CalendarStatus>(`${V2}/integrations/calendar/connect`, { provider, events, label })).data;

export const disconnectCalendar = async (): Promise<CalendarStatus> =>
  (await api.delete<CalendarStatus>(`${V2}/integrations/calendar`)).data;

export const authorizeGoogleCalendar = async (): Promise<GoogleCalendarAuthorization> =>
  (await api.post<GoogleCalendarAuthorization>(`${V2}/integrations/calendar/google/authorize`)).data;

export const syncGoogleCalendar = async (): Promise<{ connected: boolean; synced: boolean; reason?: string; created?: number; updated?: number; revoked?: number }> =>
  (await api.post<{ connected: boolean; synced: boolean; reason?: string; created?: number; updated?: number; revoked?: number }>(`${V2}/integrations/calendar/google/sync`)).data;

export const disconnectGoogleCalendar = async (): Promise<{ status: string; revoked: boolean; message: string }> =>
  (await api.delete<{ status: string; revoked: boolean; message: string }>(`${V2}/integrations/calendar/google`)).data;

export const getNotificationPreferences = async (): Promise<{
  preferences: NotificationPreferences;
  recent: { id: string; title: string; status: string; suppressed_reason: string | null }[];
}> => (await api.get(`${V2}/today/notifications`)).data;

export const patchNotificationPreferences = async (
  body: Partial<{ enabled: boolean; daily_cap: number; preferred_hour: number; modules: Record<string, boolean> }>
): Promise<{ preferences: NotificationPreferences }> =>
  (await api.patch(`${V2}/today/notifications`, body)).data;

// --- Phase 6: routines, shelf, perfume, supplements, food context -----------
// Every warning carries the id of a reviewed rule. The client never invents
// one, and it never renders a warning that arrived without one.

export type RoutineKind = 'morning' | 'evening' | 'wash_day' | 'weekly' | 'event';
export type Severity = 'info' | 'caution' | 'avoid';
export type Diet = 'vegan' | 'vegetarian' | 'jain' | 'eggetarian' | 'pescatarian' | 'non_vegetarian';

export interface RuleWarning {
  /** Always present. A warning with no reviewed rule behind it does not exist. */
  rule_id: string;
  severity: Severity;
  headline: string;
  detail: string;
  evidence_note: string;
  item_ids: string[];
  slot: string | null;
  plain_english?: string;
}

export interface RoutineStep {
  id?: string;
  slot: string;
  label: string;
  order: number;
  required: boolean;
  optional: boolean;
  why: string;
  frequency: string;
  inventory_item_id: string | null;
  product_name: string | null;
  owned: boolean;
  safety_note: string;
  alternative: string;
  climate_note: string;
  /** A required step with nothing owned for it. Names a category, never a product. */
  is_gap: boolean;
  completed_today?: boolean;
  plain_english?: string;
}

export interface Routine {
  id?: string;
  kind: RoutineKind;
  label: string;
  frequency: string;
  version?: number;
  steps: RoutineStep[];
  warnings: RuleWarning[];
  climate_notes: { rule_id: string; slot: string; note: string }[];
  skipped_for_allergy: string[];
  summary?: string;
  disclaimer: string;
}

export interface CareProductControl {
  inventory_item_id: string;
  display_name: string;
  category: 'skin_care' | 'hair_care';
  slot: string | null;
  paused: boolean;
  preferred: boolean;
  eligible: boolean;
}

export interface CareGuidanceItem {
  domain: 'skin_care' | 'hair_care';
  rule_id: string;
  rule_version: string;
  priority: number;
  title: string;
  body: string;
  trigger_codes: string[];
  evidence_claim_ids: string[];
  evidence_applicability_version: string;
}

export interface CareGuidance {
  guidance_version: string;
  ruleset_version: string;
  fingerprint: string;
  items: CareGuidanceItem[];
}

export interface HomeCareItem {
  domain: 'home_care';
  rule_id: string;
  rule_version: string;
  priority: number;
  title: string;
  body: string;
  trigger_codes: string[];
  evidence_claim_ids: string[];
  evidence_applicability_version: string;
}

export interface HomeCare {
  home_care_version: string;
  ruleset_version: string;
  fingerprint: string;
  items: HomeCareItem[];
}

// Explicit Care experience feedback is a user-owned record. These types are
// deliberately separate from generic Progress feedback: recording an
// experience never changes a routine, a recommendation, or a memory.
export type CareExperienceFeedbackSubjectType = 'product' | 'routine_step';
export type CareExperienceFeedbackDimension =
  | 'overall_experience'
  | 'comfort'
  | 'ease_of_use'
  | 'routine_fit';
export type CareExperienceFeedbackSentiment = 'positive' | 'neutral' | 'negative';

export interface CareExperienceFeedback {
  id: string;
  feedback_version: 'v3-03.13';
  subject_type: CareExperienceFeedbackSubjectType;
  subject_id: string;
  routine_kind: RoutineKind | null;
  routine_slot: string | null;
  dimension: CareExperienceFeedbackDimension;
  sentiment: CareExperienceFeedbackSentiment;
  note: string | null;
  experienced_on: string;
  created_at: string | null;
}

export interface CareExperienceFeedbackRecord extends CareExperienceFeedback {
  affects_recommendations: false;
  creates_memory: false;
  changes_care_safety: false;
  message: string;
}

export interface CareExperienceFeedbackInput {
  subject_type: CareExperienceFeedbackSubjectType;
  subject_id: string;
  dimension: CareExperienceFeedbackDimension;
  sentiment: CareExperienceFeedbackSentiment;
  note?: string;
  /** Supported by the server for reviewed backdated workflows, but omitted by the normal UX. */
  experienced_on?: string;
}

export interface ShelfProductRow {
  inventory_item_id: string;
  display_name: string;
  brand: string | null;
  category: string;
  slot: string | null;
  slot_label: string | null;
  effective_expiry: string | null;
  days_to_expiry: number | null;
  low_use: boolean;
  usage_count: number;
  remaining_percent: number | null;
  ingredients: { ingredient_key: string; display_name: string; needs_confirmation: boolean }[];
  needs_confirmation: { ingredient_key: string; display_name: string }[];
  rule_id?: string;
}

export interface ShelfSummary {
  categories: Record<string, { product_count: number; slots_filled: string[]; warning_count: number }>;
  counts: {
    products: number; avoid: number; caution: number;
    needs_attention: number; awaiting_confirmation: number; drafts: number;
  };
  needs_attention: RuleWarning[];
  draft_note: string | null;
}

export interface ExpiringReport {
  window_days: number;
  expired: ShelfProductRow[];
  expiring_soon: ShelfProductRow[];
  no_date_recorded: ShelfProductRow[];
  note: string;
}

export interface PerfumePick {
  inventory_item_id: string;
  display_name: string;
  brand: string | null;
  fragrance_family: string | null;
  remaining_percent: number | null;
  reasons: { rule_id: string; factor: string; note: string }[];
  missing_information: string[];
  owned: boolean;
}

export interface SupplementRow {
  inventory_item_id: string;
  display_name: string;
  brand: string | null;
  user_entered_purpose: string | null;
  use_frequency: string | null;
  expiry_date: string | null;
  days_to_expiry: number | null;
  flags: { flag: string; message: string }[];
}

export interface NutritionSuggestion {
  rule_id: string;
  rule_version: string;
  title: string;
  body: string;
  trigger_codes: string[];
  food_options: string[];
}

export interface NutritionAppearanceResponse {
  enabled: boolean;
  diet?: Diet;
  diet_label?: string;
  guidance_version?: string;
  ruleset_version?: string;
  fingerprint?: string;
  food_options_version?: string;
  food_first?: boolean;
  suggestions: NutritionSuggestion[];
  boundaries?: string[];
  disclaimer: string;
  message?: string;
}

export interface ImproveOverview {
  has_shelf: boolean;
  has_routines: boolean;
  routines: Routine[];
  consistency: { days_considered: number; days_with_activity: number; steps_completed: number; note: string };
  needs_attention: RuleWarning[];
  expiring: ExpiringReport;
  low_use: { rule_id: string; products: ShelfProductRow[]; count: number; definition: string; note: string };
  missing_categories: RuleWarning[];
  counts: ShelfSummary['counts'];
  routine_effort?: { resolved: 'minimal' | 'balanced' | 'detailed' | 'not_sure'; source: string; can_simplify: boolean; next_simpler: string | null };
  care_product_controls: CareProductControl[];
  disclaimer: string;
}

export const analyseShelf = async (climate?: string): Promise<ShelfSummary> =>
  (await api.post<ShelfSummary>(`${V2}/shelf/analyse`, climate ? { climate } : {})).data;

export const getShelfSummary = async (): Promise<ShelfSummary> =>
  (await api.get<ShelfSummary>(`${V2}/shelf/summary`)).data;

export const getShelfExpiring = async (days = 60): Promise<ExpiringReport> =>
  (await api.get<ExpiringReport>(`${V2}/shelf/expiring`, { params: { days } })).data;

export const getShelfLowUse = async (): Promise<ImproveOverview['low_use']> =>
  (await api.get(`${V2}/shelf/low-use`)).data;

export const getShelfValueToRecover = async (): Promise<{
  estimated_total: number; currency: string; is_estimate: boolean;
  items: { item_id: string; display_name: string; estimated_value: number | null }[];
  items_missing_price: number; explanation: string;
}> => (await api.get(`${V2}/shelf/value-to-recover`)).data;

export const generateRoutines = async (
  body: { kinds?: RoutineKind[]; climate?: string; explain?: boolean } = {}
): Promise<{
  routines: Routine[];
  explanation_source: ExplanationSource;
  care_guidance: CareGuidance;
  home_care: HomeCare;
  message: string | null;
  disclaimer: string;
}> =>
  (await api.post(`${V2}/routines/generate`, body)).data;

export const getRoutinesToday = async (): Promise<{
  date: string; part_of_day: string; routines: Routine[]; message: string | null; disclaimer: string; care_guidance: CareGuidance; home_care: HomeCare;
}> => (await api.get(`${V2}/routines/today`)).data;

export const completeRoutineStep = async (
  stepId: string, completed = true, done_on?: string
): Promise<{ step_id: string; completed: boolean; note: string }> =>
  (await api.post(`${V2}/routines/steps/${stepId}/complete`, { completed, done_on })).data;

export const getImproveOverview = async (): Promise<ImproveOverview> =>
  (await api.get<ImproveOverview>(`${V2}/routines/improve`)).data;

export interface CareRoutineMutationResponse {
  product_preference_version?: string;
  selection_preference_version?: string;
  simplification_version?: string;
  changed: boolean;
  status: string;
  inventory_item_id?: string;
  display_name?: string;
  category?: 'skin_care' | 'hair_care';
  slot?: string | null;
  message: string;
}

export const simplifyCareRoutine = async (): Promise<CareRoutineMutationResponse> =>
  (await api.post<CareRoutineMutationResponse>(`${V2}/routines/simplify`)).data;

export const pauseCareProduct = async (itemId: string): Promise<CareRoutineMutationResponse> =>
  (await api.post<CareRoutineMutationResponse>(`${V2}/routines/products/${itemId}/pause`)).data;

export const resumeCareProduct = async (itemId: string): Promise<CareRoutineMutationResponse> =>
  (await api.post<CareRoutineMutationResponse>(`${V2}/routines/products/${itemId}/resume`)).data;

export const preferCareProduct = async (itemId: string): Promise<CareRoutineMutationResponse> =>
  (await api.post<CareRoutineMutationResponse>(`${V2}/routines/products/${itemId}/prefer`)).data;

export const unpreferCareProduct = async (itemId: string): Promise<CareRoutineMutationResponse> =>
  (await api.post<CareRoutineMutationResponse>(`${V2}/routines/products/${itemId}/unprefer`)).data;

export const recordCareExperienceFeedback = async (
  body: CareExperienceFeedbackInput
): Promise<CareExperienceFeedbackRecord> => {
  // Do not add a client date. The server owns the canonical local date when
  // experienced_on is omitted by the normal capture flow.
  const { experienced_on, ...normal } = body;
  const payload = experienced_on === undefined ? normal : { ...normal, experienced_on };
  return (await api.post<CareExperienceFeedbackRecord>(`${V2}/routines/experience-feedback`, payload)).data;
};

export const listCareExperienceFeedback = async (
  subject_type: CareExperienceFeedbackSubjectType,
  subject_id: string,
  limit = 50,
): Promise<{ feedback: CareExperienceFeedback[] }> =>
  (await api.get<{ feedback: CareExperienceFeedback[] }>(`${V2}/routines/experience-feedback`, {
    params: { subject_type, subject_id, limit },
  })).data;

export const deleteCareExperienceFeedback = async (
  feedback_id: string,
): Promise<{ deleted: true; id: string }> =>
  (await api.delete<{ deleted: true; id: string }>(`${V2}/routines/experience-feedback/${feedback_id}`)).data;

export const checkIngredients = async (body: {
  label_text?: string; ingredients?: string[]; item_ids?: string[];
  against_owned?: boolean; explain?: boolean;
}): Promise<{
  identified: { ingredient_key: string; display_name: string; needs_confirmation: boolean }[];
  unidentified: string[];
  warnings: RuleWarning[];
  needs_confirmation: { ingredient_key: string; display_name: string }[];
  note: string;
}> => (await api.post(`${V2}/ingredients/check`, body)).data;

export const confirmIngredients = async (
  item_id: string, ingredient_keys: string[], confirmed = true
): Promise<{ updated: number; confirmed: boolean; note: string }> =>
  (await api.post(`${V2}/ingredients/confirm`, { item_id, ingredient_keys, confirmed })).data;

export const getIngredient = async (key: string): Promise<{
  ingredient_key: string; display_name: string; inci_name: string | null; family: string;
  summary: string; common_use: string; aliases: string[];
  rules: { rule_id: string; severity: Severity; headline: string; guidance: string }[];
  note: string;
}> => (await api.get(`${V2}/ingredients/${encodeURIComponent(key)}`)).data;

export const getPerfumeRecommendation = async (params: {
  occasion_key?: string; weather?: string; time_of_day?: string; season?: string;
} = {}): Promise<{
  recommendations: PerfumePick[]; considered: number;
  missing_information: string[]; note: string; message: string | null;
}> => (await api.get(`${V2}/perfume/recommendation`, { params })).data;

export const getSupplementsSummary = async (): Promise<{
  supplements: SupplementRow[]; count: number;
  tracked_fields: string[]; we_do_not: string[];
  disclaimer: string; message: string | null;
}> => (await api.get(`${V2}/supplements/summary`)).data;

export const getNutritionSuggestions = async (): Promise<NutritionAppearanceResponse> =>
  (await api.get<NutritionAppearanceResponse>(`${V2}/nutrition/appearance-suggestions`)).data;

export const patchNutritionPreferences = async (body: {
  enabled?: boolean; diet?: Diet; focus_nutrients?: string[]; avoid_foods?: string[];
}): Promise<{ enabled: boolean; diet: Diet }> =>
  (await api.patch(`${V2}/nutrition/preferences`, body)).data;

export const getHydrationPreferences = async (): Promise<{
  enabled: boolean; remind_in_hot_weather_only: boolean; note: string | null; no_target: string;
}> => (await api.get(`${V2}/nutrition/hydration`)).data;

export const patchHydrationPreferences = async (body: {
  enabled?: boolean; remind_in_hot_weather_only?: boolean; note?: string;
}): Promise<{ enabled: boolean }> => (await api.patch(`${V2}/nutrition/hydration`, body)).data;

export const recordObservation = async (
  note: string, area: 'skin' | 'hair' | 'scalp' | 'nails' | 'general' = 'general'
): Promise<{ id: string; note: string; boundary: { boundary: boolean; message: string } | null; message: string }> =>
  (await api.post(`${V2}/routines/observations`, { note, area })).data;

// --- Phase 7: progress, goals, memory and milestones -----------------------
// Every metric carries its own formula and formula version. There is no
// overall score in this API, and the registry is built so one cannot be added.

export type MetricUnit = 'ratio' | 'count' | 'currency' | 'days' | 'scale_1_5';
export type MetricDirection = 'higher_is_better' | 'lower_is_better' | 'neutral';
export type MetricStatus = 'ok' | 'partial' | 'unavailable';
export type MemoryVerification = 'unverified' | 'confirmed' | 'corrected' | 'rejected';
export type GoalKind = 'no_buy' | 'use_up' | 'routine' | 'wardrobe' | 'custom';

export interface Metric {
  key: string;
  label: string;
  value: number | null;
  unit: MetricUnit;
  direction: MetricDirection;
  status: MetricStatus;
  /** Always present. A number without one of these is not shown. */
  formula: string;
  formula_version: string;
  explanation: string;
  /** The misreading this metric most needs to head off. */
  not_a_measure_of: string;
  update_frequency: string;
  inputs: Record<string, unknown>;
  missing_inputs: string[];
  note: string;
}

export interface Goal {
  id: string;
  kind: GoalKind;
  title: string;
  metric_key: string | null;
  metric_status: MetricStatus | null;
  starting_value: number | null;
  current_value: number | null;
  target_value: number | null;
  progress: number | null;
  starts_on: string;
  target_date: string | null;
  status: string;
  note: string | null;
  updates: { recorded_on: string; value: number | null; source: string; note: string | null }[];
  progress_note: string | null;
}

export interface EarnedMilestone {
  id: string;
  rule_id: string;
  label: string;
  description: string;
  earned_on: string;
  evidence: Record<string, unknown>;
  acknowledged: boolean;
}

export interface ProgressOverview {
  period: 'week' | 'month';
  period_start: string;
  period_end: string;
  metrics: Metric[];
  available_count: number;
  unavailable_count: number;
  /** Always true. Stated by the server so a client cannot imply otherwise. */
  no_overall_score: boolean;
  no_overall_score_note: string;
  goals: Goal[];
  milestones: EarnedMilestone[];
}

export interface MemoryEvidence {
  source: string;
  source_label: string;
  observed_at: string | null;
  evidence: Record<string, unknown>;
}

export interface MemoryFact {
  id: string;
  category: string;
  category_label: string;
  fact: string;
  source: string;
  source_label: string;
  confidence: number;
  created_at: string | null;
  last_reinforced_at: string | null;
  reinforcement_count: number;
  verification_state: MemoryVerification;
  deletion_state: 'active' | 'deleted';
  deleted_at: string | null;
  /** Whether this fact is currently shaping what the app suggests. */
  influences_recommendations: boolean;
  linked_evidence: MemoryEvidence[];
  why_we_remember: string;
  category_enabled: boolean;
}

export interface MemoryCategorySummary {
  key: string;
  label: string;
  enabled: boolean;
  count: number;
}

export interface MemoryOverview {
  facts: MemoryFact[];
  categories: MemoryCategorySummary[];
  influencing_count: number;
  note: string;
}

export interface ComparisonCheck {
  key: string;
  passed: boolean;
  blocking: boolean;
  detail: string;
}

export interface ComparisonResponse {
  body_area: string;
  comparable: boolean;
  comparison: {
    comparable: boolean;
    checks: ComparisonCheck[];
    blocking_reasons: string[];
    days_apart: number | null;
    guidance: string[];
    disclaimer: string;
    baseline: { photo_id: string; media_id: string; taken_on: string };
    current: { photo_id: string; media_id: string; taken_on: string };
  } | null;
  photos_held: number;
  rejected?: { photo_id: string; taken_on: string; reasons: string[] }[];
  message: string;
  guidance?: string[];
}

export const getProgress = async (period: 'week' | 'month' = 'week'): Promise<ProgressOverview> =>
  (await api.get<ProgressOverview>(`${V2}/progress`, { params: { period } })).data;

export const getMetricDefinitions = async (): Promise<{
  metrics: Metric[]; registry_version: string; no_overall_score: boolean; note: string;
}> => (await api.get(`${V2}/progress/metrics`)).data;

export const getMetric = async (key: string): Promise<{
  definition: Metric;
  current: Metric;
  history: { period: string; period_start: string; value: number | null; status: MetricStatus; formula_version: string }[];
  how_it_is_worked_out: string;
  what_it_is_not: string;
}> => (await api.get(`${V2}/progress/metrics/${encodeURIComponent(key)}`)).data;

export const recordSelfReport = async (
  rating: 1 | 2 | 3 | 4 | 5, note?: string
): Promise<{ rating: number; message: string }> =>
  (await api.post(`${V2}/progress/self-report`, { rating, note })).data;

export const addProgressPhoto = async (body: {
  media_id: string;
  body_area: 'face' | 'hair' | 'scalp' | 'skin' | 'full_body' | 'hands';
  lighting: string; angle: string; framing: string;
  taken_on?: string; time_of_day?: string; note?: string;
}): Promise<{ id: string; note: string }> =>
  (await api.post(`${V2}/progress/photos`, body)).data;

export const getComparisons = async (body_area = 'face'): Promise<ComparisonResponse> =>
  (await api.get<ComparisonResponse>(`${V2}/progress/comparisons`, { params: { body_area } })).data;

export const getGoals = async (): Promise<{ goals: Goal[] }> =>
  (await api.get(`${V2}/goals`)).data;

export const createGoal = async (body: {
  kind: GoalKind; title: string; metric_key?: string;
  target_value?: number; starts_on?: string; target_date?: string; note?: string;
}): Promise<Goal> => (await api.post<Goal>(`${V2}/goals`, body)).data;

export const patchGoal = async (id: string, body: {
  title?: string; target_value?: number; target_date?: string;
  status?: 'active' | 'paused' | 'achieved' | 'abandoned';
  progress_value?: number; progress_note?: string;
}): Promise<Goal> => (await api.patch<Goal>(`${V2}/goals/${id}`, body)).data;

export const getMemory = async (category?: string): Promise<MemoryOverview> =>
  (await api.get<MemoryOverview>(`${V2}/memory`, { params: category ? { category } : undefined })).data;

export const patchMemory = async (id: string, body: {
  fact?: string; verification_state?: MemoryVerification;
}): Promise<MemoryFact> => (await api.patch<MemoryFact>(`${V2}/memory/${id}`, body)).data;

export const deleteMemory = async (id: string): Promise<{ id: string; message: string }> =>
  (await api.delete(`${V2}/memory/${id}`)).data;

export const exportMemory = async (): Promise<{
  exported_at: string; facts: MemoryFact[]; total: number;
  deleted_count: number; disabled_categories: string[]; note: string;
}> => (await api.get(`${V2}/memory/export`)).data;

export const setMemoryCategory = async (
  category: string, enabled: boolean
): Promise<{ category: string; label: string; enabled: boolean; message: string }> =>
  (await api.patch(`${V2}/memory/categories/${category}`, { category, enabled })).data;

export const sendMemoryFeedback = async (body: {
  subject_type: 'look' | 'product' | 'routine_step' | 'purchase' | 'colour' | 'occasion';
  signal: 'liked' | 'rejected' | 'wore_it' | 'not_for_me' | 'returned' | 'complimented';
  subject_id?: string; subject_label?: string; reason?: string;
}): Promise<{ learned: MemoryFact | null; message: string }> =>
  (await api.post(`${V2}/memory/feedback`, body)).data;

export const getMilestones = async (): Promise<{
  earned: EarnedMilestone[];
  streaks: { behaviour: string; current_length: number; longest_length: number; reset_count: number; note: string }[];
  all_rules: { rule_id: string; label: string; description: string; behaviour: string; threshold: number }[];
  rewarded_behaviours: string[];
  never_rewarded: string[];
  note: string;
}> => (await api.get(`${V2}/milestones`)).data;

export const acknowledgeMilestone = async (id: string): Promise<{ acknowledged: boolean }> =>
  (await api.post(`${V2}/milestones/${id}/acknowledge`, {})).data;

// --- Phase 8: support ----------

export const openSupportCase = async (body: {
  category: 'account' | 'bug' | 'content' | 'other';
  subject: string;
  message: string;
}): Promise<{ id: string; severity: string; status: string; message: string }> =>
  (await api.post(`${V2}/support`, body)).data;

export const requestStylistReview = async (body: {
  subject_type: 'look' | 'outfit' | 'wardrobe' | 'routine' | 'occasion';
  subject_id?: string;
  question: string;
}): Promise<{ id: string; status: string; message: string }> =>
  (await api.post(`${V2}/stylist-review`, body)).data;

// --- Skin and Hair maintenance timing (VC-06) --------------------------------

export interface MaintenanceKindStatus {
  kind: string;
  label: string;
  domain: 'hair_care' | 'skin_care';
  description: string;
  status: 'due' | 'coming_up' | 'not_due' | 'needs_anchor' | 'not_tracked';
  reason: string;
  tracked: boolean;
  reminders_enabled: boolean;
  interval_days: number;
  interval_is_custom: boolean;
  last_done_on: string | null;
  next_due_on: string | null;
  days_until_due: number | null;
}

export interface MaintenanceOverview {
  version: string;
  catalogue_version: string;
  plan_date: string;
  kinds: MaintenanceKindStatus[];
  note: string;
  interval_bounds: { min_days: number; max_days: number };
  due: string[];
  coming_up: string[];
  needs_anchor: string[];
}

export const getMaintenance = async (): Promise<MaintenanceOverview> =>
  (await api.get<MaintenanceOverview>(`${V2}/maintenance`)).data;

export const updateMaintenance = async (
  kind: string,
  body: { tracked?: boolean; interval_days?: number | null; reminders_enabled?: boolean },
): Promise<MaintenanceOverview> =>
  (await api.put<MaintenanceOverview>(`${V2}/maintenance/${kind}`, body)).data;

export const recordMaintenanceDone = async (
  kind: string,
  body: { done_on?: string; note?: string } = {},
): Promise<MaintenanceOverview> =>
  (await api.post<MaintenanceOverview>(`${V2}/maintenance/${kind}/done`, body)).data;

export const forgetMaintenanceDone = async (
  kind: string,
  doneOn: string,
): Promise<MaintenanceOverview & { removed: boolean }> =>
  (await api.delete<MaintenanceOverview & { removed: boolean }>(`${V2}/maintenance/${kind}/done/${doneOn}`)).data;
