/**
 * Reviewing a shelf photo.
 *
 * The rule that cannot bend: nothing is on the shelf until it is tapped. The
 * review screen has to say so, and each row has to offer both answers with
 * equal weight — a screen that makes "keep" easy and "drop" hard is a screen
 * that fills the inventory with guesses.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react-native';

import {
  CandidateRow,
  CaptureDone,
  CaptureProgress,
  EmptyCapture,
} from '../components/inventory/ShelfCapturePieces';
import type { ImportCandidate } from '../services/apiV2';

const candidate: ImportCandidate = {
  id: 'c1',
  position: 0,
  category: 'beauty',
  subcategory: null,
  display_name: 'Minimalist Niacinamide 10%',
  brand: 'Minimalist',
  confidence: 0.72,
  details: { product_type: 'serum', size: '30 ml' },
  attributes: [],
  uncertain_fields: [],
  photo_quality_notes: 'Label readable.',
  state: 'pending',
  item_id: null,
  decided_at: null,
};

describe('the review list', () => {
  it('says nothing is on the shelf yet', () => {
    render(<CaptureProgress decided={0} total={15} unreadable={null} />);
    expect(screen.getByText('NOTHING HERE IS ON YOUR SHELF YET')).toBeTruthy();
    expect(screen.getByText('0 of 15 reviewed')).toBeTruthy();
  });

  it('counts what it could not read rather than hiding it', () => {
    render(<CaptureProgress decided={3} total={8} unreadable={4} />);
    expect(screen.getByText(/4 more things were visible but not readable/i)).toBeTruthy();
  });

  it('says nothing about unreadable things when there were none', () => {
    render(<CaptureProgress decided={3} total={8} unreadable={0} />);
    expect(screen.queryByText(/not readable/i)).toBeNull();
  });
});

describe('one candidate', () => {
  it('shows what the photo suggested', () => {
    render(<CandidateRow candidate={candidate} onKeep={jest.fn()} onDrop={jest.fn()} />);
    expect(screen.getByText('Minimalist Niacinamide 10%')).toBeTruthy();
    expect(screen.getByText(/Minimalist · Skin Care/)).toBeTruthy();
  });

  it('offers keep and drop as one tap each', () => {
    const keep = jest.fn();
    const drop = jest.fn();
    render(<CandidateRow candidate={candidate} onKeep={keep} onDrop={drop} />);

    fireEvent.press(screen.getByLabelText('Keep Minimalist Niacinamide 10%'));
    expect(keep).toHaveBeenCalledTimes(1);

    fireEvent.press(screen.getByLabelText('Drop Minimalist Niacinamide 10%'));
    expect(drop).toHaveBeenCalledTimes(1);
  });

  it('names what it could not read instead of guessing', () => {
    render(
      <CandidateRow
        candidate={{ ...candidate, uncertain_fields: ['size', 'expiry_date'] }}
        onKeep={jest.fn()}
        onDrop={jest.fn()}
      />,
    );
    expect(screen.getByText(/Could not read: size, expiry_date/)).toBeTruthy();
  });

  it('never judges the product, only names it', () => {
    render(<CandidateRow candidate={candidate} onKeep={jest.fn()} onDrop={jest.fn()} />);
    const text = JSON.stringify(screen.toJSON()).toLowerCase();
    for (const word of ['recommend', 'good for', 'bad for', 'you should', 'better than']) {
      expect(text).not.toContain(word);
    }
  });
});

describe('finishing', () => {
  it('says what was kept and that dropped ones saved nothing', () => {
    render(
      <CaptureDone kept={12} dropped={3} onScanAnother={jest.fn()} onOpenInventory={jest.fn()} />,
    );
    expect(screen.getByText('12 items are on your shelf')).toBeTruthy();
    expect(screen.getByText(/3 suggestions were dropped and saved nothing/i)).toBeTruthy();
  });

  it('offers another shelf, because a shelf is rarely one photo', () => {
    const again = jest.fn();
    render(<CaptureDone kept={5} dropped={0} onScanAnother={again} onOpenInventory={jest.fn()} />);
    fireEvent.press(screen.getByLabelText('Photograph another shelf'));
    expect(again).toHaveBeenCalled();
  });

  it('says plainly when a photo yielded nothing', () => {
    render(<EmptyCapture onRetake={jest.fn()} />);
    expect(screen.getByText(/could not read anything on that shelf/i)).toBeTruthy();
    expect(screen.getByText(/more light/i)).toBeTruthy();
  });
});
