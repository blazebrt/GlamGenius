/**
 * Step 13 — the supplement detail on the phone.
 *
 * The surface reports what a label says as it was recorded and who recorded
 * it. These tests hold it to that: provenance is always visible and never
 * reads as scanned or official; unconfirmed details look unconfirmed; printed
 * units and serving text are shown exactly as printed; overlap is a fact about
 * names, not a warning; research appears only when published, always with an
 * open source link; missing information reads as intentional; and the
 * professional boundary is shown when the person's own note calls for it.
 */
import React from 'react';
import * as fs from 'fs';
import * as path from 'path';
import { Linking, StyleSheet } from 'react-native';
import { fireEvent, render, screen } from '@testing-library/react-native';

import { SupplementDetailSection } from '../components/inventory/SupplementDetail';
import type { SupplementDetail, SupplementDetailComponent } from '../services/apiV2';
import { S, factProvenance } from '../strings/supplements';

const component = (overrides: Partial<SupplementDetailComponent> = {}): SupplementDetailComponent => ({
  id: 'fact-1',
  printed: { name: 'Magnesium oxide', amount: '500', unit: 'mg', serving_text: '2 capsules daily' },
  provenance: 'you_entered',
  confirmed: true,
  counts_for_overlap: true,
  missing_information: [],
  nutrient: { status: 'identified', key: 'magnesium', display_name: 'Magnesium' },
  form: { status: 'exact', name: 'magnesium oxide' },
  package_chemistry: {
    status: 'calculated', element: 'magnesium', element_symbol: 'Mg', percent_by_weight: '60.3',
    formula: 'MgO', hydration: null, withheld_reason: null,
  },
  published_knowledge: { status: 'not_enough_information' },
  ...overrides,
});

const detail = (overrides: Partial<SupplementDetail> = {}): SupplementDetail => ({
  contract_version: 'step-13-v1',
  item: {
    inventory_item_id: 'item-1', display_name: 'Night magnesium', brand: 'Brand', user_entered_purpose: null,
    provenance: 'you_entered', confirmed: true,
  },
  expiry: { state: 'current', date: '2027-12-01', days_to_expiry: 400 },
  components: [component()],
  overlaps: [],
  missing_information: [],
  professional_boundary: { boundary: false, reason: null, message: null },
  fingerprint: 'f',
  ...overrides,
});

const PUBLISHED = {
  status: 'published' as const,
  summary: 'Poorly absorbed compared with soluble magnesium salts.',
  value: 'about 4',
  unit: '% of the dose absorbed',
  disagreement: 'Other work finds the gap smaller.',
  evidence_strength: 'moderate',
  source: { name: 'Firoz M, Graber M. Magnesium Research 2001', publisher: 'Magnesium Research', url: 'https://pubmed.ncbi.nlm.nih.gov/11794633/' },
};

/** Words the app may never say in its own voice on this surface. */
const OWN_VOICE_BANNED = [
  /you should/i, /\btake \d/i, /\byour dos/i, /\bdosage\b/i, /daily intake/i, /\btotal\b/i, /too much/i,
  /\bexceed/i, /\bunsafe\b/i, /\btoxic\b/i, /\bspoiled\b/i, /\bineffective\b/i, /\bdeficien/i,
  /\brecommend/i, /\bbest\b/i, /\bbetter\b/i, /\bverified\b/i, /\bofficial\b/i, /\bscanned\b/i,
  /\bwarning\b/i, /\bdanger/i,
];

const allText = (): string => screen.toJSON() ? JSON.stringify(screen.toJSON()) : '';

describe('Step 13 supplement detail', () => {
  it('always shows who recorded a detail, and manual entry never reads as scanned or official', () => {
    render(<SupplementDetailSection detail={detail({ components: [
      component(),
      component({ id: 'fact-2', printed: { name: 'Zinc oxide', amount: null, unit: null, serving_text: null }, provenance: 'read_from_photo_confirmed_by_you' }),
    ] })} />);
    expect(screen.getByText(S.provenance.you_entered)).toBeTruthy();
    expect(screen.getByText(S.provenance.read_from_photo_confirmed_by_you)).toBeTruthy();
    const text = allText();
    for (const word of [/scanned/i, /official/i, /manufacturer/i, /regulator/i, /verified/i]) {
      expect(text).not.toMatch(word);
    }
  });

  it('makes an unconfirmed detail look unconfirmed and offers confirmation', () => {
    const onConfirm = jest.fn();
    render(<SupplementDetailSection
      detail={detail({
        components: [component({
          provenance: 'read_from_photo_not_confirmed', confirmed: false, counts_for_overlap: false,
          nutrient: { status: 'awaiting_confirmation' }, form: { status: 'awaiting_confirmation' },
          package_chemistry: { status: 'awaiting_confirmation' }, published_knowledge: { status: 'awaiting_confirmation' },
        })],
        missing_information: ['confirmation'],
      })}
      onConfirm={onConfirm}
    />);
    expect(screen.getByText(S.provenance.read_from_photo_not_confirmed)).toBeTruthy();
    expect(screen.getByText(S.awaitingConfirmation)).toBeTruthy();
    // Nothing derived from an unconfirmed detail is shown.
    expect(screen.queryByTestId('supplement-chemistry')).toBeNull();
    expect(screen.queryByTestId('supplement-knowledge')).toBeNull();
    fireEvent.press(screen.getByRole('button', { name: S.a11y.confirm('Magnesium oxide') }));
    expect(onConfirm).toHaveBeenCalledWith('fact-1');
    expect(screen.getByText(`${S.missingLabel}: ${S.missing.confirmation}`)).toBeTruthy();
  });

  it('keeps printed units exactly as recorded, with no text transform', () => {
    render(<SupplementDetailSection detail={detail({ components: [
      component({ id: 'a', printed: { name: 'Vitamin B12', amount: '500', unit: 'µg', serving_text: null } }),
      component({ id: 'b', printed: { name: 'Vitamin D3', amount: '1000', unit: 'IU', serving_text: null } }),
      component({ id: 'c', printed: { name: 'Selenium', amount: '55', unit: 'mcg', serving_text: null } }),
    ] })} />);
    for (const value of ['500 µg', '1000 IU', '55 mcg']) {
      const node = screen.getByText(`${S.printedAmount}: ${value}`);
      const style = StyleSheet.flatten(node.props.style) ?? {};
      expect(style.textTransform).toBeUndefined();
    }
    expect(allText()).not.toMatch(/Mcg|MCG|Iu\b/);
  });

  it('frames serving text as printed label text, never as an instruction', () => {
    render(<SupplementDetailSection detail={detail()} />);
    expect(screen.getByText(`${S.printedServingText}: “2 capsules daily”`)).toBeTruthy();
    expect(allText()).not.toMatch(/Take 2|take 2 capsules|you should take|your dose/i);
  });

  it('shows package chemistry as chemistry, never as an amount in the body', () => {
    render(<SupplementDetailSection detail={detail()} />);
    expect(screen.getByText(`${S.chemistry.label}: 60.3% of this compound’s weight is magnesium.`)).toBeTruthy();
    expect(screen.getByText(S.chemistry.basis)).toBeTruthy();
    // 500 mg x 60.3% is never shown.
    expect(allText()).not.toMatch(/301/);
  });

  it('states why chemistry is withheld when the hydrated form is not printed', () => {
    render(<SupplementDetailSection detail={detail({ components: [component({
      printed: { name: 'Ferrous sulphate', amount: '200', unit: 'mg', serving_text: null },
      form: { status: 'exact', name: 'ferrous sulfate' },
      package_chemistry: {
        status: 'withheld', element: null, element_symbol: null, percent_by_weight: null, formula: null,
        hydration: null, withheld_reason: 'hydration_not_stated',
      },
    })] })} />);
    expect(screen.getByText(`${S.chemistry.label}: ${S.chemistry.withheld.hydration_not_stated}`)).toBeTruthy();
    expect(allText()).not.toMatch(/36\.8|20\.1/);
  });

  it('says the form is not stated rather than guessing one', () => {
    render(<SupplementDetailSection detail={detail({ components: [component({
      printed: { name: 'Magnesium', amount: '300', unit: 'mg', serving_text: null },
      form: { status: 'not_stated', name: null },
      package_chemistry: {
        status: 'withheld', element: null, element_symbol: null, percent_by_weight: null, formula: null,
        hydration: null, withheld_reason: 'form_not_stated',
      },
    })] })} />);
    expect(screen.getByText(`${S.form.label}: ${S.form.notStated}`)).toBeTruthy();
    expect(screen.queryByText(/oxide|citrate|glycinate/i)).toBeNull();
    expect(screen.queryByTestId('supplement-knowledge')).toBeNull();
  });

  it('shows research only when published, always with a source that opens', () => {
    const openURL = jest.spyOn(Linking, 'openURL').mockResolvedValue(true);
    const { rerender } = render(<SupplementDetailSection detail={detail()} />);
    expect(screen.getByText(`${S.knowledge.label}: ${S.knowledge.none}`)).toBeTruthy();
    expect(screen.queryByRole('link')).toBeNull();

    rerender(<SupplementDetailSection detail={detail({ components: [component({ published_knowledge: PUBLISHED })] })} />);
    expect(screen.getByText(PUBLISHED.summary)).toBeTruthy();
    expect(screen.getByText(`${S.knowledge.sourcesDiffer}: ${PUBLISHED.disagreement}`)).toBeTruthy();
    expect(screen.getByText(`${PUBLISHED.source.name} · ${PUBLISHED.source.publisher}`)).toBeTruthy();
    const link = screen.getByRole('link', { name: S.a11y.openSource(PUBLISHED.source.name) });
    fireEvent.press(link);
    expect(openURL).toHaveBeenCalledWith(PUBLISHED.source.url);
    openURL.mockRestore();
  });

  it('states overlap as a fact about names, never a warning or a sum', () => {
    render(<SupplementDetailSection detail={detail({ overlaps: [{
      component_key: 'vitamin c', nutrient_display_name: 'Vitamin C', printed_names_here: ['Ascorbic acid', 'Vitamin C'],
      other_products: [{ inventory_item_id: 'item-2', product_name: 'Travel C', printed_names: ['Vitamin C'] }],
      product_count: 2,
    }] })} />);
    expect(screen.getByText(S.overlap.heading)).toBeTruthy();
    expect(screen.getByText('Listed on 2 products you recorded.')).toBeTruthy();
    expect(screen.getByText('Travel C: Vitamin C')).toBeTruthy();
    expect(screen.getByText(S.overlap.note)).toBeTruthy();
    expect(allText()).not.toMatch(/too much|unsafe|stop one|limit|warning|total/i);
  });

  it('renders missing information and expiry as calm, intentional statements', () => {
    render(<SupplementDetailSection detail={detail({
      expiry: { state: 'unknown', date: null, days_to_expiry: null },
      components: [component({ printed: { name: 'Zinc', amount: null, unit: null, serving_text: null }, missing_information: ['amount', 'unit', 'serving_text'] })],
      missing_information: ['expiry_date'],
    })} />);
    expect(screen.getByText(S.expiry.unknown)).toBeTruthy();
    expect(screen.getByText(S.amountNotAdded, { exact: false })).toBeTruthy();
    expect(screen.getByText(`${S.missingLabel}: printed amount, unit, serving text`)).toBeTruthy();
    expect(screen.getByText(`${S.missingLabel}: expiry date`)).toBeTruthy();
    for (const state of ['past', 'coming_up'] as const) {
      expect(S.expiry[state]).not.toMatch(/unsafe|toxic|spoiled|ineffective|throw|dispose|replace/i);
    }
  });

  it('shows the professional boundary when the person’s own note calls for it', () => {
    const message = 'It sounds like this is something a doctor is already looking after. That is outside what GlamGenius can help with — please talk to them about it.';
    render(<SupplementDetailSection detail={detail({ professional_boundary: { boundary: true, reason: 'hard_handoff:clinical_condition', message } })} />);
    expect(screen.getByLabelText('This one needs a professional')).toBeTruthy();
    expect(screen.getByText(message)).toBeTruthy();
  });

  it('never speaks in the app’s own voice about dose, safety, need or preference', () => {
    render(<SupplementDetailSection detail={detail({ components: [component({ published_knowledge: PUBLISHED })] })} />);
    // Published research is a reviewer's quoted text; everything else is ours.
    const own = allText().replace(PUBLISHED.summary, '').replace(PUBLISHED.disagreement, '').replace(PUBLISHED.unit, '');
    for (const pattern of OWN_VOICE_BANNED) {
      expect(own).not.toMatch(pattern);
    }
  });

  it('keeps every supplement string in the keyed file free of advice', () => {
    const file = fs.readFileSync(path.join(__dirname, '..', 'strings', 'supplements.ts'), 'utf8');
    // The values only: the header comment lists the banned words on purpose.
    const strings = file.slice(file.indexOf('export const S = {'));
    const values = [...strings.matchAll(/'([^']{8,})'/g)].map((match) => match[1]);
    expect(values.length).toBeGreaterThan(20);
    for (const value of values) {
      for (const pattern of OWN_VOICE_BANNED) {
        expect({ value, match: pattern.test(value) }).toEqual({ value, match: false });
      }
    }
  });

  it('maps provenance exactly as the server does', () => {
    expect(factProvenance('user_declared', 'confirmed')).toBe('you_entered');
    expect(factProvenance('user_declared', 'draft')).toBe('you_entered_not_confirmed');
    expect(factProvenance('photo_extracted', 'confirmed')).toBe('read_from_photo_confirmed_by_you');
    expect(factProvenance('photo_extracted', 'draft')).toBe('read_from_photo_not_confirmed');
    expect(factProvenance('open_food_facts', 'confirmed')).toBe('unknown_source');
  });

  it('is wired into the owned item screen, and the old unqualified "Confirmed from label" is gone', () => {
    const screenSource = fs.readFileSync(path.join(__dirname, '..', '..', 'app', 'inventory-item.tsx'), 'utf8');
    expect(screenSource).toContain('SupplementDetailSection');
    expect(screenSource).toContain('getSupplementDetail');
    expect(screenSource).not.toContain('Confirmed from label');
    expect(screenSource).toContain('factProvenance(fact.source, fact.verification_state)');
  });
});
