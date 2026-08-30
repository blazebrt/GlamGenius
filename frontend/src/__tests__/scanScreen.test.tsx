/**
 * What a person sees after a scan.
 *
 * The rule under test is the one that cannot be relaxed: nothing is shown
 * without a confidence level, and Open Food Facts data never appears without
 * their attribution beside it.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react-native';

import {
  ConfidenceBadge,
  FssaiLine,
  LabelReview,
  NotFoundResult,
  OfflineNote,
  ProductResult,
} from '../components/scan/ScanPieces';
import { ODBL_ATTRIBUTION_TEXT } from '../components/common/OpenFoodFactsAttribution';
import type { ScanResult } from '../services/productScan';

const known: ScanResult = {
  barcode: '8901058000191',
  found: true,
  outcome: 'found_off',
  confidence: { level: 'unverified', text: 'From one source, not checked yet.' },
  open_food_facts: {
    product_name: 'Maggi Masala Noodles',
    brands: 'Nestlé',
    ingredients_text: 'Wheat flour, palm oil, salt',
    quantity: '70 g',
  },
  glamgenius: { confidence: 'unverified', fssai_licence: '10012345678901' },
  can_capture_label: false,
};

const unknown: ScanResult = {
  barcode: '8909999999999',
  found: false,
  outcome: 'not_found',
  confidence: { level: 'not_enough_information', text: 'Not enough information about this one yet.' },
  message: 'We do not know this one yet. Take a photo of the label and we will read it.',
  can_capture_label: true,
};

describe('a known product', () => {
  it('shows the product with its confidence level', () => {
    render(<ProductResult result={known} onCaptureLabel={jest.fn()} onScanAgain={jest.fn()} />);
    expect(screen.getByText('Maggi Masala Noodles')).toBeTruthy();
    expect(screen.getByText('Nestlé')).toBeTruthy();
    expect(screen.getByText('Unverified')).toBeTruthy();
    expect(screen.getByText('From one source, not checked yet.')).toBeTruthy();
  });

  it('shows the Open Food Facts attribution alongside their data', () => {
    render(<ProductResult result={known} onCaptureLabel={jest.fn()} onScanAgain={jest.fn()} />);
    expect(screen.getByText(ODBL_ATTRIBUTION_TEXT)).toBeTruthy();
  });

  it('states the FSSAI licence as a fact about the pack', () => {
    render(<ProductResult result={known} onCaptureLabel={jest.fn()} onScanAgain={jest.fn()} />);
    expect(screen.getByText('FSSAI licence')).toBeTruthy();
    expect(screen.getByText('10012345678901')).toBeTruthy();
  });

  it('offers the label when the record is incomplete', () => {
    const capture = jest.fn();
    render(
      <ProductResult
        result={{ ...known, can_capture_label: true }}
        onCaptureLabel={capture}
        onScanAgain={jest.fn()}
      />,
    );
    fireEvent.press(screen.getByLabelText('Photograph the label'));
    expect(capture).toHaveBeenCalled();
  });
});

describe('an unknown product', () => {
  it('says so plainly and offers the label photo', () => {
    const capture = jest.fn();
    render(<NotFoundResult result={unknown} onCaptureLabel={capture} onScanAgain={jest.fn()} />);
    expect(screen.getByText('We do not know this one yet')).toBeTruthy();
    expect(screen.getByText('Not enough information')).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Photograph the label'));
    expect(capture).toHaveBeenCalled();
  });

  it('carries a confidence level even with nothing to show', () => {
    render(<NotFoundResult result={unknown} onCaptureLabel={jest.fn()} onScanAgain={jest.fn()} />);
    expect(screen.getByLabelText('Confidence: Not enough information')).toBeTruthy();
  });
});

describe('offline', () => {
  it('tells the person their scans are saved rather than lost', () => {
    render(<OfflineNote queued={3} />);
    expect(screen.getByText(/3 scans saved/i)).toBeTruthy();
  });

  it('says scanning still works with nothing queued', () => {
    render(<OfflineNote queued={0} />);
    expect(screen.getByText(/Scanning still works/i)).toBeTruthy();
  });
});

describe('the label review', () => {
  const facts = {
    product_name: 'Masala Oats',
    brand: 'Test Brand',
    ingredients_text: 'Oats, salt, spices',
    nutrition_per_100g: { energy_kcal: '384', sugars_g: '3.4' },
    fssai_licence: '10012345678901',
    uncertain_fields: ['serving_size'],
  };

  it('reads the label back before anything is saved', () => {
    render(<LabelReview facts={facts} onConfirm={jest.fn()} onRetake={jest.fn()} />);
    expect(screen.getByText('This is what the label says')).toBeTruthy();
    expect(screen.getByText(/Nothing is saved until you confirm it/i)).toBeTruthy();
    expect(screen.getByText('Masala Oats')).toBeTruthy();
    expect(screen.getByText('384')).toBeTruthy();
  });

  it('names what it could not read instead of guessing', () => {
    render(<LabelReview facts={facts} onConfirm={jest.fn()} onRetake={jest.fn()} />);
    expect(screen.getByText(/Could not read clearly: serving_size/i)).toBeTruthy();
  });

  it('takes the confirmation as the one tap that makes it count', () => {
    const confirm = jest.fn();
    render(<LabelReview facts={facts} onConfirm={confirm} onRetake={jest.fn()} />);
    fireEvent.press(screen.getByLabelText('Confirm this label'));
    expect(confirm).toHaveBeenCalled();
  });

  it('never judges the food, only transcribes it', () => {
    render(<LabelReview facts={facts} onConfirm={jest.fn()} onRetake={jest.fn()} />);
    const shown = screen.toJSON();
    const text = JSON.stringify(shown).toLowerCase();
    for (const word of ['healthy', 'unhealthy', 'junk', 'bad for you', 'avoid this']) {
      expect(text).not.toContain(word);
    }
  });
});

describe('every confidence level', () => {
  it.each([
    ['verified', 'Verified'],
    ['community', 'Community checked'],
    ['unverified', 'Unverified'],
    ['not_enough_information', 'Not enough information'],
  ])('renders %s with words a person can read', (level, label) => {
    render(<ConfidenceBadge confidence={{ level: level as never, text: 'Explained here.' }} />);
    expect(screen.getByText(label)).toBeTruthy();
    expect(screen.getByText('Explained here.')).toBeTruthy();
  });
});

describe('the FSSAI line', () => {
  it('shows the number and nothing inferred from it', () => {
    render(<FssaiLine licence="10012345678901" />);
    expect(screen.getByText('10012345678901')).toBeTruthy();
    expect(screen.queryByText(/approved|safe|certified/i)).toBeNull();
  });
});
