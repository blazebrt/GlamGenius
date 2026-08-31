/**
 * ODbL requires the attribution to be visible wherever the data is used.
 * The wording is a licence condition, so it is pinned exactly.
 */
import * as fs from 'fs';
import * as path from 'path';

import React from 'react';
import { render, screen } from '@testing-library/react-native';

import {
  ODBL_ATTRIBUTION_TEXT, ODBL_LICENSE_URL, OFF_SOURCE_URL, OpenFoodFactsAttribution,
} from '../components/common/OpenFoodFactsAttribution';

describe('Open Food Facts attribution', () => {
  it('renders the exact wording the licence requires', () => {
    render(<OpenFoodFactsAttribution />);
    expect(screen.getByText(ODBL_ATTRIBUTION_TEXT)).toBeTruthy();
  });

  it('uses the wording verbatim, not a paraphrase', () => {
    expect(ODBL_ATTRIBUTION_TEXT).toBe(
      'Contains information from Open Food Facts, made available under the Open Database License (ODbL)',
    );
  });

  it('links to both the source and the licence', () => {
    render(<OpenFoodFactsAttribution />);
    expect(screen.getByLabelText('Open Food Facts')).toBeTruthy();
    expect(screen.getByLabelText('Open Database License')).toBeTruthy();
    expect(ODBL_LICENSE_URL).toContain('opendatacommons.org');
    expect(OFF_SOURCE_URL).toContain('openfoodfacts.org');
  });

  it('is reachable to a screen reader as a labelled block', () => {
    render(<OpenFoodFactsAttribution />);
    expect(screen.getByLabelText('Open Food Facts attribution')).toBeTruthy();
  });
});

/**
 * Every surface, not just the first one built. The verdict screen is the main
 * place a person reads a product name, its ingredients and its nutrition, and
 * it was rendering all three with no attribution at all.
 */
describe('the surfaces that show their data', () => {
  const SOURCES = [
    'src/components/scan/ScanPieces.tsx',
    'app/verdict.tsx',
  ];

  it.each(SOURCES)('%s renders the attribution', (file) => {
    const text = fs.readFileSync(path.join(__dirname, '../..', file), 'utf8');
    expect(text).toContain('OpenFoodFactsAttribution');
  });

  it('keeps the attribution on the verdict source so a screen can render it', () => {
    const client = fs.readFileSync(
      path.join(__dirname, '../services/verdictClient.ts'), 'utf8',
    );
    // Dropping it in the conversion is how it went missing the first time.
    expect(client).toContain('attribution:');
  });
});
