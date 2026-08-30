/**
 * ODbL requires the attribution to be visible wherever the data is used.
 * The wording is a licence condition, so it is pinned exactly.
 */
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
