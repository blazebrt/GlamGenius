/**
 * Step 15 — what a Product Result share may carry out of the app.
 *
 * Pure: the share text is a function of the canonical Product Result and an
 * optional referral code, and of nothing else.
 */
import { buildVerdictShareText } from '../services/verdictShare';
import { buildVerdict, type VerdictSource } from '../services/verdictModel';
import { dominantView } from '../services/purchaseOsModel';
import { fill, GROWTH, GROWTH_COPY_VERSION } from '../strings/growth';
import { S } from '../strings/verdict';
import { ODBL_ATTRIBUTION_TEXT } from '../components/common/OpenFoodFactsAttribution';
import type { PurchaseOsCheck } from '../services/apiV2';

const PRODUCT = 'Morning Oats';
const BARCODE = '8901058000191';
const FINGERPRINT = 'f'.repeat(64);
const SNAPSHOT_ID = '5d9a8f0e-1111-4c1e-9f42-0a1b2c3d4e5f';
const ICMR = { name: 'ICMR-NIN 2024', url: 'https://www.nin.res.in/dietaryguidelines/', publisher: 'ICMR-NIN', version: '2024' };

function sugarRow(status = 'published', sources = [ICMR]) {
  return {
    key: 'sugar', label: 'label_sugar', status: 'high', band: 'red' as const,
    quantity: { value: 45, unit: 'g', basis: 'per_100_g' as const },
    explanation: 'lower_label_fact', rule: 'grade.sugar', sources,
    evidence: { status },
  };
}

function source(overrides: Partial<VerdictSource> = {}): VerdictSource {
  return {
    outcome: 'graded', grade: 'A', productName: PRODUCT, barcode: BARCODE, brand: 'Acme Mills',
    totalSugarG: 1, saltG: 0.01, totalFatG: 2, proteinG: 10, packSizeG: 500,
    decision: { action: 'buy', reasonKey: 'label_facts' },
    negatives: [], positives: [], components: [], ingredients: [],
    attribution: null, physicalPackContext: true, factsProvenance: 'confirmed_label_snapshot',
    confidence: { level: 'confirmed', text: 'Checked against the pack' },
    labelVersion: {
      id: SNAPSHOT_ID, versionNumber: 3, contentFingerprint: FINGERPRINT,
      observedAt: '2026-09-01T00:00:00Z', changedFields: ['batch_number'], completeness: 'complete_for_grading',
    },
    officialRecords: {
      authority: 'FSSAI', record_type: 'food_recall', source_url: 'https://foscos.fssai.gov.in/food-recall',
      last_successful_check_at: '2026-09-01T00:00:00Z',
      records: [{ recall_id: 'FSSAI-RECALL-7781', source_url: 'https://foscos.fssai.gov.in/food-recall', match_state: 'matched', batch_number: 'LOT-A77', fssai_licence: '10012345678901' }],
    },
    communityObservations: {
      policy_version: 'v1', public_enabled: true, active_window_days: 30, brand_reply_url: null,
      signals: [{ observation_code: 'seal_broken', scope: 'batch', batch_number: 'LOT-A77', independent_reporters: 3, first_reported_at: null, last_reported_at: null, analysis_score_eligible: false, official_finding: false }],
    },
    comparableAlternative: null,
    mrpComparison: null,
    ...overrides,
  };
}

function ceilingCheck(): PurchaseOsCheck {
  return {
    contract_version: 'step-14-v1',
    context: { kind: 'scan', strategy: 'scan_product', category: 'food' },
    subject: { household_subject_id: 'b6a7c1f0-2222-4a2d-8e11-1234567890ab', is_account_holder: false },
    identity: { state: 'exact', barcode: BARCODE, label_version: 3, content_fingerprint: FINGERPRINT, physical_pack_context: true, reference_view: false },
    decision: { state: 'decided', verdict: 'wait', primary_reason_code: 'official_record_matches_pack', primary_reason_authority: 'official_records', decision_fingerprint: 'b'.repeat(64) },
    authorities: [
      { authority: 'product_result', status: 'applied', action: 'buy', reason_key: 'label_facts' },
      { authority: 'official_records', status: 'applied', matched_record_count: 1, source: { name: 'FSSAI / FoSCoS', url: 'https://foscos.fssai.gov.in/food-recall' } },
    ],
    memory: { kind: 'scan_decision', fidelity: 'user_outcome_only', state: 'prior_exact_decision', current_decision: { decision: 'SKIP' }, earlier_version_decision: null, history_complete: false },
    ownership: { authority: 'inventory_product_link', effect: 'context_only', state: 'owned_exact_version' },
    alternative: { status: 'not_enough_information', reason_key: null, candidate: null },
    value: { state: 'missing', missing: ['current_price'] },
    boundary: null,
    missing_information: [],
  } as unknown as PurchaseOsCheck;
}

const PERSONAL_OR_PACK = [
  SNAPSHOT_ID, FINGERPRINT, BARCODE, 'LOT-A77', '10012345678901', 'FSSAI-RECALL-7781', 'recall', 'Recall',
  'FoSCoS', 'FSSAI', 'official', 'Official', 'batch', 'licence', 'shelf', 'already own', 'you own', 'household', 'family',
  'watch', 'Watch', 'decision history', 'You chose', 'seal_broken', 'b6a7c1f0', 'version 3', 'Acme Mills',
  'Checked against the pack',
];

describe('Step 15 — the external share boundary', () => {
  it('uses the canonical Product Result even when the Step 14 ceiling changes the screen', () => {
    const src = source();
    const onScreen = dominantView(buildVerdict(src), ceilingCheck());
    expect(onScreen.verdict).toBe('WAIT');
    const text = buildVerdictShareText(src);
    expect(text).toContain(fill(GROWTH.share.decision, { decision: 'BUY' }));
    expect(text).not.toContain('WAIT');
    expect(text).not.toContain(onScreen.primaryReason);
  });

  it('takes no view: nothing on screen can be passed in', () => {
    // The second parameter is options only; the function has no view argument.
    expect(buildVerdictShareText.length).toBeLessThanOrEqual(2);
    const text = buildVerdictShareText(source(), { referralCode: null, view: { verdict: 'WAIT' } } as never);
    expect(text).not.toContain('WAIT');
  });

  it('carries nothing about the pack in hand, the person or their household', () => {
    const text = buildVerdictShareText(source(), { referralCode: 'ABCDEFGH23' });
    for (const fragment of PERSONAL_OR_PACK) expect(text).not.toContain(fragment);
    expect(text).toContain(PRODUCT);
    expect(text).toContain(GROWTH.share.ownPack);
  });

  it('never exports the everyday number', () => {
    for (const src of [source(), source({ decision: { action: 'skip', reasonKey: 'sugar' }, negatives: [sugarRow()], totalSugarG: 45 })]) {
      const text = buildVerdictShareText(src);
      expect(text).not.toMatch(/per 100 g|total sugar|\bg salt|protein/i);
      expect(text).not.toContain(buildVerdict(src).everydayNumber);
    }
    const notGraded = source({ outcome: 'not_graded', grade: null, decision: { action: null, reasonKey: 'not_graded' }, quantityGuidance: 'Use about one teaspoon a day.' });
    const text = buildVerdictShareText(notGraded);
    expect(text).toContain(GROWTH.share.notGraded);
    expect(text).not.toContain('teaspoon');
  });

  it('states a negative decision only with its published, openable source', () => {
    const skip = source({ grade: 'E', decision: { action: 'skip', reasonKey: 'sugar' }, negatives: [sugarRow()] });
    const text = buildVerdictShareText(skip);
    expect(text).toContain(fill(GROWTH.share.decision, { decision: 'SKIP' }));
    expect(text).toContain(S.primary.reasonSugar);
    expect(text).toContain(fill(GROWTH.share.reasonSource, { name: ICMR.name, url: ICMR.url }));
  });

  it.each([
    ['an unpublished rule', sugarRow('candidate')],
    ['a source nobody can open', sugarRow('published', [{ ...ICMR, url: null }] as never)],
    ['a non-web locator', sugarRow('published', [{ ...ICMR, url: 'internal:rule/sugar' }])],
    ['no source at all', sugarRow('published', [])],
  ])('withholds a negative decision resting on %s', (_label, row) => {
    for (const action of ['wait', 'skip'] as const) {
      const text = buildVerdictShareText(source({ grade: 'D', decision: { action, reasonKey: 'sugar' }, negatives: [row] }));
      expect(text).not.toMatch(/\b(WAIT|SKIP)\b/);
      expect(text).not.toContain(S.primary.reasonSugar);
      expect(text).toContain(GROWTH.share.resultInApp);
    }
  });

  it('withholds a reason whose row is not the one the decision names', () => {
    const text = buildVerdictShareText(source({ decision: { action: 'skip', reasonKey: 'salt' }, negatives: [sugarRow()] }));
    expect(text).not.toContain('SKIP');
    expect(text).toContain(GROWTH.share.resultInApp);
  });

  it('states BUY plainly, with or without a sourced reason', () => {
    expect(buildVerdictShareText(source())).toContain(fill(GROWTH.share.decision, { decision: 'BUY' }));
    const withReason = buildVerdictShareText(source({ decision: { action: 'buy', reasonKey: 'sugar' }, negatives: [sugarRow()] }));
    expect(withReason).toContain(ICMR.url);
  });

  it('says "not enough information" as what it is', () => {
    const text = buildVerdictShareText(source({ outcome: 'not_enough_information', grade: null, decision: { action: null, reasonKey: 'not_enough_information' } }));
    expect(text).toContain(GROWTH.share.notEnoughInformation);
    expect(text).not.toMatch(/\b(BUY|WAIT|SKIP)\b/);
  });

  it('carries the Open Food Facts attribution whenever that data is in play', () => {
    const withOff = buildVerdictShareText(source({ attribution: 'Open Food Facts', factsProvenance: 'open_food_facts' }));
    expect(withOff).toContain(ODBL_ATTRIBUTION_TEXT);
    expect(withOff).toContain('https://world.openfoodfacts.org/');
    expect(withOff).toContain('https://opendatacommons.org/licenses/odbl/1-0/');
    expect(buildVerdictShareText(source())).not.toContain(ODBL_ATTRIBUTION_TEXT);
  });

  it('adds an invite code only when a well-formed one is given', () => {
    const withCode = buildVerdictShareText(source(), { referralCode: 'ABCDEFGH23' });
    expect(withCode).toContain(GROWTH.share.privateBeta);
    expect(withCode).toContain(fill(GROWTH.share.inviteCode, { code: 'ABCDEFGH23' }));
    for (const bad of [null, undefined, '', 'abc', 'ABCDEFGH23\nClick here', 'ABC DEF GHI', '<script>']) {
      const text = buildVerdictShareText(source(), { referralCode: bad as string | null | undefined });
      expect(text).toContain(GROWTH.share.privateBeta);
      expect(text).not.toContain('Invite code');
      expect(text).not.toContain('Click here');
    }
  });

  it('has no scarcity, urgency, reward or mockery', () => {
    const text = buildVerdictShareText(source(), { referralCode: 'ABCDEFGH23' });
    const all = JSON.stringify(GROWTH);
    for (const phrase of ['only', 'spots', 'left', 'exclusive', 'VIP', 'hurry', 'disappear', 'reward', 'earn', 'free gift', '!!', 'toxic', 'never buy', 'stop using', 'poison', 'dangerous']) {
      expect(text.toLowerCase()).not.toContain(phrase.toLowerCase());
      expect(all.toLowerCase()).not.toContain(phrase.toLowerCase());
    }
  });

  it('is built entirely from keyed strings and Product Result facts', () => {
    const skip = source({ grade: 'E', decision: { action: 'skip', reasonKey: 'sugar' }, negatives: [sugarRow()], attribution: 'Open Food Facts' });
    const lines = buildVerdictShareText(skip, { referralCode: 'ABCDEFGH23' }).split('\n').filter(Boolean);
    const allowed = new Set<string>([
      GROWTH.share.intro, PRODUCT, fill(GROWTH.share.decision, { decision: 'SKIP' }), S.primary.reasonSugar,
      fill(GROWTH.share.reasonSource, { name: ICMR.name, url: ICMR.url }), GROWTH.share.odblAttribution,
      GROWTH.share.odblLinks, GROWTH.share.ownPack, GROWTH.share.privateBeta,
      fill(GROWTH.share.inviteCode, { code: 'ABCDEFGH23' }),
    ]);
    for (const line of lines) expect(allowed.has(line)).toBe(true);
    expect(GROWTH_COPY_VERSION).toBe('growth-copy.v1');
  });

  it('names a product without a recorded name without inventing one', () => {
    expect(buildVerdictShareText(source({ productName: '   ' }))).toContain(GROWTH.share.unnamedProduct);
  });
});
