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

export const getEvaluation = async (id: string): Promise<PurchaseEvaluation> =>
  (await api.get<PurchaseEvaluation>(`${V2}/shopping/evaluations/${id}`)).data;

export const recordPurchaseDecision = async (
  id: string, decision: 'bought' | 'waiting' | 'skipped', note?: string
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

export const getWeek = async (week_start?: string): Promise<WeeklyPlan> =>
  (await api.get<WeeklyPlan>(`${V2}/planner/week`, { params: week_start ? { week_start } : undefined })).data;

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

export const getNotificationPreferences = async (): Promise<{
  preferences: NotificationPreferences;
  recent: { id: string; title: string; status: string; suppressed_reason: string | null }[];
}> => (await api.get(`${V2}/today/notifications`)).data;

export const patchNotificationPreferences = async (
  body: Partial<{ enabled: boolean; daily_cap: number; preferred_hour: number; modules: Record<string, boolean> }>
): Promise<{ preferences: NotificationPreferences }> =>
  (await api.patch(`${V2}/today/notifications`, body)).data;
