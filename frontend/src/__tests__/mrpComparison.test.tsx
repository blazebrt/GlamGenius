/**
 * What confirmed pack labels stated as MRP, on the verdict surface.
 *
 * Most of this is about restraint. The arithmetic is the easy part; the hard
 * part is that a number beside a product is read as advice unless the copy
 * works to stop it. So the bulk of these tests assert what the surface must
 * never say, and that the pack sizes are always visible beside the normalised
 * figures — because ₹100 looks cheaper than ₹120 right up until you divide.
 */
import React from 'react';
import { render, screen } from '@testing-library/react-native';

import {
  MrpComparison, formatInr, formatObservedOn, formatQuantity,
} from '../components/verdict/MrpComparison';
import { BetterOption } from '../components/verdict/BetterOption';
import { LabelReview } from '../components/scan/ScanPieces';
import { ODBL_ATTRIBUTION_TEXT } from '../components/common/OpenFoodFactsAttribution';
import { toVerdictSource } from '../services/verdictClient';
import type { MrpComparison as MrpComparisonModel } from '../services/verdictModel';
import { S } from '../strings/verdict';

const observation = (overrides: Record<string, unknown> = {}) => ({
  barcode: '8902000000001',
  mrpInr: '120.00',
  quantity: { amount: '500', unit: 'g' as const },
  mrpPer100Inr: '24.00',
  observedAt: '2026-09-02T10:00:00+00:00',
  source: 'confirmed_pack_label' as const,
  ...overrides,
});

const available = (overrides: Record<string, unknown> = {}): MrpComparisonModel => ({
  policyVersion: 'pack-mrp-value-v1',
  status: 'available',
  reasonKey: 'comparison_available',
  comparison: {
    basis: 'per_100g',
    current: observation(),
    candidate: observation({
      barcode: '8902000000002', mrpInr: '100.00',
      quantity: { amount: '400', unit: 'g' as const }, mrpPer100Inr: '25.00',
    }),
    relationship: 'candidate_higher_mrp_per_100',
    differenceInrPer100: '1.00',
    ...overrides,
  },
});

const missing: MrpComparisonModel = {
  policyVersion: 'pack-mrp-value-v1',
  status: 'not_enough_information',
  reasonKey: 'candidate_mrp_unavailable',
  comparison: null,
};

function textOf(node: unknown): string {
  if (typeof node === 'string') return node;
  if (Array.isArray(node)) return node.map(textOf).join(' ');
  if (node && typeof node === 'object') {
    const element = node as { children?: unknown; props?: { children?: unknown } };
    return textOf(element.children ?? element.props?.children);
  }
  return '';
}

const rendered = () => textOf(screen.toJSON());

// ---------------------------------------------------------------------------
// Formatting: exact strings in, formatted strings out, no arithmetic
// ---------------------------------------------------------------------------
describe('INR formatting', () => {
  it.each([
    ['120.00', '₹120'],
    ['120.50', '₹120.50'],
    ['1299.00', '₹1,299'],
    ['9999.00', '₹9,999'],
    ['100000.00', '₹1,00,000'],
    ['0.50', '₹0.50'],
    ['-4.00', '-₹4'],
  ])('renders %s as %s', (input, expected) => {
    expect(formatInr(input)).toBe(expected);
  });

  it('never reinterprets the separator through a device locale', () => {
    // The backend sent an exact decimal string. Parsing it into a float and
    // formatting that back is how "24.00" becomes "2.400" somewhere.
    expect(formatInr('24.00')).toBe('₹24');
    expect(formatInr('24.05')).toBe('₹24.05');
  });

  it('keeps the unit with the amount', () => {
    expect(formatQuantity({ amount: '500', unit: 'g' })).toBe('500 g');
    expect(formatQuantity({ amount: '250', unit: 'ml' })).toBe('250 ml');
  });

  it('dates an observation rather than calling it current', () => {
    expect(formatObservedOn('2026-09-02T10:00:00+00:00')).toBe('2 Sep 2026');
    // A malformed date is shown as it arrived rather than invented.
    expect(formatObservedOn('not-a-date')).toBe('not-a-date');
  });
});

// ---------------------------------------------------------------------------
// The available comparison
// ---------------------------------------------------------------------------
describe('an available MRP comparison', () => {
  it('shows both packs as they were, and both normalised figures', () => {
    render(<MrpComparison value={available()} />);
    expect(screen.getByText(S.mrpComparison.heading)).toBeTruthy();
    // Absolute pack facts, so the arithmetic below can be checked.
    expect(screen.getByText('MRP ₹120 · 500 g')).toBeTruthy();
    expect(screen.getByText('MRP ₹100 · 400 g')).toBeTruthy();
    // And the normalised figures, which say the opposite of the absolute ones.
    expect(screen.getByText('MRP per 100 g ₹24')).toBeTruthy();
    expect(screen.getByText('MRP per 100 g ₹25')).toBeTruthy();
  });

  it('names which product each side is', () => {
    render(<MrpComparison value={available()} />);
    expect(screen.getByText(S.mrpComparison.current)).toBeTruthy();
    expect(screen.getByText(S.mrpComparison.alternative)).toBeTruthy();
  });

  it('dates every observation', () => {
    render(<MrpComparison value={available()} />);
    expect(screen.getAllByText('Observed on a confirmed pack · 2 Sep 2026')).toHaveLength(2);
  });

  it('says what an MRP is, so the numbers cannot read as a shop price', () => {
    render(<MrpComparison value={available()} />);
    expect(screen.getByText(S.mrpComparison.disclosure)).toBeTruthy();
    expect(S.mrpComparison.disclosure).toMatch(/printed on the pack/);
  });

  it('labels a drink comparison per 100 ml', () => {
    render(<MrpComparison value={available({ basis: 'per_100ml' })} />);
    expect(rendered()).toContain('MRP per 100 ml');
    expect(rendered()).not.toContain('MRP per 100 g');
  });

  it('renders nothing at all on a response that predates the feature', () => {
    expect(render(<MrpComparison value={null} />).toJSON()).toBeNull();
    expect(render(<MrpComparison value={undefined} />).toJSON()).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// What it may never say
// ---------------------------------------------------------------------------
//: Characterisation. V1 reports arithmetic and stops.
const FORBIDDEN_JUDGEMENT = [
  /\bsave\b/i, /\bsaving/i, /cheaper/i, /\bcheap\b/i, /expensive/i, /best value/i,
  /better value/i, /good value/i, /poor value/i, /worth it/i, /worth the/i,
  /overpriced/i, /budget/i, /premium/i, /bargain/i, /\bdeal\b/i, /discount/i,
  /\boffer\b/i, /\bwinner\b/i, /smart/i,
];
//: Claims about what a shop will charge. We have no evidence of any of it.
const FORBIDDEN_SELLING_PRICE = [
  /price today/i, /current price/i, /selling price/i, /you will pay/i,
  /store price/i, /online price/i, /deal price/i, /street price/i, /\bmarket price\b/i,
];
//: Commerce, and anything addressed to the person rather than the product.
const FORBIDDEN_COMMERCE_AND_PERSON = [
  /affiliate/i, /amazon/i, /flipkart/i, /blinkit/i, /zepto/i, /instamart/i,
  /add to cart/i, /buy now/i, /shop now/i,
  /for you/i, /your budget/i, /affordable for you/i, /fits your/i, /you should/i,
];

describe('the words an MRP comparison may never use', () => {
  it.each([
    ['a judgement', FORBIDDEN_JUDGEMENT],
    ['a selling price', FORBIDDEN_SELLING_PRICE],
    ['commerce or personalisation', FORBIDDEN_COMMERCE_AND_PERSON],
  ])('says nothing that reads as %s', (_label, patterns) => {
    render(<MrpComparison value={available()} />);
    const text = rendered();
    for (const pattern of patterns) {
      expect(text).not.toMatch(pattern);
    }
  });

  it('keeps the same promise in the missing state', () => {
    render(<MrpComparison value={missing} />);
    const text = rendered();
    for (const pattern of [
      ...FORBIDDEN_JUDGEMENT, ...FORBIDDEN_SELLING_PRICE, ...FORBIDDEN_COMMERCE_AND_PERSON,
    ]) {
      expect(text).not.toMatch(pattern);
    }
  });

  it('never implies a market was searched', () => {
    render(<MrpComparison value={missing} />);
    for (const claim of [
      /no price found/i, /no cheaper/i, /price unavailable everywhere/i,
      /this is the best/i, /lowest price/i,
    ]) {
      expect(rendered()).not.toMatch(claim);
    }
  });

  it('carries no link anywhere', () => {
    render(<MrpComparison value={available()} />);
    expect(screen.queryByRole('link')).toBeNull();
    expect(rendered()).not.toMatch(/https?:\/\//);
  });
});

// ---------------------------------------------------------------------------
// The missing state
// ---------------------------------------------------------------------------
describe('when there is no recent pack reading', () => {
  it('says so plainly, in keyed copy, and shows no numbers', () => {
    render(<MrpComparison value={missing} />);
    expect(screen.getByText(S.mrpComparison.notEnoughInformation)).toBeTruthy();
    expect(S.mrpComparison.notEnoughInformation).toBe(
      'Not enough recent pack information to compare MRP.',
    );
    expect(rendered()).not.toMatch(/₹/);
  });

  it('is readable by a screen reader rather than a blank surface', () => {
    render(<MrpComparison value={missing} />);
    expect(screen.getByLabelText(S.mrpComparison.a11y.missing)).toBeTruthy();
    expect(screen.getByText(S.mrpComparison.heading).props.accessibilityRole).toBe('header');
  });

  it('shows the missing line when the server says available but sent nothing', () => {
    // Defence in depth against a malformed payload: no half-rendered card.
    render(<MrpComparison value={{ ...missing, status: 'available' }} />);
    expect(screen.getByText(S.mrpComparison.notEnoughInformation)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Accessibility
// ---------------------------------------------------------------------------
describe('accessibility', () => {
  it('reads role, MRP, pack size, basis and date as one sentence per side', () => {
    render(<MrpComparison value={available()} />);
    expect(screen.getByLabelText(
      'Current. MRP ₹120 for 500 g. MRP per 100 g ₹24. Observed on a confirmed pack on 2 Sep 2026.',
    )).toBeTruthy();
    expect(screen.getByLabelText(
      'Alternative. MRP ₹100 for 400 g. MRP per 100 g ₹25. Observed on a confirmed pack on 2 Sep 2026.',
    )).toBeTruthy();
  });

  it('carries a semantic heading', () => {
    render(<MrpComparison value={available()} />);
    expect(screen.getByText(S.mrpComparison.heading).props.accessibilityRole).toBe('header');
  });

  it('never conveys which MRP is lower by arrow or colour alone', () => {
    render(<MrpComparison value={available()} />);
    // Both figures are printed in full, so the comparison survives without
    // sight of any indicator at all.
    expect(rendered()).toContain('₹24');
    expect(rendered()).toContain('₹25');
    for (const glyph of ['↓', '↑', '▲', '▼', '←', '→']) {
      expect(rendered()).not.toContain(glyph);
    }
  });
});

// ---------------------------------------------------------------------------
// Placement inside the Better option card
// ---------------------------------------------------------------------------
const candidate = {
  policyVersion: 'comparable-food-alternative-v1',
  status: 'available' as const,
  reasonKey: 'comparable_option_found',
  candidate: {
    barcode: '8902000000002',
    productName: 'Sunfield Oat Porridge',
    brand: 'Sunfield',
    grade: 'B' as const,
    band: 'green' as const,
    decision: 'buy' as const,
    comparison: {
      categoryMatch: 'exact_source_leaf' as const,
      categorySource: 'open_food_facts' as const,
      currentGrade: 'C' as const,
      candidateGrade: 'B' as const,
      basis: 'per_100g' as const,
    },
    attributionText: ODBL_ATTRIBUTION_TEXT,
  },
};

describe('where the MRP comparison sits', () => {
  it('renders inside the Better option card, after the product and the action', () => {
    render(
      <BetterOption alternative={candidate} onView={jest.fn()} mrpComparison={available()} />,
    );
    const text = textOf(screen.toJSON());
    const product = text.indexOf('Sunfield Oat Porridge');
    const grades = text.indexOf('Grade B instead of Grade C');
    const action = text.indexOf(S.betterOption.viewAction);
    const mrp = text.indexOf(S.mrpComparison.heading);

    for (const index of [product, grades, action, mrp]) expect(index).toBeGreaterThan(-1);
    // Money is the last thing the card says.
    expect(mrp).toBeGreaterThan(product);
    expect(mrp).toBeGreaterThan(grades);
    expect(mrp).toBeGreaterThan(action);
  });

  it('leaves the Better option card unchanged when a price fixture changes', () => {
    const dear = render(
      <BetterOption alternative={candidate} onView={jest.fn()} mrpComparison={available()} />,
    );
    const withDearPrice = textOf(dear.toJSON());
    dear.unmount();

    const cheap = render(
      <BetterOption
        alternative={candidate}
        onView={jest.fn()}
        mrpComparison={available({
          candidate: observation({ mrpInr: '5.00', mrpPer100Inr: '1.00' }),
          relationship: 'candidate_lower_mrp_per_100',
          differenceInrPer100: '-23.00',
        })}
      />,
    );
    const withCheapPrice = textOf(cheap.toJSON());

    // The product, its grade and the action are identical either way.
    for (const unchanged of [
      'Sunfield Oat Porridge', 'Sunfield', 'Grade B instead of Grade C',
      S.betterOption.sameCategory, S.betterOption.viewAction,
    ]) {
      expect(withDearPrice).toContain(unchanged);
      expect(withCheapPrice).toContain(unchanged);
    }
  });

  it('renders the card with no money at all when there is no comparison', () => {
    render(<BetterOption alternative={candidate} onView={jest.fn()} />);
    expect(screen.getByText('Sunfield Oat Porridge')).toBeTruthy();
    expect(screen.queryByText(S.mrpComparison.heading)).toBeNull();
    expect(textOf(screen.toJSON())).not.toMatch(/₹/);
  });
});

// ---------------------------------------------------------------------------
// The label review, where an MRP first becomes visible
// ---------------------------------------------------------------------------
describe('confirming a transcribed MRP', () => {
  const facts = {
    product_name: 'Northstar Corn Flakes',
    net_quantity: '500 g',
    mrp_text: 'MRP ₹120',
    uncertain_fields: [],
  };

  it('shows the clause the camera read before anything is stored', () => {
    render(<LabelReview facts={facts} onConfirm={jest.fn()} onRetake={jest.fn()} />);
    expect(screen.getByText(S.labelReview.mrp)).toBeTruthy();
    expect(screen.getByText('MRP ₹120')).toBeTruthy();
  });

  it('offers no way to type a price', () => {
    render(<LabelReview facts={facts} onConfirm={jest.fn()} onRetake={jest.fn()} />);
    // A transcription is something a person checks, not something they compose.
    expect(screen.queryByPlaceholderText(/mrp|price|₹/i)).toBeNull();
    expect(screen.UNSAFE_queryAllByType(
      require('react-native').TextInput,
    )).toHaveLength(0);
    const text = textOf(screen.toJSON());
    for (const banned of [/enter mrp/i, /what did you pay/i, /sale price/i, /receipt/i]) {
      expect(text).not.toMatch(banned);
    }
  });

  it('shows no MRP row when the pack did not state one', () => {
    render(
      <LabelReview
        facts={{ product_name: 'Northstar Corn Flakes', uncertain_fields: ['mrp_text'] }}
        onConfirm={jest.fn()}
        onRetake={jest.fn()}
      />,
    );
    expect(screen.queryByText(S.labelReview.mrp)).toBeNull();
    // And the transcription says plainly that it could not read it.
    expect(textOf(screen.toJSON())).toContain('mrp_text');
  });
});

// ---------------------------------------------------------------------------
// The wire mapping
// ---------------------------------------------------------------------------
const wire = (value: unknown) => ({
  outcome: 'graded', grade: 'C', band: 'yellow', product_name: 'Northstar Corn Flakes',
  taxonomy: { domain: 'consumed', category: 'packaged_food', subcategory: 'cereal' },
  decision: { action: 'wait', reason_key: 'processing' },
  nutrition: { total_sugar_g: 8, salt_g: 0.5, total_fat_g: null, protein_g: 7 },
  components: [], ingredients: [], negatives: [], positives: [],
  quantity_guidance: null, purity_note: null, missing: [],
  confidence: { level: 'unverified', text: 'Unverified' },
  attribution: { text: ODBL_ATTRIBUTION_TEXT }, pack_size_g: 500, basis: 'solid',
  result_contract_version: 'v1', value,
}) as never;

describe('the wire mapping', () => {
  it('carries the server figures through without recomputing any of them', () => {
    const source = toVerdictSource(wire({
      policy_version: 'pack-mrp-value-v1',
      status: 'available',
      reason_key: 'comparison_available',
      comparison: {
        basis: 'per_100g',
        current: {
          barcode: '8902000000001', mrp_inr: '120.00',
          quantity: { amount: '500', unit: 'g' }, mrp_per_100_inr: '24.00',
          observed_at: '2026-09-02T10:00:00+00:00', source: 'confirmed_pack_label',
        },
        candidate: {
          barcode: '8902000000002', mrp_inr: '100.00',
          quantity: { amount: '400', unit: 'g' }, mrp_per_100_inr: '25.00',
          observed_at: '2026-09-02T10:00:00+00:00', source: 'confirmed_pack_label',
        },
        relationship: 'candidate_higher_mrp_per_100',
        difference_inr_per_100: '1.00',
      },
    }));
    expect(source.mrpComparison).toEqual(available());
    // Every money field is still the exact string the server sent.
    expect(source.mrpComparison?.comparison?.current.mrpPer100Inr).toBe('24.00');
    expect(typeof source.mrpComparison?.comparison?.differenceInrPer100).toBe('string');
  });

  it('maps the missing state without inventing figures', () => {
    const source = toVerdictSource(wire({
      policy_version: 'pack-mrp-value-v1',
      status: 'not_enough_information',
      reason_key: 'current_mrp_observation_stale',
      comparison: null,
    }));
    expect(source.mrpComparison?.status).toBe('not_enough_information');
    expect(source.mrpComparison?.comparison).toBeNull();
  });

  it('is absent, not empty, on a response that carries no envelope', () => {
    expect(toVerdictSource(wire(undefined)).mrpComparison).toBeNull();
  });
});
