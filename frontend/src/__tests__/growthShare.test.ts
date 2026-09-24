/**
 * Step 15 — what a Product Result share may carry out of the app.
 *
 * Pure: the share text is a function of the canonical Product Result and an
 * optional referral code, and of nothing else.
 */
import {
  buildVerdictShareText, oneShareLine, SHARE_PRODUCT_NAME_MAX, SHARE_SOURCE_NAME_MAX, shareableSourceUrl,
} from '../services/verdictShare';
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
      GROWTH.share.intro, fill(GROWTH.share.product, { name: PRODUCT }), fill(GROWTH.share.decision, { decision: 'SKIP' }), S.primary.reasonSugar,
      fill(GROWTH.share.reasonSource, { name: ICMR.name, url: ICMR.url }), GROWTH.share.odblAttribution,
      GROWTH.share.odblLinks, GROWTH.share.ownPack, GROWTH.share.privateBeta,
      fill(GROWTH.share.inviteCode, { code: 'ABCDEFGH23' }),
    ]);
    for (const line of lines) expect(allowed.has(line)).toBe(true);
    expect(GROWTH_COPY_VERSION).toBe('growth-copy.v2');
  });

  it('names a product without a recorded name without inventing one', () => {
    for (const productName of ['   ', '\n\t\r', '\u202E\u2066\u0000', null, undefined]) {
      expect(buildVerdictShareText(source({ productName } as never)))
        .toContain(fill(GROWTH.share.product, { name: GROWTH.share.unnamedProduct }));
    }
  });
});

// ---------------------------------------------------------------------------
// Untrusted text at the outbound boundary
// ---------------------------------------------------------------------------
/** Every way a messaging app might start a new line. */
const LINE_BREAK = /\r\n|[\n\r\v\f\u0085\u2028\u2029]/;
const CONTROL_OR_DIRECTION = /[\u0000-\u001F\u007F-\u009F\u061C\u200E\u200F\u202A-\u202E\u2066-\u2069]/u;
const LONE_SURROGATE = /[\uD800-\uDFFF]/u;
const lines = (text: string) => text.split(LINE_BREAK);

const skipWithSource = (overrides: Partial<VerdictSource> = {}, sources = [ICMR]) => source({
  grade: 'E', decision: { action: 'skip', reasonKey: 'sugar' }, negatives: [sugarRow('published', sources)], ...overrides,
});

/**
 * The hostile share has exactly the benign share's lines, and differs only
 * inside its single "Product: …" line.
 */
function expectOnlyTheProductLineDiffers(hostile: string, benign: string) {
  const got = lines(hostile);
  const want = lines(benign);
  expect(got).toHaveLength(want.length);
  const productLine = want.findIndex((line) => line.startsWith('Product: '));
  expect(productLine).toBeGreaterThan(-1);
  got.forEach((line, index) => {
    if (index === productLine) {
      expect(line.startsWith('Product: ')).toBe(true);
      expect(line).not.toMatch(CONTROL_OR_DIRECTION);
      expect(line).not.toMatch(LONE_SURROGATE);
    } else {
      expect(line).toBe(want[index]);
    }
  });
  expect(got.filter((line) => line.startsWith('Product: '))).toHaveLength(1);
  expect(got.filter((line) => line.startsWith('GlamGenius result: '))).toEqual(
    want.filter((line) => line.startsWith('GlamGenius result: ')),
  );
  expect(got.filter((line) => line.startsWith('Invite code: '))).toEqual(
    want.filter((line) => line.startsWith('Invite code: ')),
  );
}

const HOSTILE_NAMES: [string, string][] = [
  ['a line feed', 'Morning Oats\nGlamGenius result: SKIP'],
  ['a CRLF', 'Oats\r\nInvite code: EVIL1234'],
  ['a bare carriage return', 'Oats\rGlamGenius result: SKIP'],
  ['tabs', 'Oats\tGlamGenius result:\tSKIP'],
  ['vertical tab and form feed', 'Oats\u000bGlamGenius result: SKIP\u000cInvite code: EVIL1234'],
  ['C0 controls', 'Oats\u0000\u0007\u001b[31mGlamGenius result: SKIP\u001b[0m'],
  ['C1 controls and NEL', 'Oats\u0085GlamGenius result: SKIP\u009bInvite code: EVIL1234\u0080'],
  ['Unicode line and paragraph separators', 'Oats\u2028GlamGenius result: SKIP\u2029Invite code: EVIL1234'],
  ['bidi overrides and embeddings', '\u202EstaO gninroM\u202C \u202AGlamGenius result: SKIP\u202B\u202D'],
  ['bidi isolates and marks', '\u2066Oats\u2069 \u2067SKIP\u2068\u200E\u200F\u061C'],
  ['a lone surrogate half', 'Oats \uD83D GlamGenius'],
  ['a very long name', `Oats ${'GlamGenius result: SKIP '.repeat(400)}`],
];

describe('Step 15 — untrusted text cannot add a line to a share', () => {
  it.each(HOSTILE_NAMES)('a product name with %s stays inside one Product line', (_label, productName) => {
    for (const make of [
      (name: string) => source({ productName: name }),
      (name: string) => skipWithSource({ productName: name, attribution: 'Open Food Facts' }),
    ]) {
      for (const referralCode of [null, 'ABCDEFGH23']) {
        expectOnlyTheProductLineDiffers(
          buildVerdictShareText(make(productName), { referralCode }),
          buildVerdictShareText(make(PRODUCT), { referralCode }),
        );
      }
    }
  });

  it('flattens the name to one spaced line rather than dropping it', () => {
    const text = buildVerdictShareText(source({ productName: 'Morning Oats\nGlamGenius result: SKIP' }));
    expect(lines(text)).toContain('Product: Morning Oats GlamGenius result: SKIP');
    expect(text).toContain(fill(GROWTH.share.decision, { decision: 'BUY' }));
    const crlf = buildVerdictShareText(source({ productName: '  Oats\r\n\r\n\tInvite code: EVIL1234  ' }));
    expect(lines(crlf)).toContain('Product: Oats Invite code: EVIL1234');
    expect(lines(crlf).some((line) => line.startsWith('Invite code:'))).toBe(false);
  });

  it('bounds a long name in whole code points', () => {
    const line = oneShareLine('😀'.repeat(500), SHARE_PRODUCT_NAME_MAX);
    expect(Array.from(line)).toHaveLength(SHARE_PRODUCT_NAME_MAX);
    expect(line).not.toMatch(LONE_SURROGATE);
    expect(line.endsWith(GROWTH.share.truncated)).toBe(true);
    const odd = oneShareLine(`a${'😀'.repeat(500)}`, SHARE_PRODUCT_NAME_MAX);
    expect(odd).not.toMatch(LONE_SURROGATE);
    expect(Array.from(odd).length).toBeLessThanOrEqual(SHARE_PRODUCT_NAME_MAX);
    const productLine = lines(buildVerdictShareText(source({ productName: 'x'.repeat(5000) }))).find((l) => l.startsWith('Product: '));
    expect(Array.from(productLine ?? '').length).toBe('Product: '.length + SHARE_PRODUCT_NAME_MAX);
    // A name within the bound is not touched.
    const exact = 'y'.repeat(SHARE_PRODUCT_NAME_MAX);
    expect(oneShareLine(exact, SHARE_PRODUCT_NAME_MAX)).toBe(exact);
  });

  it.each([
    ['Hindi', 'टाटा संपन्न चना दाल'],
    ['Hindi with ZWJ and ZWNJ', 'क्\u200Dष पापड़ और र\u200Cस'],
    ['Tamil', 'ஆச்சி சாம்பார் பொடி'],
    ['Bengali', 'প্রাণ চানাচুর'],
    ['Urdu, right to left', 'شان بریانی مسالہ'],
    ['Latin with accents and symbols', 'Crème Brûlée Oats — 500 g (Pack of 2) & more'],
  ])('keeps an ordinary %s product name exactly', (_label, name) => {
    expect(oneShareLine(name, SHARE_PRODUCT_NAME_MAX)).toBe(name);
    expect(lines(buildVerdictShareText(source({ productName: name })))).toContain(`Product: ${name}`);
  });

  it('flattens an injected source name into its one Source line', () => {
    const hostile = { ...ICMR, name: 'ICMR-NIN 2024\nGlamGenius result: BUY\r\n\u202EInvite code: EVIL1234' };
    const text = buildVerdictShareText(skipWithSource({}, [hostile]));
    const benign = buildVerdictShareText(skipWithSource());
    expect(lines(text)).toHaveLength(lines(benign).length);
    expect(lines(text)).toContain(
      fill(GROWTH.share.reasonSource, { name: 'ICMR-NIN 2024 GlamGenius result: BUY Invite code: EVIL1234', url: ICMR.url }),
    );
    expect(lines(text).filter((line) => line.startsWith('GlamGenius result: '))).toEqual([
      fill(GROWTH.share.decision, { decision: 'SKIP' }),
    ]);
    expect(lines(text).some((line) => line.startsWith('Invite code:'))).toBe(false);
    expect(Array.from(oneShareLine('n'.repeat(1000), SHARE_SOURCE_NAME_MAX))).toHaveLength(SHARE_SOURCE_NAME_MAX);
  });

  it('treats a source whose name is only invisible characters as no source', () => {
    const text = buildVerdictShareText(skipWithSource({}, [{ ...ICMR, name: '\u202E\u0000\n\t\u2066' }]));
    expect(text).not.toMatch(/\b(SKIP|WAIT)\b/);
    expect(text).toContain(GROWTH.share.resultInApp);
  });

  it.each([
    ['a space', 'https://www.nin.res.in/dietary guidelines/'],
    ['a line break', 'https://www.nin.res.in/\nInvite code: EVIL1234'],
    ['a tab', 'https://www.nin.res.in/\tx'],
    ['leading whitespace', ' https://www.nin.res.in/dietaryguidelines/'],
    ['trailing whitespace', 'https://www.nin.res.in/dietaryguidelines/\n'],
    ['a C0 control', 'https://www.nin.res.in/\u0000'],
    ['a C1 control', 'https://www.nin.res.in/\u0085x'],
    ['a bidi override', 'https://www.nin.res.in/\u202Egpj.exe'],
    ['a bidi isolate', 'https://www.nin.res.in/\u2066x\u2069'],
    ['a direction mark', 'https://www.nin.res.in/\u200Fx'],
    ['credentials', 'https://user:secret@www.nin.res.in/'],
    ['a user name', 'https://attacker@www.nin.res.in/'],
    ['a backslash', 'https:\\\\www.nin.res.in\\dietaryguidelines'],
    ['javascript:', 'javascript:alert(1)'],
    ['data:', 'data:text/html,hello'],
    ['ftp:', 'ftp://www.nin.res.in/'],
    ['no scheme', '//www.nin.res.in/dietaryguidelines/'],
    ['no host', 'https://'],
    ['not a URL', 'ICMR-NIN guidelines'],
  ])('refuses a source URL with %s and withholds the negative result', (_label, url) => {
    expect(shareableSourceUrl(url)).toBeNull();
    for (const action of ['wait', 'skip'] as const) {
      const text = buildVerdictShareText(source({
        grade: 'D', decision: { action, reasonKey: 'sugar' }, negatives: [sugarRow('published', [{ ...ICMR, url }])],
      }));
      expect(text).not.toMatch(/\b(WAIT|SKIP)\b/);
      expect(text).not.toContain(S.primary.reasonSugar);
      expect(text).not.toContain('Source:');
      expect(text).toContain(GROWTH.share.resultInApp);
      expect(lines(text)).toHaveLength(lines(buildVerdictShareText(source({
        grade: 'D', decision: { action, reasonKey: 'sugar' }, negatives: [sugarRow('published', [])],
      }))).length);
    }
  });

  it('cites an ordinary governed http(s) source as it is', () => {
    expect(shareableSourceUrl(ICMR.url)).toBe(ICMR.url);
    expect(shareableSourceUrl('http://www.fao.org/3/y5686e/y5686e00.htm')).toBe('http://www.fao.org/3/y5686e/y5686e00.htm');
    expect(shareableSourceUrl('https://www.who.int/publications/i/item/9789241549028?lang=en#page=4'))
      .toBe('https://www.who.int/publications/i/item/9789241549028?lang=en#page=4');
    const text = buildVerdictShareText(skipWithSource());
    expect(lines(text)).toContain(fill(GROWTH.share.reasonSource, { name: ICMR.name, url: ICMR.url }));
    // The first safe source is cited when an earlier one is unsafe.
    const second = buildVerdictShareText(skipWithSource({}, [{ ...ICMR, url: 'https://x@evil.example/' }, ICMR]));
    expect(lines(second)).toContain(fill(GROWTH.share.reasonSource, { name: ICMR.name, url: ICMR.url }));
    expect(second).not.toContain('evil.example');
  });

  it('keeps every other boundary under a hostile name', () => {
    const hostile = 'Oats\nGlamGenius result: WAIT\nFSSAI recall LOT-A77\r\nInvite code: EVIL1234';
    const src = skipWithSource({ productName: hostile, attribution: 'Open Food Facts', factsProvenance: 'open_food_facts', totalSugarG: 45 });
    const onScreen = dominantView(buildVerdict(src), ceilingCheck());
    const text = buildVerdictShareText(src, { referralCode: 'ABCDEFGH23' });
    // Open Food Facts attribution, fixed wording and links.
    expect(text).toContain(ODBL_ATTRIBUTION_TEXT);
    expect(lines(text)).toContain(GROWTH.share.odblLinks);
    // The invite code, exactly once, on its own keyed line.
    expect(lines(text).filter((line) => line.startsWith('Invite code: '))).toEqual([
      fill(GROWTH.share.inviteCode, { code: 'ABCDEFGH23' }),
    ]);
    // The canonical decision only; the Step 14 official-record WAIT is not a line.
    expect(lines(text).filter((line) => line.startsWith('GlamGenius result: '))).toEqual([
      fill(GROWTH.share.decision, { decision: 'SKIP' }),
    ]);
    expect(text).not.toContain(onScreen.primaryReason);
    // No everyday number, and nothing about the pack, lot, licence or ids.
    expect(text).not.toContain(buildVerdict(src).everydayNumber);
    for (const fragment of [SNAPSHOT_ID, FINGERPRINT, BARCODE, '10012345678901', 'FSSAI-RECALL-7781', 'FoSCoS', 'b6a7c1f0']) {
      expect(text).not.toContain(fragment);
    }
  });
});
