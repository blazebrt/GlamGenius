/**
 * Real-device qualification readiness for the Step 8L skin-care FOR YOU loop.
 *
 * This file does not re-prove Step 8L. `step8lForYouMobile` and
 * `step8lScanFlow` already hold the product rules. What this file holds is the
 * *qualification contract*: every state a tester must be able to name on a
 * physical phone has a stable, independently observable hook, and the states
 * that must never be confused for one another are provably distinct.
 *
 * The distinction that matters most on hardware is between four things that
 * all look like "no verdict":
 *
 *   technical failure   — the request did not complete
 *   governed non-decision — the server explained an absence
 *   hard handoff        — the safety authority took over
 *   malformed presentable — a claim arrived without its evidence
 *
 * A tester reading a screen cannot tell those apart from the prose alone, and
 * three of the four must never show a verdict while the fourth must never show
 * even its own sentence. So each one is named here, separately.
 *
 * Nothing in this file is a bypass. There is no fake decision, no injected
 * barcode, no operator-chosen verdict: the tests mock the same service
 * boundaries the existing Step 8L suites already mock, and the application
 * behaves exactly as it does in a real build.
 *
 * Passing this file means the app is *ready to be qualified*. It is not a
 * qualification pass — see docs/quality/SKIN_CARE_FOR_YOU_DEVICE_QUALIFICATION.md.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react-native';
import { Linking, TextInput } from 'react-native';
import fs from 'fs';
import path from 'path';

import { ForYouCard, FOR_YOU_COPY } from '../components/scan/ForYouCard';
import {
  ConfirmedSkinCareLabelCard,
  LabelTypeChoice,
  SafetyPreflight,
  SkinCareLabelReview,
  SAFETY_COPY,
} from '../components/scan/SkinCarePieces';
import type { ForYouResponse } from '../services/productScan';

const FRONTEND_ROOT = path.resolve(__dirname, '..', '..');

/** Sentinels. The reviewed sentences live on the server, never here. */
const SERVER_VERDICT = 'SERVER VERDICT WORD';
const SERVER_REASON = 'SERVER REVIEWED REASON SENTENCE';
const CANONICAL_URL = 'https://sentinel.example.org/exact/path?query=1#frag';

const CITATION = {
  source_key: 'sentinel.source.key',
  title: 'SENTINEL SOURCE TITLE',
  publisher: 'SENTINEL PUBLISHER',
  canonical_url: CANONICAL_URL,
  locator: 'SENTINEL LOCATOR',
  publication_date: null,
  version_or_revision: null,
  jurisdiction: null,
};

function response(
  overrides: Partial<ForYouResponse['result']> = {},
  extra: Partial<ForYouResponse> = {},
): ForYouResponse {
  return {
    barcode: '8901030000011',
    product_category: 'skin_care',
    copy_version: 'for-you-copy.v1',
    pack: {
      is_proven: true,
      current_pack_scan_id: 'scan-1',
      label_snapshot_id: 'snap-1',
      label_snapshot_source_scan_id: 'scan-1',
      label_snapshot_version: 1,
      content_fingerprint: 'f'.repeat(64),
    },
    result: {
      status: 'decision_presentable',
      reason: 'reviewed_explanation_available',
      action: 'buy',
      verdict_key: 'for_you.verdict.buy',
      verdict_text: SERVER_VERDICT,
      reason_key: 'for_you.sentinel.reason',
      reason_text: SERVER_REASON,
      citation: { ...CITATION },
      handoff: null,
      ...overrides,
    },
    release: { id: 'rel-1', version: 3, content_hash: 'c'.repeat(64) },
    ...extra,
  };
}

const noop = () => undefined;

function renderCard(res: ForYouResponse | null, props: Record<string, unknown> = {}) {
  return render(<ForYouCard response={res} onRetry={noop} onAddSkinDetails={noop} {...props} />);
}

/** Every word the client is forbidden to produce on its own. */
const VERDICT_WORDS = ['BUY', 'WAIT', 'SKIP', 'Buy', 'Wait', 'Skip', 'buy', 'wait', 'skip'];

function expectNoVerdictAnywhere() {
  expect(screen.queryByTestId('for-you-verdict')).toBeNull();
  for (const word of VERDICT_WORDS) expect(screen.queryByText(word)).toBeNull();
}

const READABLE_FACTS = {
  product_name: 'A CREAM',
  brand: 'A BRAND',
  product_type: 'Ointment',
  ingredients_text: 'Aqua, Glycerin, Sentinel Ingredient',
};

// ---------------------------------------------------------------------------
// 1-4 — the category is asked, and only the two supported kinds exist
// ---------------------------------------------------------------------------
describe('qualification: the category question', () => {
  beforeEach(() => {
    render(<LabelTypeChoice onChoose={noop} onCancel={noop} />);
  });

  it('exposes the packaged-food choice under a stable hook', () => {
    expect(screen.getByTestId('label-kind-packaged-food')).toBeTruthy();
  });

  it('exposes the skin-care choice under a stable hook', () => {
    expect(screen.getByTestId('label-kind-skin-care')).toBeTruthy();
  });

  it('offers no third category, now or by accident', () => {
    // Any option added to this screen has to be a label-kind hook to be
    // pressable in qualification, so counting them catches a new one even if
    // it is worded differently from the two below.
    expect(screen.getAllByTestId(/^label-kind-(?!choice$)/)).toHaveLength(2);
  });

  it.each(['Hair care', 'Hair Care', 'Cosmetics', 'Make-up', 'Makeup', 'Supplement'])(
    'never offers a fake %s category',
    (fake) => {
      expect(screen.queryByText(fake)).toBeNull();
      expect(screen.queryByLabelText(fake)).toBeNull();
    },
  );
});

describe('qualification: the category type itself admits only two kinds', () => {
  it('declares exactly packaged_food and skin_care', () => {
    const source = fs.readFileSync(
      path.join(FRONTEND_ROOT, 'src/components/scan/SkinCarePieces.tsx'),
      'utf8',
    );
    const declaration = source.match(/export type LabelKind = ([^;]+);/);
    expect(declaration).not.toBeNull();
    const kinds = (declaration![1].match(/'[a-z_]+'/g) ?? []).sort();
    expect(kinds).toEqual(["'packaged_food'", "'skin_care'"]);
  });
});

// ---------------------------------------------------------------------------
// 5-8 — the label review, readable and unreadable
// ---------------------------------------------------------------------------
describe('qualification: the skin-care label review', () => {
  function reviewWith(readable: boolean, message: string | null) {
    return render(
      <SkinCareLabelReview
        facts={READABLE_FACTS}
        ingredientsReadable={readable}
        message={message}
        onConfirm={noop}
        onRetake={noop}
      />,
    );
  }

  it('is independently observable as a stage', () => {
    reviewWith(true, null);
    expect(screen.getByTestId('skin-care-label-review')).toBeTruthy();
  });

  it('exposes what the camera read as its own region', () => {
    reviewWith(true, null);
    const ingredients = screen.getByTestId('skin-care-ingredients');
    expect(ingredients).toBeTruthy();
    expect(screen.getByText(READABLE_FACTS.ingredients_text)).toBeTruthy();
  });

  it('exposes confirmation for a readable ingredient list', () => {
    reviewWith(true, null);
    expect(screen.getByTestId('skin-care-confirm')).toBeTruthy();
  });

  it('withholds confirmation entirely when the ingredients are unreadable', () => {
    reviewWith(false, 'SERVER UNREADABLE MESSAGE');
    expect(screen.queryByTestId('skin-care-confirm')).toBeNull();
  });

  it('makes the unreadable state observable in its own right', () => {
    reviewWith(false, 'SERVER UNREADABLE MESSAGE');
    const warning = screen.getByTestId('skin-care-ingredients-unreadable');
    expect(warning).toBeTruthy();
    expect(warning.props.accessibilityRole).toBe('alert');
    expect(screen.getByText('SERVER UNREADABLE MESSAGE')).toBeTruthy();
  });

  it('keeps retake available in both states', () => {
    const readable = reviewWith(true, null);
    expect(screen.getByTestId('skin-care-retake')).toBeTruthy();
    readable.unmount();
    reviewWith(false, 'SERVER UNREADABLE MESSAGE');
    expect(screen.getByTestId('skin-care-retake')).toBeTruthy();
  });

  it('offers no way to type a different answer', () => {
    const { UNSAFE_queryAllByType } = reviewWith(true, null);
    expect(UNSAFE_queryAllByType(TextInput)).toHaveLength(0);
  });

  it('names the confirmed pack state for the tester', () => {
    render(
      <ConfirmedSkinCareLabelCard
        barcode="8901030000011"
        facts={READABLE_FACTS}
        confirmed={{
          barcode: '8901030000011',
          scan_id: 'scan-1',
          created: true,
          product_category: 'skin_care',
          label_snapshot: null,
          confidence: { level: 'unverified', text: 'Confirmed from your photo.' },
          confirmations: 0,
        } as never}
        onScanAgain={noop}
      />,
    );
    expect(screen.getByTestId('skin-care-confirmed-card')).toBeTruthy();
    expect(screen.getByTestId('skin-care-scan-again')).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// 9-14 — the safety preflight
// ---------------------------------------------------------------------------
const SAFETY_HOOKS = [
  ['safety-pregnancy', SAFETY_COPY.pregnancy],
  ['safety-breastfeeding', SAFETY_COPY.breastfeeding],
  ['safety-medication_involved', SAFETY_COPY.medication],
  ['safety-diagnosed_condition_involved', SAFETY_COPY.condition],
  ['safety-subject_is_child', SAFETY_COPY.child],
] as const;

describe('qualification: the safety preflight', () => {
  beforeEach(() => {
    render(<SafetyPreflight onSubmit={noop} />);
  });

  it('is independently observable as a stage', () => {
    expect(screen.getByTestId('safety-preflight')).toBeTruthy();
  });

  it.each(SAFETY_HOOKS)('exposes %s as an unchecked checkbox with a label', (hook, label) => {
    const node = screen.getByTestId(hook);
    expect(node.props.accessibilityRole).toBe('checkbox');
    expect(node.props.accessibilityState).toEqual({ checked: false });
    expect(node.props.accessibilityLabel).toBe(label);
  });

  it('exposes "none of these" as a checkbox too', () => {
    const none = screen.getByTestId('safety-none');
    expect(none.props.accessibilityRole).toBe('checkbox');
    expect(none.props.accessibilityState).toEqual({ checked: false });
  });

  it('reports a selected flag as checked', () => {
    fireEvent.press(screen.getByTestId('safety-pregnancy'));
    expect(screen.getByTestId('safety-pregnancy').props.accessibilityState).toEqual({ checked: true });
    expect(screen.getByTestId('safety-breastfeeding').props.accessibilityState).toEqual({ checked: false });
  });

  it('clears every other choice when "none of these" is taken', () => {
    fireEvent.press(screen.getByTestId('safety-pregnancy'));
    fireEvent.press(screen.getByTestId('safety-medication_involved'));
    fireEvent.press(screen.getByTestId('safety-none'));

    expect(screen.getByTestId('safety-none').props.accessibilityState).toEqual({ checked: true });
    for (const [hook] of SAFETY_HOOKS) {
      expect(screen.getByTestId(hook).props.accessibilityState).toEqual({ checked: false });
    }
  });

  it('clears "none of these" when a flag is taken afterwards', () => {
    fireEvent.press(screen.getByTestId('safety-none'));
    expect(screen.getByTestId('safety-none').props.accessibilityState).toEqual({ checked: true });

    fireEvent.press(screen.getByTestId('safety-pregnancy'));
    expect(screen.getByTestId('safety-none').props.accessibilityState).toEqual({ checked: false });
    expect(screen.getByTestId('safety-pregnancy').props.accessibilityState).toEqual({ checked: true });
  });

  it('withholds submission until a closed selection exists', () => {
    const submit = screen.getByTestId('safety-submit');
    expect(submit.props.accessibilityState).toEqual({ disabled: true });

    fireEvent.press(screen.getByTestId('safety-none'));
    expect(screen.getByTestId('safety-submit').props.accessibilityState).toEqual({ disabled: false });
  });

  it('has no field for a medicine name, a diagnosis or a symptom', () => {
    expect(screen.UNSAFE_queryAllByType(TextInput)).toHaveLength(0);
  });
});

describe('qualification: submitting the safety answer', () => {
  it('sends only the flags actually selected', () => {
    const submit = jest.fn();
    render(<SafetyPreflight onSubmit={submit} />);
    fireEvent.press(screen.getByTestId('safety-pregnancy'));
    fireEvent.press(screen.getByTestId('safety-submit'));
    expect(submit).toHaveBeenCalledWith({ pregnancy: true });
  });

  it('sends an empty context for "none of these"', () => {
    const submit = jest.fn();
    render(<SafetyPreflight onSubmit={submit} />);
    fireEvent.press(screen.getByTestId('safety-none'));
    fireEvent.press(screen.getByTestId('safety-submit'));
    expect(submit).toHaveBeenCalledWith({});
  });
});

// ---------------------------------------------------------------------------
// 15-17 — technical failure
// ---------------------------------------------------------------------------
describe('qualification: a technical FOR YOU failure', () => {
  it('is independently observable and carries no verdict', () => {
    renderCard(null, { failed: true });
    expect(screen.getByTestId('for-you-card')).toBeTruthy();
    expect(screen.getByTestId('for-you-technical-unavailable')).toBeTruthy();
    expectNoVerdictAnywhere();
  });

  it('exposes a retry hook that calls back', () => {
    const retry = jest.fn();
    renderCard(null, { failed: true, onRetry: retry });
    fireEvent.press(screen.getByTestId('for-you-retry'));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it('is distinguishable from every governed state', () => {
    renderCard(null, { failed: true });
    expect(screen.queryByTestId('for-you-nondecision')).toBeNull();
    expect(screen.queryByTestId('for-you-handoff')).toBeNull();
    expect(screen.queryByTestId('for-you-malformed-presentable')).toBeNull();
    expect(screen.queryByTestId('for-you-source')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 18-19 — loading
// ---------------------------------------------------------------------------
describe('qualification: the FOR YOU loading state', () => {
  it('is independently observable', () => {
    renderCard(response(), { loading: true });
    expect(screen.getByTestId('for-you-loading')).toBeTruthy();
  });

  it('carries no verdict, reason, source or failure state', () => {
    renderCard(response(), { loading: true });
    expectNoVerdictAnywhere();
    expect(screen.queryByTestId('for-you-reason')).toBeNull();
    expect(screen.queryByTestId('for-you-source')).toBeNull();
    expect(screen.queryByTestId('for-you-technical-unavailable')).toBeNull();
    expect(screen.queryByText(SERVER_VERDICT)).toBeNull();
    expect(screen.queryByText(SERVER_REASON)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 20-24 — governed non-decision, and the profile gap
// ---------------------------------------------------------------------------
function nonDecision(overrides: Partial<ForYouResponse['result']> = {}) {
  return response({
    status: 'not_enough_information',
    action: null,
    verdict_key: null,
    verdict_text: null,
    citation: null,
    reason_key: 'for_you.not_enough.semantic_mapping',
    reason_text: 'SERVER STRUCTURAL SENTENCE',
    ...overrides,
  });
}

describe('qualification: a governed non-decision', () => {
  it('is independently observable', () => {
    renderCard(nonDecision());
    expect(screen.getByTestId('for-you-nondecision')).toBeTruthy();
  });

  it('may render the server structural sentence, because it asserts nothing about the product', () => {
    renderCard(nonDecision());
    expect(screen.getByTestId('for-you-nondecision')).toHaveTextContent('SERVER STRUCTURAL SENTENCE');
  });

  it('carries no verdict and no source', () => {
    renderCard(nonDecision());
    expectNoVerdictAnywhere();
    expect(screen.queryByTestId('for-you-source')).toBeNull();
    expect(screen.queryByTestId('for-you-open-source')).toBeNull();
  });

  it('is distinguishable from a technical failure', () => {
    renderCard(nonDecision());
    expect(screen.queryByTestId('for-you-technical-unavailable')).toBeNull();
    expect(screen.queryByTestId('for-you-retry')).toBeNull();
  });

  it('exposes the skin-details CTA for the personal-context gap only', () => {
    const add = jest.fn();
    renderCard(nonDecision({ reason_key: 'for_you.not_enough.personal_context' }), {
      onAddSkinDetails: add,
    });
    fireEvent.press(screen.getByTestId('for-you-add-skin-details'));
    expect(add).toHaveBeenCalledTimes(1);
  });

  it('withholds that CTA for an unrelated gap', () => {
    renderCard(nonDecision({ reason_key: 'for_you.not_enough.semantic_mapping' }));
    expect(screen.queryByTestId('for-you-add-skin-details')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 25-28 — the hard handoff
// ---------------------------------------------------------------------------
const HANDOFF_MESSAGE = 'SERVER HANDOFF SENTENCE, WORD FOR WORD';

function handoff() {
  return response(
    {
      status: 'handoff_required',
      reason: 'professional_handoff_required',
      action: null,
      verdict_key: null,
      verdict_text: null,
      citation: null,
      reason_key: 'for_you.handoff.required',
      reason_text: HANDOFF_MESSAGE,
      handoff: { reason: 'pregnancy', message: HANDOFF_MESSAGE },
    },
    { release: null },
  );
}

describe('qualification: a hard handoff', () => {
  it('is independently observable', () => {
    renderCard(handoff());
    expect(screen.getByTestId('for-you-handoff')).toBeTruthy();
  });

  it('renders no verdict', () => {
    renderCard(handoff());
    expectNoVerdictAnywhere();
  });

  it('adds no local source and no local medical prose', () => {
    renderCard(handoff());
    expect(screen.queryByTestId('for-you-source')).toBeNull();
    expect(screen.queryByTestId('for-you-open-source')).toBeNull();
    expect(screen.queryByTestId('for-you-add-skin-details')).toBeNull();
  });

  it('passes the authority sentence through unchanged', () => {
    renderCard(handoff());
    expect(screen.getByTestId('for-you-handoff')).toHaveTextContent(HANDOFF_MESSAGE);
  });

  it('is distinguishable from a non-decision, a failure and a malformed decision', () => {
    renderCard(handoff());
    expect(screen.queryByTestId('for-you-nondecision')).toBeNull();
    expect(screen.queryByTestId('for-you-technical-unavailable')).toBeNull();
    expect(screen.queryByTestId('for-you-malformed-presentable')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 29-33 — a malformed presentable decision, four ways
// ---------------------------------------------------------------------------
const MALFORMED_CASES: [string, Partial<ForYouResponse['result']>][] = [
  ['the verdict is missing', { verdict_text: null }],
  ['the reason is missing', { reason_text: '' }],
  ['the citation is missing', { citation: null }],
  ['the canonical url is missing', { citation: { ...CITATION, canonical_url: '' } }],
];

describe.each(MALFORMED_CASES)('qualification: a presentable decision where %s', (_label, overrides) => {
  // Every case carries the same claim sentence and the same personal-context
  // reason key, so a leak shows up identically whichever field is absent.
  const malformed = () =>
    response({
      ...overrides,
      reason_key: 'for_you.not_enough.personal_context',
      reason_text:
        (overrides as { reason_text?: string }).reason_text === ''
          ? ''
          : SERVER_REASON,
    });

  it('exposes a structural unavailable state', () => {
    renderCard(malformed());
    expect(screen.getByTestId('for-you-malformed-presentable')).toBeTruthy();
    expect(screen.getByTestId('for-you-malformed-presentable')).toHaveTextContent(
      FOR_YOU_COPY.notAvailable,
    );
  });

  it('never renders the server product or personal reason', () => {
    renderCard(malformed());
    expect(screen.queryByTestId('for-you-reason')).toBeNull();
    expect(screen.queryByText(SERVER_REASON)).toBeNull();
  });

  it('never renders a verdict', () => {
    renderCard(malformed());
    expectNoVerdictAnywhere();
    expect(screen.queryByText(SERVER_VERDICT)).toBeNull();
  });

  it('never renders a source', () => {
    renderCard(malformed());
    expect(screen.queryByTestId('for-you-source')).toBeNull();
    expect(screen.queryByTestId('for-you-open-source')).toBeNull();
    expect(screen.queryByText(CITATION.title)).toBeNull();
    expect(screen.queryByText(CITATION.publisher)).toBeNull();
  });

  it('never offers the profile-gap CTA, even though the reason key names that gap', () => {
    renderCard(malformed());
    expect(screen.queryByTestId('for-you-add-skin-details')).toBeNull();
  });

  it('is not presented as a governed non-decision', () => {
    renderCard(malformed());
    expect(screen.queryByTestId('for-you-nondecision')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 34-40 — a valid presentable decision
// ---------------------------------------------------------------------------
describe('qualification: a valid presentable decision', () => {
  it('exposes the verdict under its own hook, exactly as the server wrote it', () => {
    renderCard(response());
    expect(screen.getByTestId('for-you-verdict')).toHaveTextContent(SERVER_VERDICT);
  });

  it('exposes the reason under its own hook, exactly as the server wrote it', () => {
    renderCard(response());
    expect(screen.getByTestId('for-you-reason')).toHaveTextContent(SERVER_REASON);
  });

  it('exposes the source block under its own hook', () => {
    renderCard(response());
    const source = screen.getByTestId('for-you-source');
    expect(source).toBeTruthy();
    expect(screen.getByText(CITATION.title)).toBeTruthy();
    expect(screen.getByText(CITATION.publisher)).toBeTruthy();
  });

  it('exposes the source-open action as a link', () => {
    renderCard(response());
    const open = screen.getByTestId('for-you-open-source');
    expect(open.props.accessibilityRole).toBe('link');
  });

  it('opens exactly the canonical URL the server supplied', () => {
    const openURL = jest.spyOn(Linking, 'openURL').mockResolvedValue(true as never);
    renderCard(response());
    fireEvent.press(screen.getByTestId('for-you-open-source'));
    expect(openURL).toHaveBeenCalledTimes(1);
    expect(openURL).toHaveBeenCalledWith(CANONICAL_URL);
    openURL.mockRestore();
  });

  it('does not transform the action field into the verdict word', () => {
    // action stays "buy" while the server's word is something else entirely.
    renderCard(response({ verdict_text: 'ZZZZZ' }));
    expect(screen.getByTestId('for-you-verdict')).toHaveTextContent('ZZZZZ');
    for (const word of VERDICT_WORDS) expect(screen.queryByText(word)).toBeNull();
  });

  it('does not transform the reason key into prose', () => {
    renderCard(
      response({ reason_key: 'for_you.not_enough.personal_context', reason_text: SERVER_REASON }),
    );
    expect(screen.getByTestId('for-you-reason')).toHaveTextContent(SERVER_REASON);
    // The key named the profile gap; the decision is presentable, so no CTA.
    expect(screen.queryByTestId('for-you-add-skin-details')).toBeNull();
  });

  it('shows no release, snapshot or evidence identifier to the customer', () => {
    const { toJSON } = renderCard(response());
    const rendered = JSON.stringify(toJSON());
    for (const secret of ['rel-1', 'c'.repeat(64), 'f'.repeat(64), 'snap-1', CITATION.source_key]) {
      expect(rendered).not.toContain(secret);
    }
  });
});

// ---------------------------------------------------------------------------
// The neutral treatment contract
// ---------------------------------------------------------------------------
/** Strip comments: prose that describes a banned construct is not that construct. */
function code(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .split('\n')
    .map((line) => line.replace(/(^|[^:])\/\/.*$/, '$1'))
    .join('\n');
}

describe('qualification: the client never interprets the action', () => {
  const card = code(
    fs.readFileSync(path.join(FRONTEND_ROOT, 'src/components/scan/ForYouCard.tsx'), 'utf8'),
  );

  it('never reads the action field at all', () => {
    expect(card).not.toMatch(/\.action\b/);
  });

  it('carries no success, warning or danger colour', () => {
    for (const tone of ['success', 'warning', 'danger']) {
      expect(card).not.toContain(`COLORS.${tone}`);
    }
  });

  it('renders one verdict style, not a per-action map', () => {
    // A single `verdict` style entry, applied unconditionally.
    expect(card.match(/style=\{styles\.verdict\}/g) ?? []).toHaveLength(1);
    expect(card).not.toMatch(/verdict[A-Z][a-z]+\s*:/);
  });

  it('gives every action the same treatment at runtime', () => {
    const styleFor = (action: string) => {
      const view = renderCard(response({ action }));
      const node = screen.getByTestId('for-you-verdict');
      const style = JSON.stringify(node.props.style);
      view.unmount();
      return style;
    };
    const buy = styleFor('buy');
    expect(styleFor('wait')).toBe(buy);
    expect(styleFor('skip')).toBe(buy);
  });
});
