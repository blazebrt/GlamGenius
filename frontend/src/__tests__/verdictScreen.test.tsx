/**
 * The verdict screen, checked against what it promises.
 *
 * The four acceptance criteria are the four describe blocks. The first one —
 * "buy or not from the colour alone" — cannot be measured by a test runner, so
 * what is tested is the property that makes it possible: the answer is carried
 * by a filled colour block, the three colours are distinguishable, and nothing
 * technical is on the screen to read.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react-native';

import {
  BAND_COLOURS, ComponentRow, GradeBlock, IngredientList, REPORT_OPTIONS,
  FactorSection, ReportSheet, VerdictActions, VerdictLines,
} from '../components/verdict/VerdictPieces';
import { S, t } from '../strings/verdict';
import {
  buildVerdict, everydayNumber, rupees, type VerdictSource,
} from '../services/verdictModel';

/** Only the words rendered on screen — not props, styles or role names. */
function visibleText(node: unknown): string {
  if (typeof node === 'string') return node;
  if (Array.isArray(node)) return node.map(visibleText).join(' ');
  if (node && typeof node === 'object') {
    return visibleText((node as { children?: unknown }).children);
  }
  return '';
}

const base: VerdictSource = {
  outcome: 'graded',
  grade: 'D',
  productName: 'Glucose Biscuit',
  totalSugarG: 22.5,
  saltG: 0.9,
  totalFatG: 13.4,
  proteinG: 7.2,
  packSizeG: 100,
  components: [
    {
      key: 'processing', label: S.why.processing.label, plain: S.why.processing.nova4,
      band: 'red', rule: 'Ceiling C.', source: 'Monteiro CA et al., 2019',
      term: { word: S.why.processing.term, plain: S.why.processing.termPlain },
    },
    {
      key: 'nutrients', label: S.why.nutrients.label,
      plain: t(S.why.nutrients.highSaturatedFat, { source: 'from palm, 3rd ingredient' }),
      band: 'yellow', rule: 'Down 1 step.', source: 'UK Food Standards Agency',
      term: { word: S.why.nutrients.term, plain: S.why.nutrients.termPlain },
    },
    {
      key: 'additives', label: S.why.additives.label, plain: S.why.additives.none,
      band: 'green', rule: '', source: 'FSSAI',
    },
    {
      key: 'naming', label: S.why.naming.label, plain: S.why.naming.notPromised,
      band: 'green', rule: '', source: 'FSSAI Labelling Regulations, 2020',
    },
  ],
  ingredients: [
    { name: 'Refined wheat flour (maida)', tier: 'plain', tierLabel: S.ingredients.tierPlain,
      description: S.ingredients.unknownIngredient },
    { name: 'Emulsifier (INS 322)', tier: 'green', tierLabel: S.ingredients.tierGreen,
      description: 'Keeps oil and water mixed.' },
  ],
  alternative: { name: 'Marie Light', pricePaise: 3000, grade: 'C' },
};

// ---------------------------------------------------------------------------
// "buy or not from the colour alone, in under three seconds"
// ---------------------------------------------------------------------------
describe('the colour carries the answer', () => {
  it('fills a block with the colour rather than tinting an accent', () => {
    const view = buildVerdict({ ...base, grade: 'A' });
    render(<GradeBlock view={view} />);
    const block = screen.getByLabelText(/Grade A/);
    const style = Array.isArray(block.props.style)
      ? Object.assign({}, ...block.props.style)
      : block.props.style;
    expect(style.backgroundColor).toBe(BAND_COLOURS.green.fill);
  });

  it.each([
    ['A', 'green'], ['B', 'green'], ['C', 'yellow'], ['D', 'red'], ['E', 'red'],
  ] as const)('maps %s to %s', (grade, band) => {
    expect(buildVerdict({ ...base, grade }).band).toBe(band);
  });

  it('gives the three colours three different fills', () => {
    const fills = new Set(Object.values(BAND_COLOURS).map((row) => row.fill));
    expect(fills.size).toBe(3);
  });

  it('says the answer in two or three words, not a sentence', () => {
    for (const grade of ['A', 'B', 'C', 'D', 'E'] as const) {
      const words = buildVerdict({ ...base, grade }).verdict.split(' ');
      expect(words.length).toBeLessThanOrEqual(3);
    }
  });

  it('names the colour for a screen reader, so it is not the only channel', () => {
    expect(BAND_COLOURS.green.name).toBe(S.a11y.colourGreen);
    expect(BAND_COLOURS.red.name).toBe(S.a11y.colourRed);
  });
});

// ---------------------------------------------------------------------------
// "no technical term appears on the primary screen"
// ---------------------------------------------------------------------------
describe('the primary screen', () => {
  it('shows three lines and no more', () => {
    const view = buildVerdict(base);
    render(<VerdictLines view={view} onReport={jest.fn()} />);
    expect(screen.getByText(view.action)).toBeTruthy();
    expect(screen.getByText(view.everydayNumber)).toBeTruthy();
    expect(screen.getByText(view.alternativeLine!)).toBeTruthy();
  });

  it('keeps the action under ten words', () => {
    for (const grade of ['A', 'B', 'C', 'D', 'E'] as const) {
      const words = buildVerdict({ ...base, grade }).action.split(/\s+/);
      expect(words.length).toBeLessThan(10);
    }
  });

  it('shows the verified label quantity rather than a familiar-unit conversion', () => {
    const view = buildVerdict(base);
    render(<VerdictLines view={view} onReport={jest.fn()} />);
    const shown = JSON.stringify(screen.toJSON()).toLowerCase();
    expect(shown).toContain('22.5 g total sugar per 100 g');
    expect(shown).not.toContain('spoons of sugar');
  });

  it('keeps unverified familiar-unit conversions disabled', () => {
    expect(everydayNumber({ ...base, totalSugarG: 22.5, packSizeG: 100 }))
      .toBe('22.5 g total sugar per 100 g');
    expect(everydayNumber({ ...base, totalSugarG: 22.5, packSizeG: 60 }))
      .toBe('22.5 g total sugar per 100 g');
  });

  it('shows the declared label fact without a made-up comparison', () => {
    const dal = {
      ...base, grade: 'A' as const, totalSugarG: 2.4, saltG: 0.04,
      totalFatG: 1.7, proteinG: 22, packSizeG: 100,
    };
    expect(everydayNumber(dal)).toBe('2.4 g total sugar per 100 g');
  });

  it('keeps a declared label fact visible when no reviewed comparison exists', () => {
    expect(everydayNumber({
      ...base, totalSugarG: 1, saltG: 0.1, totalFatG: 2, proteinG: 3,
    })).toBe('1 g total sugar per 100 g');
  });

  it('shows one better product with its price and its letter', () => {
    const view = buildVerdict(base);
    expect(view.alternativeLine).toBe('Marie Light, ₹30 — C');
  });

  it('formats rupees the way an Indian price is written', () => {
    expect(rupees(3000)).toBe('30');
    expect(rupees(125000)).toBe('1,250');
  });

  it('offers Why, Listen and Share', () => {
    render(
      <VerdictActions
        onWhy={jest.fn()} onListen={jest.fn()} onShare={jest.fn()}
        speaking={false} speechAvailable
      />,
    );
    expect(screen.getByLabelText(S.a11y.why)).toBeTruthy();
    expect(screen.getByLabelText(S.a11y.listen)).toBeTruthy();
    expect(screen.getByLabelText(S.a11y.share)).toBeTruthy();
  });
});

describe('factor quantities', () => {
  it('states the verified quantity and its per-100 basis together', () => {
    render(
      <FactorSection
        title={S.factors.negatives}
        empty={S.factors.noNegatives}
        onExplain={jest.fn()}
        rows={[{
          key: 'sugar', label: 'sugar', status: 'high', band: 'red',
          quantity: { value: 26.4, unit: 'g', basis: 'per_100_g' },
          explanation: 'high_sugar', rule: 'grade.step2.sugar', sources: [],
        }]}
      />,
    );

    expect(screen.getByText('26.4 g per 100 g')).toBeTruthy();
    // The name of the thing, resolved from the label key. A row that
    // shows only "High" and a number does not say high what.
    expect(screen.getByText(S.factors.label_sugar)).toBeTruthy();
  });

  it('locks customer factor headings to Negatives and Positives', () => {
    expect(S.factors.negatives).toBe('Negatives');
    expect(S.factors.positives).toBe('Positives');
    expect(Object.values(S.factors)).not.toContain('What lowers it');
    expect(Object.values(S.factors)).not.toContain('What helps');
  });

  it('uses the server decision action instead of inferring it from the grade', () => {
    const view = buildVerdict({ ...base, grade: 'A', decision: { action: 'skip', reasonKey: 'sugar' } });
    expect(view.action).toBe(S.primary.decisionSkip);
  });
});

// ---------------------------------------------------------------------------
// "voice output works"
// ---------------------------------------------------------------------------
describe('voice', () => {
  it('reads the verdict, the action, the number and the alternative', () => {
    const view = buildVerdict(base);
    expect(view.spoken).toContain(view.verdict);
    expect(view.spoken).toContain(view.action);
    expect(view.spoken).toContain('22.5 g total sugar per 100 g');
    expect(view.spoken).toContain('Marie Light');
    expect(view.spoken).toContain('30 rupees');
  });

  it('says "rupees" rather than a symbol, because it is heard not read', () => {
    expect(buildVerdict(base).spoken).not.toContain('₹');
  });

  it('offers the speaker to everybody, never labelled as an accessibility aid', () => {
    render(
      <VerdictActions
        onWhy={jest.fn()} onListen={jest.fn()} onShare={jest.fn()}
        speaking={false} speechAvailable
      />,
    );
    // The words a person reads, not React Native's own prop names.
    const shown = visibleText(screen.toJSON()).toLowerCase();
    for (const word of ['accessibility', 'literacy', 'cannot read', 'illiterate', 'assist']) {
      expect(shown).not.toContain(word);
    }
  });

  it('switches the button to stop while it is speaking', () => {
    const listen = jest.fn();
    render(
      <VerdictActions
        onWhy={jest.fn()} onListen={listen} onShare={jest.fn()}
        speaking speechAvailable
      />,
    );
    fireEvent.press(screen.getByLabelText(S.a11y.stop));
    expect(listen).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// "report an error is one tap from any number"
// ---------------------------------------------------------------------------
describe('report an error', () => {
  it('is one tap from the number on the primary screen', () => {
    const report = jest.fn();
    const view = buildVerdict(base);
    render(<VerdictLines view={view} onReport={report} />);
    fireEvent.press(screen.getByLabelText(t(S.a11y.report, { subject: view.everydayNumber })));
    expect(report).toHaveBeenCalledWith(view.everydayNumber);
  });

  it('is one tap from any ingredient', () => {
    const report = jest.fn();
    render(<IngredientList ingredients={base.ingredients} onReport={report} onExplain={jest.fn()} />);
    fireEvent.press(screen.getByLabelText(t(S.a11y.report, { subject: 'Emulsifier (INS 322)' })));
    expect(report).toHaveBeenCalledWith('Emulsifier (INS 322)');
  });

  it('offers structured options rather than a text box', () => {
    render(
      <ReportSheet
        subject="5 spoons of sugar" onPick={jest.fn()} onCancel={jest.fn()}
        onAddPhoto={jest.fn()} photoAdded={false} busy={false} status={null}
      />,
    );
    for (const option of REPORT_OPTIONS) {
      expect(screen.getByLabelText(option.label)).toBeTruthy();
    }
  });

  it('takes the photo inline', () => {
    const addPhoto = jest.fn();
    render(
      <ReportSheet
        subject="sugar" onPick={jest.fn()} onCancel={jest.fn()}
        onAddPhoto={addPhoto} photoAdded={false} busy={false} status={null}
      />,
    );
    fireEvent.press(screen.getByLabelText(S.report.addPhoto));
    expect(addPhoto).toHaveBeenCalled();
  });

  it('never asks anyone to send an email', () => {
    render(
      <ReportSheet
        subject="sugar" onPick={jest.fn()} onCancel={jest.fn()}
        onAddPhoto={jest.fn()} photoAdded={false} busy={false} status={null}
      />,
    );
    const shown = JSON.stringify(screen.toJSON()).toLowerCase();
    for (const word of ['email', 'e-mail', 'mail us', '@', 'write to']) {
      expect(shown).not.toContain(word);
    }
  });
});

// ---------------------------------------------------------------------------
// Why, and the ingredient list
// ---------------------------------------------------------------------------
describe('the why screen', () => {
  it('shows four components, each with a colour dot and a plain sentence', () => {
    for (const component of base.components) {
      render(<ComponentRow component={component} expanded={false} onToggle={jest.fn()} />);
      expect(screen.getByText(component.label)).toBeTruthy();
      expect(screen.getByText(component.plain)).toBeTruthy();
    }
    expect(base.components).toHaveLength(4);
  });

  it('expands to the rule and the source', () => {
    const component = base.components[0];
    render(<ComponentRow component={component} expanded onToggle={jest.fn()} />);
    expect(screen.getByText(S.why.ruleLead)).toBeTruthy();
    expect(screen.getByText(S.why.sourceLead)).toBeTruthy();
    expect(screen.getAllByText(component.source).length).toBeGreaterThan(0);
  });

  it('never shows a technical word without its explanation beside it', () => {
    const component = base.components[0];
    render(<ComponentRow component={component} expanded onToggle={jest.fn()} />);
    expect(screen.getByText(component.term!.word)).toBeTruthy();
    const shown = JSON.stringify(screen.toJSON());
    expect(shown).toContain(component.term!.plain);
  });

  it('keeps the technical word out of the collapsed row', () => {
    const component = base.components[0];
    render(<ComponentRow component={component} expanded={false} onToggle={jest.fn()} />);
    expect(screen.queryByText(component.term!.word)).toBeNull();
  });
});

describe('the ingredient list', () => {
  it('shows every ingredient with a tier and a plain description', () => {
    render(<IngredientList ingredients={base.ingredients} onReport={jest.fn()} onExplain={jest.fn()} />);
    expect(screen.getByText('Refined wheat flour (maida)')).toBeTruthy();
    expect(screen.getByText('Keeps oil and water mixed.')).toBeTruthy();
    expect(screen.getByText(S.ingredients.orderNote)).toBeTruthy();
  });

  it('says so when we have no description rather than inventing one', () => {
    render(<IngredientList ingredients={base.ingredients} onReport={jest.fn()} onExplain={jest.fn()} />);
    expect(screen.getByText(S.ingredients.unknownIngredient)).toBeTruthy();
  });

  it('says so when the pack carried no ingredient list', () => {
    render(<IngredientList ingredients={[]} onReport={jest.fn()} onExplain={jest.fn()} />);
    expect(screen.getByText(S.ingredients.empty)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// The two answers that are not letters
// ---------------------------------------------------------------------------
describe('answers that are not a letter', () => {
  it('shows no letter for a cooking ingredient', () => {
    const view = buildVerdict({ ...base, outcome: 'not_graded', grade: null });
    expect(view.letter).toBeNull();
    expect(view.action).toBe(S.primary.actionNotGraded);
  });

  it('shows no letter and no guess when the label was incomplete', () => {
    const view = buildVerdict({ ...base, outcome: 'not_enough_information', grade: null });
    expect(view.letter).toBeNull();
    expect(view.everydayNumber).toBe('');
    expect(view.spoken).toBe(S.voice.unknown);
  });
});

// ---------------------------------------------------------------------------
// LEGAL_RULES.md
// ---------------------------------------------------------------------------
describe('the strings', () => {
  const walk = (node: unknown, out: string[] = []): string[] => {
    if (typeof node === 'string') out.push(node);
    else if (node && typeof node === 'object') {
      Object.values(node as Record<string, unknown>).forEach((value) => walk(value, out));
    }
    return out;
  };

  it('never claims a health outcome', () => {
    for (const line of walk(S)) {
      const lower = line.toLowerCase();
      for (const word of ['unhealthy', 'healthy', 'toxic', 'poison', 'cancer', 'disease',
        'harmful', 'damages', 'cures', 'prevents']) {
        expect(lower).not.toContain(word);
      }
    }
  });

  it('never mocks a brand or calls a product junk', () => {
    for (const line of walk(S)) {
      const lower = line.toLowerCase();
      for (const word of ['junk', 'garbage', 'rubbish', 'fake', 'scam', 'cheap', 'nasty']) {
        expect(lower).not.toContain(word);
      }
    }
  });

  it('never tells a person what their body needs', () => {
    for (const line of walk(S)) {
      const lower = line.toLowerCase();
      for (const phrase of ['you should', 'your diet', 'you need', 'your health', 'for you']) {
        expect(lower).not.toContain(phrase);
      }
    }
  });

  it('has no empty string anywhere', () => {
    for (const line of walk(S)) {
      expect(line.length).toBeGreaterThan(0);
    }
  });
});

