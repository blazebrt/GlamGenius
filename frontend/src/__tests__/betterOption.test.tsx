/**
 * The comparable alternative on the verdict surface.
 *
 * Most of what follows is about what the card must NOT say. The card makes one
 * narrow claim — the source lists another product in the same category, and it
 * grades higher under the same rules — and every failure mode is a way of
 * quietly claiming more than that: a health benefit, a price, a market search,
 * or a choice made for the person holding the phone.
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';

import { BetterOption } from '../components/verdict/BetterOption';
import { ODBL_ATTRIBUTION_TEXT } from '../components/common/OpenFoodFactsAttribution';
import { toVerdictSource } from '../services/verdictClient';
import type { ComparableAlternative } from '../services/verdictModel';
import { S } from '../strings/verdict';

const candidate = (overrides: Record<string, unknown> = {}) => ({
  barcode: '8901000000002',
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
  ...overrides,
});

const available = (overrides: Record<string, unknown> = {}): ComparableAlternative => ({
  policyVersion: 'comparable-food-alternative-v1',
  status: 'available',
  reasonKey: 'comparable_option_found',
  candidate: candidate(overrides),
});

const missing: ComparableAlternative = {
  policyVersion: 'comparable-food-alternative-v1',
  status: 'not_enough_information',
  reasonKey: 'no_comparable_candidate_in_cached_data',
  candidate: null,
};

/** Every word the whole card rendered, however deeply nested. */
function renderedText(): string {
  return screen.root ? textOf(screen.root) : '';
}

function textOf(node: unknown): string {
  if (typeof node === 'string') return node;
  if (Array.isArray(node)) return node.map(textOf).join(' ');
  if (node && typeof node === 'object') {
    const element = node as { children?: unknown; props?: { children?: unknown } };
    return textOf(element.children ?? element.props?.children);
  }
  return '';
}

// ---------------------------------------------------------------------------
// The available card
// ---------------------------------------------------------------------------
describe('an available comparable alternative', () => {
  it('names the product, its brand and both grades', () => {
    render(<BetterOption alternative={available()} onView={jest.fn()} />);
    expect(screen.getByText(S.betterOption.heading)).toBeTruthy();
    expect(screen.getByText('Sunfield Oat Porridge')).toBeTruthy();
    expect(screen.getByText('Sunfield')).toBeTruthy();
    // Both letters, in text. The comparison is stated, never characterised.
    expect(screen.getByText('Grade B instead of Grade C')).toBeTruthy();
  });

  it('says the categories matched, without claiming we authored the category', () => {
    render(<BetterOption alternative={available()} onView={jest.fn()} />);
    expect(screen.getByText(S.betterOption.sameCategory)).toBeTruthy();
    expect(S.betterOption.sameCategory).toMatch(/Open Food Facts/);
    // Nothing may suggest a GlamGenius-certified or official category.
    for (const claim of [/certified/i, /verified category/i, /official category/i]) {
      expect(renderedText()).not.toMatch(claim);
    }
  });

  it('states availability as what the source says, never as stock', () => {
    render(<BetterOption alternative={available()} onView={jest.fn()} />);
    expect(screen.getByText(S.betterOption.availability)).toBeTruthy();
    for (const claim of [/in stock/i, /near you/i, /available now/i, /delivery/i]) {
      expect(renderedText()).not.toMatch(claim);
    }
  });

  it('renders the Open Food Facts attribution with their data', () => {
    render(<BetterOption alternative={available()} onView={jest.fn()} />);
    expect(screen.getByText(ODBL_ATTRIBUTION_TEXT)).toBeTruthy();
    expect(screen.getByLabelText('Open Food Facts attribution')).toBeTruthy();
  });

  it('offers one secondary action, which reaches the product', () => {
    const onView = jest.fn();
    render(<BetterOption alternative={available()} onView={onView} />);
    const action = screen.getByLabelText('View Sunfield Oat Porridge');
    fireEvent.press(action);
    expect(onView).toHaveBeenCalledWith('8901000000002');
    expect(onView).toHaveBeenCalledTimes(1);
  });

  it('omits the brand rather than inventing one', () => {
    render(<BetterOption alternative={available({ brand: null })} onView={jest.fn()} />);
    expect(screen.getByText('Sunfield Oat Porridge')).toBeTruthy();
    expect(screen.queryByText('Sunfield')).toBeNull();
    // And a blank brand string is the same as no brand at all.
    screen.unmount();
    render(<BetterOption alternative={available({ brand: '   ' })} onView={jest.fn()} />);
    expect(screen.queryByText('Sunfield')).toBeNull();
  });

  it('falls back to the barcode when the source carries no name', () => {
    render(<BetterOption alternative={available({ productName: null })} onView={jest.fn()} />);
    expect(screen.getByText('8901000000002')).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// What the card may never say
// ---------------------------------------------------------------------------
//: Characterisation, a health claim, or a judgement of the current product.
const FORBIDDEN_WORDS = [
  /healthier/i, /healthiest/i, /\bhealthy\b/i, /safer/i, /safest/i, /cleaner/i,
  /cleanest/i, /\bbest\b/i, /superior/i, /\bjunk\b/i, /toxic/i, /\bbad\b/i,
  /swap/i, /upgrade/i,
];
//: Money, in every form. Step 6A chooses independently of what things cost.
const FORBIDDEN_COMMERCE = [
  /₹/, /\brs\.?\b/i, /price/i, /\bmrp\b/i, /discount/i, /\boffer\b/i, /\bbuy now\b/i,
  /add to cart/i, /shop/i, /amazon/i, /flipkart/i, /blinkit/i, /zepto/i,
];
//: Anything implying the choice was made for this particular person.
const FORBIDDEN_PERSONAL = [
  /for you/i, /your skin/i, /your diet/i, /suits you/i, /recommended for/i,
  /based on your/i, /you should/i,
];
//: Shopper observations are a different layer and never appear in this card.
const FORBIDDEN_COMMUNITY = [
  /shopper/i, /reported/i, /reviews?/i, /\bstars?\b/i, /rating/i, /popular/i, /trending/i,
];

describe('the words the card may never use', () => {
  it.each([
    ['characterisation', FORBIDDEN_WORDS],
    ['commerce', FORBIDDEN_COMMERCE],
    ['personalisation', FORBIDDEN_PERSONAL],
    ['community', FORBIDDEN_COMMUNITY],
  ])('says nothing that reads as %s', (_label, patterns) => {
    render(<BetterOption alternative={available()} onView={jest.fn()} />);
    const text = renderedText();
    for (const pattern of patterns) {
      expect(text).not.toMatch(pattern);
    }
  });

  it('keeps the same promise in the missing state', () => {
    render(<BetterOption alternative={missing} onView={jest.fn()} />);
    const text = renderedText();
    for (const pattern of [...FORBIDDEN_WORDS, ...FORBIDDEN_COMMERCE, ...FORBIDDEN_PERSONAL]) {
      expect(text).not.toMatch(pattern);
    }
  });

  it('never implies the market was searched', () => {
    render(<BetterOption alternative={missing} onView={jest.fn()} />);
    const text = renderedText();
    for (const claim of [
      /no alternatives found/i, /nothing better/i, /this is the best/i,
      /best in category/i, /no better product/i, /nothing beats/i,
    ]) {
      expect(text).not.toMatch(claim);
    }
  });
});

// ---------------------------------------------------------------------------
// The missing state
// ---------------------------------------------------------------------------
describe('when there is not enough information', () => {
  it('says so plainly, in keyed copy, and shows no product', () => {
    render(<BetterOption alternative={missing} onView={jest.fn()} />);
    expect(screen.getByText(S.betterOption.notEnoughInformation)).toBeTruthy();
    expect(S.betterOption.notEnoughInformation).toBe(
      'Not enough information to suggest a comparable alternative yet.',
    );
    // No fabricated candidate, and nothing to press.
    expect(screen.queryByText('Sunfield Oat Porridge')).toBeNull();
    expect(screen.queryByLabelText(/^View /)).toBeNull();
  });

  it('is readable by a screen reader rather than a blank surface', () => {
    render(<BetterOption alternative={missing} onView={jest.fn()} />);
    expect(screen.getByLabelText(S.betterOption.a11y.missing)).toBeTruthy();
    const heading = screen.getByText(S.betterOption.heading);
    expect(heading.props.accessibilityRole).toBe('header');
  });

  it('renders nothing at all when the response predates the feature', () => {
    const empty = render(<BetterOption alternative={null} onView={jest.fn()} />);
    expect(empty.toJSON()).toBeNull();
    const absent = render(<BetterOption alternative={undefined} onView={jest.fn()} />);
    expect(absent.toJSON()).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Accessibility
// ---------------------------------------------------------------------------
describe('accessibility', () => {
  it('carries a semantic heading', () => {
    render(<BetterOption alternative={available()} onView={jest.fn()} />);
    expect(screen.getByText(S.betterOption.heading).props.accessibilityRole).toBe('header');
  });

  it('reads the name and the grade comparison as one label', () => {
    render(<BetterOption alternative={available()} onView={jest.fn()} />);
    const label = screen.getByLabelText(
      'Sunfield Oat Porridge. Grade B instead of Grade C. Same category.',
    );
    expect(label).toBeTruthy();
  });

  it('gives the action a meaningful name rather than "tap here"', () => {
    render(<BetterOption alternative={available()} onView={jest.fn()} />);
    const action = screen.getByLabelText('View Sunfield Oat Porridge');
    expect(action.props.accessibilityRole).toBe('button');
  });

  it('never conveys the comparison by colour alone', () => {
    render(<BetterOption alternative={available()} onView={jest.fn()} />);
    // Both letters are in the text, so the meaning survives without colour.
    expect(renderedText()).toContain('Grade B instead of Grade C');
  });
});

// ---------------------------------------------------------------------------
// The wire mapping
// ---------------------------------------------------------------------------
const wire = (alternative: unknown) => ({
  outcome: 'graded', grade: 'C', band: 'yellow', product_name: 'Northstar Corn Flakes',
  taxonomy: { domain: 'consumed', category: 'packaged_food', subcategory: 'cereal' },
  decision: { action: 'wait', reason_key: 'processing' },
  nutrition: { total_sugar_g: 8, salt_g: 0.5, total_fat_g: null, protein_g: 7 },
  components: [], ingredients: [], negatives: [], positives: [],
  quantity_guidance: null, purity_note: null, missing: [],
  confidence: { level: 'unverified', text: 'Unverified' },
  attribution: { text: ODBL_ATTRIBUTION_TEXT }, pack_size_g: 200, basis: 'solid',
  result_contract_version: 'v1', alternative,
}) as never;

describe('the wire mapping', () => {
  it('carries the server decision through without re-deriving it', () => {
    const source = toVerdictSource(wire({
      policy_version: 'comparable-food-alternative-v1',
      status: 'available',
      reason_key: 'comparable_option_found',
      candidate: {
        barcode: '8901000000002', product_name: 'Sunfield Oat Porridge', brand: 'Sunfield',
        grade: 'B', band: 'green', decision: 'buy',
        comparison: {
          category_match: 'exact_source_leaf', category_source: 'open_food_facts',
          current_grade: 'C', candidate_grade: 'B', basis: 'per_100g',
        },
        attribution: { text: ODBL_ATTRIBUTION_TEXT },
      },
    }));
    expect(source.comparableAlternative).toEqual({
      policyVersion: 'comparable-food-alternative-v1',
      status: 'available',
      reasonKey: 'comparable_option_found',
      candidate: candidate(),
    });
    // The legacy price-carrying field is untouched and stays empty.
    expect(source.alternative).toBeNull();
  });

  it('maps the missing state without inventing a candidate', () => {
    const source = toVerdictSource(wire({
      policy_version: 'comparable-food-alternative-v1',
      status: 'not_enough_information',
      reason_key: 'no_comparable_candidate_in_cached_data',
      candidate: null,
    }));
    expect(source.comparableAlternative?.status).toBe('not_enough_information');
    expect(source.comparableAlternative?.candidate).toBeNull();
  });

  it('is absent, not empty, on a response that carries no envelope', () => {
    expect(toVerdictSource(wire(undefined)).comparableAlternative).toBeNull();
  });
});
