/**
 * The three things an ingredient row must let a person do.
 *
 * Opening the authority, asking us to explain ourselves, and telling us we are
 * wrong are three different requests. Collapsing any two of them into one
 * control means a person who disagrees with a flag has no way to say so that
 * is distinguishable from wanting to read more about it.
 */
import React from 'react';
import { Linking } from 'react-native';
import { render, screen, fireEvent } from '@testing-library/react-native';

import { IngredientDetail, IngredientList } from '../components/verdict/VerdictPieces';
import { S, t } from '../strings/verdict';
import type { VerdictIngredient } from '../services/verdictModel';

const flagged: VerdictIngredient = {
  name: 'Antioxidant (INS 319)',
  label: 'Tertiary butylhydroquinone (TBHQ) · INS 319',
  tier: 'red',
  tierLabel: S.ingredients.tierRed,
  status: 'flagged',
  description: 'Stops fat going rancid.',
  whyFlagged: null,
  sources: [{
    name: 'FSSAI. Food Safety and Standards (Food Products Standards and Food Additives) Regulations, 2011, as amended.',
    url: 'https://www.fssai.gov.in/cms/food-safety-and-standards-regulations.php',
    publisher: 'FSSAI',
    version: 'FSSAI-ADDITIVES-2011',
  }],
  detail: {
    whatItDoes: 'Stops fat going rancid.',
    whyFlagged: 'Usage is regulated; this rule flags it.',
    rule: 'grade.step3.red_tier',
    authorityPosition: 'FSSAI Food Additives Regulations, 2011.',
    interpretation: 'Ceiling D.',
    evidenceStatus: 'low',
    source: {
      name: 'FSSAI Food Additives Regulations, 2011',
      url: 'https://www.fssai.gov.in/cms/food-safety-and-standards-regulations.php',
      publisher: 'FSSAI',
      version: 'FSSAI-ADDITIVES-2011',
    },
  },
};

describe('a flagged ingredient row', () => {
  it('offers source, explain and report as three separate controls', () => {
    const onReport = jest.fn();
    const onExplain = jest.fn();
    const openURL = jest.spyOn(Linking, 'openURL').mockResolvedValue(true as never);

    render(<IngredientList ingredients={[flagged]} onReport={onReport} onExplain={onExplain} />);

    const label = flagged.label as string;
    const sourceControl = screen.getByLabelText(t(S.a11y.openSource, { name: label }));
    const explainControl = screen.getByLabelText(t(S.a11y.explain, { name: label }));
    const reportControl = screen.getByLabelText(t(S.a11y.report, { subject: label }));

    // Three distinct elements, not one element found three ways.
    expect(sourceControl).not.toBe(explainControl);
    expect(explainControl).not.toBe(reportControl);
    expect(sourceControl).not.toBe(reportControl);

    fireEvent.press(sourceControl);
    fireEvent.press(explainControl);
    fireEvent.press(reportControl);

    // Each control calls its own handler, and only its own.
    expect(openURL).toHaveBeenCalledTimes(1);
    expect(onExplain).toHaveBeenCalledTimes(1);
    expect(onExplain).toHaveBeenCalledWith(flagged);
    expect(onReport).toHaveBeenCalledTimes(1);
    expect(onReport).toHaveBeenCalledWith(label);
    openURL.mockRestore();
  });

  it('is understandable without opening anything', () => {
    render(<IngredientList ingredients={[flagged]} onReport={jest.fn()} onExplain={jest.fn()} />);
    // The name, what it is, and what it does — all on the row itself.
    expect(screen.getByText(flagged.label as string)).toBeTruthy();
    expect(screen.getByText(S.ingredients.flagged)).toBeTruthy();
    expect(screen.getByText('Stops fat going rancid.')).toBeTruthy();
  });
});

describe('the deeper explanation behind the ? control', () => {
  it('carries every section a person needs to check our reasoning', () => {
    render(<IngredientDetail ingredient={flagged} />);
    for (const lead of [
      S.ingredients.whatItDoes,
      S.ingredients.whyFlagged,
      S.ingredients.exactRule,
      S.ingredients.authorityPosition,
      S.ingredients.ourInterpretation,
      S.ingredients.evidenceStatus,
    ]) {
      expect(screen.getByText(lead)).toBeTruthy();
    }
    // The exact rule, so an error report can name the decision basis.
    expect(screen.getByText('grade.step3.red_tier')).toBeTruthy();
    expect(screen.getByText(S.ingredients.openSource)).toBeTruthy();
  });

  it('says what is missing rather than filling a gap with a guess', () => {
    render(<IngredientDetail ingredient={{ ...flagged, detail: null, sources: [] }} />);
    // Each gap says what is missing in its own terms, so the reader can tell
    // "no rule fired" apart from "the rule fired and we added nothing".
    expect(screen.getByText(S.ingredients.noRule)).toBeTruthy();
    expect(screen.getByText(S.ingredients.noInterpretation)).toBeTruthy();
    expect(screen.getByText(S.ingredients.noNote)).toBeTruthy();
    expect(screen.getByText(S.ingredients.noAuthority)).toBeTruthy();
  });
});
