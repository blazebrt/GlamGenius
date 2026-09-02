import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';
import { Linking } from 'react-native';

import { CommunityObservations } from '../components/verdict/CommunityObservations';
import {
  BATCH_SCOPED_CODES,
  CommunityReportSheet,
  OBSERVATION_CODES,
  PRODUCT_DATA_CODES,
} from '../components/verdict/CommunityReportSheet';

const REPLY_URL = 'https://example.org/report-a-concern';

const envelope = (signals: unknown[], overrides: Record<string, unknown> = {}) => ({
  policy_version: 'community-observations-v1',
  public_enabled: true,
  active_window_days: 90,
  brand_reply_url: REPLY_URL,
  signals,
  ...overrides,
}) as never;

const batchSignal = {
  observation_code: 'seal_broken', scope: 'batch' as const, batch_number: 'b-123',
  independent_reporters: 3, first_reported_at: '2026-08-01T00:00:00+00:00',
  last_reported_at: '2026-08-20T00:00:00+00:00',
  analysis_score_eligible: false as const, official_finding: false as const,
};
const productSignal = {
  ...batchSignal, observation_code: 'ingredients_list_differs_from_app',
  scope: 'product' as const, batch_number: null,
};

// The words a shopper observation may never put next to a brand's name.
const FORBIDDEN = [
  /warning/i, /danger/i, /unsafe/i, /adulterated/i, /\bfake\b/i, /fraud/i,
  /contaminated/i, /defective/i, /bad batch/i, /safety issue/i,
];

describe('shopper observations on the verdict surface', () => {
  it('states the count, what was seen, and where it came from', () => {
    render(<CommunityObservations communityObservations={envelope([batchSignal])} />);
    expect(screen.getByText('Shopper observations')).toBeTruthy();
    expect(screen.getByText(/3 shoppers reported a broken seal/)).toBeTruthy();
    // The disclosure is visible, not behind a tap.
    expect(screen.getByText(/Reported by shoppers\./)).toBeTruthy();
    expect(screen.getByText(/Not laboratory testing or an official finding\./)).toBeTruthy();
  });

  it('shows the batch only for a batch-scoped signal', () => {
    const view = render(<CommunityObservations communityObservations={envelope([batchSignal])} />);
    expect(screen.getByText(/Batch: b-123/)).toBeTruthy();
    view.rerender(<CommunityObservations communityObservations={envelope([productSignal])} />);
    expect(screen.queryByText(/Batch:/)).toBeNull();
  });

  it('offers the brand a visible right of reply', () => {
    const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(true);
    render(<CommunityObservations communityObservations={envelope([batchSignal])} />);
    fireEvent.press(screen.getByLabelText('Brand right of reply'));
    expect(open).toHaveBeenCalledWith(REPLY_URL);
    open.mockRestore();
  });

  it('renders nothing at all when there is no public signal', () => {
    // Silence, not reassurance: no signal can mean below threshold, outside the
    // window, display off, or another shopper's batch.
    for (const value of [envelope([]), envelope([], { public_enabled: false }), null]) {
      const view = render(<CommunityObservations communityObservations={value as never} />);
      expect(view.toJSON()).toBeNull();
      view.unmount();
    }
  });

  it('never characterises what shoppers saw', () => {
    const { toJSON } = render(
      <CommunityObservations communityObservations={envelope([batchSignal, productSignal])} />,
    );
    const rendered = JSON.stringify(toJSON());
    for (const forbidden of FORBIDDEN) expect(rendered).not.toMatch(forbidden);
  });

  it('shows no photograph, no name and nothing to like or reply to', () => {
    const { toJSON } = render(
      <CommunityObservations communityObservations={envelope([batchSignal])} />,
    );
    const rendered = JSON.stringify(toJSON());
    expect(rendered).not.toMatch(/"Image"/);
    for (const social of [/username/i, /avatar/i, /\blike\b/i, /upvote/i, /comment/i, /reply to/i, /★|⭐/]) {
      expect(rendered).not.toMatch(social);
    }
  });

  it('reads to a screen reader as a count, an observation and its origin', () => {
    render(<CommunityObservations communityObservations={envelope([batchSignal])} />);
    const label = screen.getByLabelText(
      /3 shoppers reported a broken seal\. Batch b-123\. Reported by shoppers\./,
    );
    expect(label).toBeTruthy();
  });
});

describe('the report flow', () => {
  const props = {
    selected: null, onSelect: jest.fn(), onAddPhoto: jest.fn(), onSubmit: jest.fn(),
    onCancel: jest.fn(), onCaptureLabel: jest.fn(), photoAdded: false, busy: false,
    status: null, signedIn: true, batchRequired: false,
  };

  it('offers a closed list of choices and nothing else', () => {
    const { UNSAFE_queryAllByType } = render(<CommunityReportSheet {...props} />);
    expect(OBSERVATION_CODES).toHaveLength(10);
    expect(screen.getByText('a broken seal')).toBeTruthy();
    expect(screen.getByText('the ingredient list looked different')).toBeTruthy();

    // Zero free text: not a comment box, not a caption, not an optional note.
    const { TextInput } = jest.requireActual('react-native');
    expect(UNSAFE_queryAllByType(TextInput)).toHaveLength(0);
    for (const field of [/tell us more/i, /comment/i, /caption/i, /describe/i, /rating/i, /stars/i]) {
      expect(screen.queryByText(field)).toBeNull();
    }
  });

  it('requires a photo before it will submit', () => {
    render(<CommunityReportSheet {...props} selected="seal_broken" />);
    expect(screen.getByText('Add a photo of what you saw.')).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Send observation'));
    expect(props.onSubmit).not.toHaveBeenCalled();
  });

  it('sends a shopper to capture their own label rather than typing a batch', () => {
    const { UNSAFE_queryAllByType } = render(
      <CommunityReportSheet {...props} selected="seal_broken" batchRequired photoAdded />,
    );
    expect(screen.getByText(/Capture the pack label first/)).toBeTruthy();
    const { TextInput } = jest.requireActual('react-native');
    expect(UNSAFE_queryAllByType(TextInput)).toHaveLength(0);
    fireEvent.press(screen.getByLabelText('Capture pack label'));
    expect(props.onCaptureLabel).toHaveBeenCalled();
    expect(BATCH_SCOPED_CODES).toContain('seal_broken');
    expect(PRODUCT_DATA_CODES).not.toContain('seal_broken');
  });

  it('tells a signed-out shopper what is needed without blocking the screen', () => {
    render(<CommunityReportSheet {...props} signedIn={false} selected="seal_broken" photoAdded />);
    expect(screen.getByText('Sign in on this phone to send a report.')).toBeTruthy();
  });

  it('announces the selection and says saved, never verified', () => {
    render(
      <CommunityReportSheet
        {...props} selected="pack_leaking" photoAdded status="Observation saved."
      />,
    );
    expect(screen.getByLabelText('a leaking pack').props.accessibilityState.selected).toBe(true);
    expect(screen.getByText('Observation saved.')).toBeTruthy();
    for (const overclaim of [/verified/i, /confirmed/i, /proven/i, /validated/i]) {
      expect(screen.queryByText(overclaim)).toBeNull();
    }
  });
});
