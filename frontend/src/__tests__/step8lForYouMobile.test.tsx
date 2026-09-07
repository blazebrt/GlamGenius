/**
 * Step 8L — the first complete skin-care FOR YOU loop, from the phone's side.
 *
 * Almost every test here is about something the client must *not* do: infer a
 * category, decide a verdict, invent a reason, choose a source, keep a personal
 * decision, or scan the barcode again after confirming a pack.
 */
import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react-native';
import { Linking, TextInput } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import fs from 'fs';
import path from 'path';

import { ForYouCard, FOR_YOU_COPY, isPresentable } from '../components/scan/ForYouCard';
import {
  LabelTypeChoice,
  SafetyPreflight,
  SkinCareLabelReview,
  SAFETY_COPY,
  toSafetyContext,
} from '../components/scan/SkinCarePieces';
import type { ForYouResponse } from '../services/productScan';

const FRONTEND_ROOT = path.resolve(__dirname, '..', '..');

/** Sentinel copy. The real reviewed sentence lives on the server. */
const SERVER_VERDICT = 'SERVER VERDICT';
const SERVER_REASON = 'SERVER REVIEWED REASON';

const CITATION = {
  source_key: 'sentinel.source.key',
  title: 'SENTINEL SOURCE TITLE',
  publisher: 'SENTINEL PUBLISHER',
  canonical_url: 'https://sentinel.example.org/exact/path?x=1',
  locator: 'SENTINEL LOCATOR',
  publication_date: null,
  version_or_revision: 'Last updated sentinel',
  jurisdiction: null,
};

function response(overrides: Partial<ForYouResponse['result']> = {}, extra: Partial<ForYouResponse> = {}): ForYouResponse {
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
  return render(
    <ForYouCard response={res} onRetry={noop} onAddSkinDetails={noop} {...props} />,
  );
}

// ---------------------------------------------------------------------------
// Server copy is the only copy
// ---------------------------------------------------------------------------
describe('the card renders what the server decided', () => {
  it('shows the exact server verdict and reason strings', () => {
    renderCard(response());
    expect(screen.getByText(SERVER_VERDICT)).toBeTruthy();
    expect(screen.getByText(SERVER_REASON)).toBeTruthy();
  });

  it('follows the server when the server copy changes', () => {
    renderCard(response({ verdict_text: 'A DIFFERENT WORD', reason_text: 'A different sentence.' }));
    expect(screen.getByText('A DIFFERENT WORD')).toBeTruthy();
    expect(screen.getByText('A different sentence.')).toBeTruthy();
    expect(screen.queryByText(SERVER_VERDICT)).toBeNull();
  });

  it('never derives the verdict word from the action field', () => {
    // action stays "buy"; only verdict_text is changed.
    renderCard(response({ verdict_text: 'ZZZ' }));
    expect(screen.getByText('ZZZ')).toBeTruthy();
    expect(screen.queryByText('BUY')).toBeNull();
    expect(screen.queryByText('Buy')).toBeNull();
  });

  it('never reconstructs prose from a reason key', () => {
    renderCard(response({ reason_key: 'for_you.not_enough.semantic_mapping', reason_text: SERVER_REASON }));
    expect(screen.getByText(SERVER_REASON)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// The source is the server's, exactly
// ---------------------------------------------------------------------------
describe('the cited source', () => {
  it('shows the exact citation metadata', () => {
    renderCard(response());
    expect(screen.getByText('SENTINEL SOURCE TITLE')).toBeTruthy();
    expect(screen.getByText('SENTINEL PUBLISHER')).toBeTruthy();
    expect(screen.getByText('SENTINEL LOCATOR')).toBeTruthy();
  });

  it('opens exactly the canonical URL', () => {
    const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(true as never);
    renderCard(response());
    fireEvent.press(screen.getByLabelText(FOR_YOU_COPY.openSource));
    expect(open).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith('https://sentinel.example.org/exact/path?x=1');
    open.mockRestore();
  });
});

// ---------------------------------------------------------------------------
// Fail closed
// ---------------------------------------------------------------------------
describe('a malformed presentable response', () => {
  it.each([
    ['citation', { citation: null }],
    ['verdict_text', { verdict_text: null }],
    ['reason_text', { reason_text: '' }],
    ['an openable url', { citation: { ...CITATION, canonical_url: '' } }],
  ])('shows no verdict when %s is missing', (_label, overrides) => {
    renderCard(response(overrides as Partial<ForYouResponse['result']>));
    expect(screen.queryByText(SERVER_VERDICT)).toBeNull();
    expect(screen.queryByLabelText(FOR_YOU_COPY.openSource)).toBeNull();
    expect(isPresentable(response(overrides as Partial<ForYouResponse['result']>))).toBe(false);
  });
});

describe('non-decision states', () => {
  it.each([
    'not_enough_information',
    'not_enough_decision_policy',
    'not_enough_explanation',
    'not_enough_copy',
    'pack_not_confirmed',
    'pack_category_not_supported',
    'some_future_status_nobody_has_written_yet',
  ])('never invents a verdict for %s', (status) => {
    renderCard(response({
      status, action: null, verdict_key: null, verdict_text: null,
      citation: null, reason_text: 'Server sentence for this state.',
    }));
    expect(screen.getByText('Server sentence for this state.')).toBeTruthy();
    for (const word of ['BUY', 'WAIT', 'SKIP', 'Buy', 'Wait', 'Skip']) {
      expect(screen.queryByText(word)).toBeNull();
    }
  });

  it('offers the profile editor only for the personal-context gap', () => {
    const add = jest.fn();
    renderCard(
      response({
        status: 'not_enough_information', action: null, verdict_key: null, verdict_text: null,
        citation: null, reason_key: 'for_you.not_enough.personal_context',
        reason_text: 'Server sentence about missing details.',
      }),
      { onAddSkinDetails: add },
    );
    expect(screen.getByText('Server sentence about missing details.')).toBeTruthy();
    fireEvent.press(screen.getByLabelText(FOR_YOU_COPY.addSkinDetails));
    expect(add).toHaveBeenCalled();
  });

  it('does not offer the profile editor for other gaps', () => {
    renderCard(response({
      status: 'not_enough_information', action: null, verdict_key: null, verdict_text: null,
      citation: null, reason_key: 'for_you.not_enough.semantic_mapping', reason_text: 'Other gap.',
    }));
    expect(screen.queryByLabelText(FOR_YOU_COPY.addSkinDetails)).toBeNull();
  });
});

describe('a hard handoff', () => {
  const handoff = response({
    status: 'handoff_required',
    reason: 'professional_handoff_required',
    action: null, verdict_key: null, verdict_text: null, citation: null,
    reason_key: 'for_you.handoff.required',
    reason_text: 'SERVER HANDOFF SENTENCE',
    handoff: { reason: 'pregnancy', message: 'SERVER HANDOFF SENTENCE' },
  }, { release: null });

  it('shows the authority message and nothing else', () => {
    renderCard(handoff);
    expect(screen.getByText('SERVER HANDOFF SENTENCE')).toBeTruthy();
    for (const word of ['BUY', 'WAIT', 'SKIP', SERVER_VERDICT]) {
      expect(screen.queryByText(word)).toBeNull();
    }
    expect(screen.queryByLabelText(FOR_YOU_COPY.openSource)).toBeNull();
    expect(screen.queryByText('SENTINEL SOURCE TITLE')).toBeNull();
  });
});

describe('technical unavailability', () => {
  it('is never turned into a verdict', () => {
    const retry = jest.fn();
    renderCard(null, { failed: true, onRetry: retry });
    expect(screen.getByText(FOR_YOU_COPY.unavailable)).toBeTruthy();
    for (const word of ['BUY', 'WAIT', 'SKIP']) {
      expect(screen.queryByText(word)).toBeNull();
    }
    fireEvent.press(screen.getByLabelText(FOR_YOU_COPY.retry));
    expect(retry).toHaveBeenCalled();
  });
});

describe('release provenance', () => {
  it('is never shown to the customer', () => {
    const { toJSON } = renderCard(response());
    const rendered = JSON.stringify(toJSON());
    for (const secret of ['rel-1', 'c'.repeat(64), 'f'.repeat(64), 'snap-1', 'sentinel.source.key']) {
      expect(rendered).not.toContain(secret);
    }
  });
});

// ---------------------------------------------------------------------------
// The category is asked, and the safety check collects only flags
// ---------------------------------------------------------------------------
describe('the label type question', () => {
  it('offers exactly the two supported kinds', () => {
    const choose = jest.fn();
    render(<LabelTypeChoice onChoose={choose} onCancel={noop} />);
    expect(screen.getByText('What kind of label is this?')).toBeTruthy();
    expect(screen.getByTestId('label-kind-packaged-food')).toBeTruthy();
    expect(screen.getByTestId('label-kind-skin-care')).toBeTruthy();
    expect(screen.queryByText('Hair care')).toBeNull();
    expect(screen.queryByText('Cosmetics')).toBeNull();
    fireEvent.press(screen.getByTestId('label-kind-skin-care'));
    expect(choose).toHaveBeenCalledWith('skin_care');
  });
});

describe('the safety preflight', () => {
  it('collects closed choices and no free text', () => {
    const { UNSAFE_queryAllByType } = render(<SafetyPreflight onSubmit={noop} />);
    expect(UNSAFE_queryAllByType(TextInput)).toHaveLength(0);
    for (const banned of ['medication name', 'diagnosis', 'Notes', 'Symptoms', 'Age']) {
      expect(screen.queryByText(banned)).toBeNull();
    }
  });

  it('sends only the flags that were selected', () => {
    const submit = jest.fn();
    render(<SafetyPreflight onSubmit={submit} />);
    fireEvent.press(screen.getByTestId('safety-pregnancy'));
    fireEvent.press(screen.getByTestId('safety-submit'));
    expect(submit).toHaveBeenCalledWith({ pregnancy: true });
  });

  it('treats "none of these" as exclusive and sends an empty context', () => {
    const submit = jest.fn();
    render(<SafetyPreflight onSubmit={submit} />);
    fireEvent.press(screen.getByTestId('safety-pregnancy'));
    fireEvent.press(screen.getByTestId('safety-medication_involved'));
    fireEvent.press(screen.getByTestId('safety-none'));
    fireEvent.press(screen.getByTestId('safety-submit'));
    expect(submit).toHaveBeenCalledWith({});
  });

  it('cannot be submitted before the person answers', () => {
    const submit = jest.fn();
    render(<SafetyPreflight onSubmit={submit} />);
    fireEvent.press(screen.getByTestId('safety-submit'));
    expect(submit).not.toHaveBeenCalled();
  });

  it('maps each choice to exactly one governed flag', () => {
    expect(toSafetyContext(new Set(['pregnancy', 'subject_is_child'] as never))).toEqual({
      pregnancy: true, subject_is_child: true,
    });
    expect(toSafetyContext(new Set())).toEqual({});
    // stated_age is deliberately not collected in this milestone.
    expect(JSON.stringify(toSafetyContext(new Set(['pregnancy'] as never)))).not.toContain('age');
  });

  it('exposes the reviewed structural copy', () => {
    render(<SafetyPreflight onSubmit={noop} />);
    expect(screen.getByText(SAFETY_COPY.heading)).toBeTruthy();
    expect(screen.getByText(SAFETY_COPY.body)).toBeTruthy();
  });
});

describe('an unreadable ingredient list', () => {
  it('shows the server message and offers no confirmation', () => {
    const confirm = jest.fn();
    render(
      <SkinCareLabelReview
        facts={{ product_name: 'A cream' }}
        ingredientsReadable={false}
        message="SERVER UNREADABLE MESSAGE"
        onConfirm={confirm}
        onRetake={noop}
      />,
    );
    expect(screen.getByText('SERVER UNREADABLE MESSAGE')).toBeTruthy();
    expect(screen.queryByTestId('skin-care-confirm')).toBeNull();
    expect(screen.getByTestId('skin-care-retake')).toBeTruthy();
    expect(confirm).not.toHaveBeenCalled();
  });

  it('offers confirmation once the ingredients read', () => {
    render(
      <SkinCareLabelReview
        facts={{ product_name: 'A cream', ingredients_text: 'Aqua, Glycerin' }}
        ingredientsReadable
        message={null}
        onConfirm={noop}
        onRetake={noop}
      />,
    );
    expect(screen.getByTestId('skin-care-confirm')).toBeTruthy();
    expect(screen.getByText('Aqua, Glycerin')).toBeTruthy();
  });

  it('never offers a way to type a correction', () => {
    const { UNSAFE_queryAllByType } = render(
      <SkinCareLabelReview
        facts={{ ingredients_text: 'Aqua' }}
        ingredientsReadable message={null} onConfirm={noop} onRetake={noop}
      />,
    );
    expect(UNSAFE_queryAllByType(TextInput)).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Nothing personal is written to the phone
// ---------------------------------------------------------------------------
describe('the personal decision is never persisted', () => {
  it('writes nothing to AsyncStorage when rendered', async () => {
    const setItem = jest.spyOn(AsyncStorage, 'setItem');
    setItem.mockClear();
    renderCard(response());
    await act(async () => { await Promise.resolve(); });
    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });
});

// ---------------------------------------------------------------------------
// No pilot knowledge, no decision logic, in production frontend files
// ---------------------------------------------------------------------------
const PRODUCTION_FILES = [
  'app/scan-product.tsx',
  'app/for-you-profile.tsx',
  'src/components/scan/ForYouCard.tsx',
  'src/components/scan/SkinCarePieces.tsx',
  'src/services/productScan.ts',
  'src/services/apiV2.ts',
];

/**
 * Strip comments before checking for banned *code*.
 *
 * A raw-text scan cannot tell a docstring that forbids a construct from code
 * that uses it, so the sentence "there is no local action-label map and no
 * .toUpperCase()" would trip the very guard it describes. Prose is checked
 * separately where prose is what matters.
 */
function code(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .split('\n')
    .map((line) => line.replace(/(^|[^:])\/\/.*$/, '$1'))
    .join('\n');
}

describe('the production client carries no science and no decision logic', () => {
  const sources = PRODUCTION_FILES.map((file) => ({
    file,
    text: fs.readFileSync(path.join(FRONTEND_ROOT, file), 'utf8'),
  }));

  it.each(['petrolatum', 'supporting_only', 'for_you.semantic.', 'for_you.policy.', 'for_you.explanation.'])(
    'never mentions %s',
    (needle) => {
      sources.forEach(({ file, text }) => {
        expect(text.toLowerCase()).not.toContain(needle.toLowerCase());
        expect(file).toBeTruthy();
      });
    },
  );

  it('never maps a signal to an action', () => {
    sources.forEach(({ file, text }) => {
      const lowered = code(text).toLowerCase();
      for (const pair of ['supporting', 'cautionary']) {
        expect(lowered).not.toContain(`${pair}:`);
        expect(lowered).not.toContain(`'${pair}'`);
      }
      expect(file).toBeTruthy();
    });
  });

  it('never upper-cases the action into customer copy', () => {
    sources.forEach(({ text }) => {
      expect(code(text)).not.toMatch(/action[^\n]{0,40}toUpperCase/);
    });
  });

  it('never imports a backend knowledge pack', () => {
    sources.forEach(({ text }) => {
      expect(code(text)).not.toContain('knowledge_packs');
    });
  });

  it('keeps the FOR YOU card free of a reason-key copy table', () => {
    const card = code(sources.find((s) => s.file.endsWith('ForYouCard.tsx'))!.text);
    // No literal reason key at all: the one that drives navigation is imported
    // from the service layer rather than spelled out here, so this file cannot
    // grow a key-to-prose table by accident.
    const keys = card.match(/'for_you\.[a-z_.]+'/g) ?? [];
    expect(keys).toEqual([]);
  });
});
