import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';

import {
  ConfidenceRow, EntitlementNote, FollowUpQuestion, Lookboard, NotEnoughInventory,
  OccasionTile, OptionalPieceRow, OwnedPieceRow, REVISE_REASONS, ReviseReasons,
  StyleProcessing, VARIANT_BLURB, WhyThisWorks, confidenceLabel,
} from '../components/style/StylePieces';
import { Look, LookPiece, OCCASION_KEYS, OccasionDefinition, StyleResult } from '../services/apiV2';

const ownedPiece = (overrides: Partial<LookPiece> = {}): LookPiece => ({
  id: 'piece-1', slot: 'clothing', ownership: 'owned', inventory_item_id: 'inv-1',
  display_name: 'Navy formal shirt', brand: 'Test Brand', category: 'wardrobe', subcategory: 'shirt',
  role_note: 'Matches the formality this occasion calls for.', score: 0.8,
  owned: true, label: 'In your inventory', ...overrides,
});

const optionalPiece = (overrides: Partial<LookPiece> = {}): LookPiece => ({
  id: 'piece-2', slot: 'shoes', ownership: 'optional_addition', inventory_item_id: null,
  display_name: 'formal closed shoes in a dark neutral', brand: null, category: null, subcategory: null,
  role_note: 'You have no confirmed shoes that suit this occasion.', score: 0,
  owned: false, label: 'Optional addition — you do not own this', ...overrides,
});

const look = (overrides: Partial<Look> = {}): Look => {
  const owned = ownedPiece();
  const optional = optionalPiece();
  return {
    id: 'look-1', run_id: 'run-1', variant: 'recommended', title: 'Recommended', rank: 1,
    score: 0.72, confidence: 0.66,
    why_it_works: 'Navy formal shirt is the balanced choice for office. Black leather derby shoes was chosen to go with it.',
    weather_note: 'Suits mild weather.', dress_code_note: 'This sits at the business casual level the occasion calls for.',
    preparation_steps: ['Press the shirt the night before'],
    missing_information: ['Expected weather was not given, so nothing was ruled out on that basis.'],
    factor_scores: {}, explanation_source: 'deterministic', status: 'active', saved: false, version: 1,
    slots: { clothing: [owned], shoes: [optional], accessories: [], perfume: [], hair: [], grooming: [] },
    owned_items: [owned], optional_additions: [optional],
    owned_item_count: 1, optional_addition_count: 1, unavailable_items: [],
    share: {
      title: 'Recommended', includes_personal_data: false,
      text: 'Recommended — 1 pieces from my own wardrobe\n• Navy formal shirt',
      note: 'Shares the names of the pieces only.',
    },
    created_at: null, ...overrides,
  };
};

const occasion: OccasionDefinition = {
  key: 'office', label: 'Office', formality: 4,
  dress_codes: ['business_casual', 'smart_casual'], default_dress_code: 'business_casual',
  default_setting: 'indoor', required_slots: ['clothing', 'shoes'],
  optional_slots: ['accessories', 'perfume', 'hair', 'grooming'],
  questions: [{ key: 'weather', label: 'What is the weather likely to be?', options: ['hot', 'cold'], required: false }],
  notes: 'A normal working day at your workplace.',
};

describe('Style Me UI', () => {
  it('offers all sixteen supported occasions', () => {
    expect(OCCASION_KEYS).toHaveLength(16);
    expect(OCCASION_KEYS).toContain('business_meeting');
    expect(OCCASION_KEYS).toContain('gym');
  });

  it('an occasion can be picked and reads its state to a screen reader', () => {
    const press = jest.fn();
    render(<OccasionTile occasion={occasion} selected={false} onPress={press} />);
    const tile = screen.getByLabelText('Style me for Office');
    expect(tile.props.accessibilityState.selected).toBe(false);
    fireEvent.press(tile);
    expect(press).toHaveBeenCalled();
  });

  it('follow-up questions only render the options the occasion actually needs', () => {
    const change = jest.fn();
    render(<FollowUpQuestion question={occasion.questions[0]} onChange={change} />);
    fireEvent.press(screen.getByLabelText('What is the weather likely to be? hot'));
    expect(change).toHaveBeenCalledWith('hot');

    // A question with no options is not rendered at all.
    const empty = render(
      <FollowUpQuestion question={{ key: 'location', label: 'Where is it?', options: [], required: false }} onChange={change} />
    );
    expect(empty.toJSON()).toBeNull();
  });

  it('shows a processing state while the looks are being built', () => {
    render(<StyleProcessing occasionLabel="Office" />);
    expect(screen.getByLabelText('Building your looks')).toBeTruthy();
    expect(screen.getByText(/only things you have confirmed are used/i)).toBeTruthy();
  });

  it('owned and optional pieces are visibly and semantically different', () => {
    render(<OwnedPieceRow piece={ownedPiece()} />);
    expect(screen.getByLabelText('Navy formal shirt, in your inventory')).toBeTruthy();
    expect(screen.getByText('In your inventory')).toBeTruthy();

    render(<OptionalPieceRow piece={optionalPiece()} />);
    expect(screen.getByLabelText(/optional addition you do not own/i)).toBeTruthy();
    expect(screen.getByText('Optional addition — you do not own this')).toBeTruthy();
  });

  it('a lookboard labels owned versus optional and never claims the optional item is owned', () => {
    render(<Lookboard look={look()} />);
    expect(screen.getByText('1 owned')).toBeTruthy();
    expect(screen.getByText('In your inventory')).toBeTruthy();
    expect(screen.getByText('Optional addition — you do not own this')).toBeTruthy();
    expect(screen.getByText(/is not in your inventory/i)).toBeTruthy();
  });

  it('the three variants are described as different decisions', () => {
    expect(VARIANT_BLURB.recommended).toMatch(/balanced/i);
    expect(VARIANT_BLURB.comfortable).toMatch(/comfort/i);
    expect(VARIANT_BLURB.expressive).toMatch(/statement/i);
  });

  it('save, revise, swap, reject and share are all reachable', () => {
    const onSave = jest.fn(); const onRevise = jest.fn(); const onReject = jest.fn();
    const onShare = jest.fn(); const onSwap = jest.fn();
    render(<Lookboard look={look()} onSave={onSave} onRevise={onRevise} onReject={onReject} onShare={onShare} onSwap={onSwap} />);

    fireEvent.press(screen.getByLabelText('Save Recommended'));
    fireEvent.press(screen.getByLabelText('Revise Recommended'));
    fireEvent.press(screen.getByLabelText('Share Recommended'));
    fireEvent.press(screen.getByLabelText('Reject Recommended'));
    fireEvent.press(screen.getByLabelText('Swap Navy formal shirt'));

    expect(onSave).toHaveBeenCalled();
    expect(onRevise).toHaveBeenCalled();
    expect(onShare).toHaveBeenCalled();
    expect(onReject).toHaveBeenCalled();
    expect(onSwap).toHaveBeenCalledWith(expect.objectContaining({ inventory_item_id: 'inv-1' }));
  });

  it('an optional addition offers no swap control, because there is nothing owned to swap', () => {
    render(<Lookboard look={look()} onSwap={jest.fn()} />);
    expect(screen.queryByLabelText(/^Swap formal closed shoes/)).toBeNull();
  });

  it('"Why this works" shows the reasoning and the preparation steps', () => {
    render(<WhyThisWorks look={look()} />);
    expect(screen.getByLabelText('Why this works')).toBeTruthy();
    expect(screen.getByText(/balanced choice for office/i)).toBeTruthy();
    expect(screen.getByText('• Press the shirt the night before')).toBeTruthy();
  });

  it('confidence is explained in words and missing information is listed', () => {
    expect(confidenceLabel(0.8)).toMatch(/confident/i);
    expect(confidenceLabel(0.3)).toMatch(/low confidence/i);
    render(<ConfidenceRow look={look()} />);
    expect(screen.getByText(/expected weather was not given/i)).toBeTruthy();
    expect(screen.getByText(/written from your own recorded details/i)).toBeTruthy();
  });

  it('revision offers concrete reasons rather than a free-text box', () => {
    const pick = jest.fn();
    render(<ReviseReasons onPick={pick} />);
    expect(REVISE_REASONS.length).toBeGreaterThanOrEqual(6);
    fireEvent.press(screen.getByLabelText('Too formal'));
    expect(pick).toHaveBeenCalledWith('too_formal');
  });

  it('an empty inventory is explained, not shown as a failure', () => {
    const result: StyleResult = {
      status: 'not_enough_inventory', run_id: 'run-1',
      occasion: {} as any, looks: [],
      message: 'There is not enough confirmed inventory to build a look for this yet. We will not invent clothes you do not own.',
      guidance: ['Add a few things you actually wear.'],
      confirmed_item_count: 0, unconfirmed_draft_count: 0, missing_information: [],
      entitlement: { feature: 'style_occasion', period: '2026-08', included: 60, used: 0, remaining: 60, source: 'beta_grant' },
    };
    const add = jest.fn();
    render(<NotEnoughInventory result={result} onAdd={add} />);
    expect(screen.getByText(/will not invent clothes you do not own/i)).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Add items to inventory'));
    expect(add).toHaveBeenCalled();
  });

  it('the shareable text carries garment names but no identifiers', () => {
    const board = look();
    expect(board.share.includes_personal_data).toBe(false);
    expect(board.share.text).toContain('Navy formal shirt');
    expect(board.share.text).not.toContain('inv-1');
    expect(board.share.text).not.toContain('look-1');
  });

  it('the remaining allowance is shown, and it comes from the backend', () => {
    const result = {
      entitlement: { feature: 'style_occasion', period: '2026-08', included: 60, used: 4, remaining: 56, source: 'beta_grant' },
    } as StyleResult;
    render(<EntitlementNote result={result} />);
    expect(screen.getByText('56 of 60 style requests left this month.')).toBeTruthy();
  });

  it('never uses body-shaming or "money wasted" language', () => {
    const rendered = render(<Lookboard look={look()} />);
    const text = JSON.stringify(rendered.toJSON()).toLowerCase();
    for (const banned of ['money wasted', 'slimming', 'flattering', 'problem area', 'body type', 'lose weight', 'attractiveness']) {
      expect(text).not.toContain(banned);
    }
  });
});
