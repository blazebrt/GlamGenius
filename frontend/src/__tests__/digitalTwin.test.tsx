import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';

import { GoalChooser, GOAL_OPTIONS, OnboardingRecovery, validateGoal } from '../components/onboarding/OnboardingPieces';
import { ObservationReviewCard, confidenceLabel } from '../components/profile/ObservationReviewCard';
import { ProfileObservation } from '../services/apiV2';

const observation: ProfileObservation = {
  id: 'obs-1', key: 'undertone', label: 'Undertone', value: 'warm',
  source: 'photo_observed', confidence: 0.82,
  why: 'Warm colour cast in even light', verification_state: 'unverified',
  source_ai_run_id: 'run-1', created_at: null, reviewed_at: null,
};

describe('progressive onboarding', () => {
  it('starts with the concrete goal question and all required options', () => {
    render(<GoalChooser value="" onChange={jest.fn()} />);
    expect(screen.getByLabelText('What are you preparing for options')).toBeTruthy();
    for (const goal of GOAL_OPTIONS) expect(screen.getByLabelText(goal)).toBeTruthy();
  });

  it('validates that a goal was explicitly chosen', () => {
    expect(validateGoal('')).toMatch(/choose/i);
    expect(validateGoal('Work')).toBeNull();
  });

  it('exposes radio semantics and selection state', () => {
    const change = jest.fn();
    render(<GoalChooser value="Work" onChange={change} />);
    expect(screen.getByLabelText('Work').props.accessibilityState.selected).toBe(true);
    fireEvent.press(screen.getByLabelText('Travel'));
    expect(change).toHaveBeenCalledWith('Travel');
  });

  it('offers navigation recovery when loading fails', () => {
    const retry = jest.fn(); const home = jest.fn();
    render(<OnboardingRecovery onRetry={retry} onHome={home} />);
    fireEvent.press(screen.getByLabelText('Retry onboarding'));
    fireEvent.press(screen.getByLabelText('Return home'));
    expect(retry).toHaveBeenCalled(); expect(home).toHaveBeenCalled();
  });
});

describe('observation verification', () => {
  it('shows value, confidence and why before any action', () => {
    render(<ObservationReviewCard observation={observation} onConfirm={jest.fn()} onReject={jest.fn()} onEdit={jest.fn()} onNotSure={jest.fn()} />);
    expect(screen.getByText('warm')).toBeTruthy();
    expect(screen.getByText('High confidence')).toBeTruthy();
    expect(screen.getByText(/warm colour cast/i)).toBeTruthy();
  });

  it('provides accessible confirm, edit, reject and not-sure actions', () => {
    const confirm = jest.fn(); const reject = jest.fn(); const notSure = jest.fn();
    render(<ObservationReviewCard observation={observation} onConfirm={confirm} onReject={reject} onEdit={jest.fn()} onNotSure={notSure} />);
    fireEvent.press(screen.getByLabelText('Confirm Undertone'));
    fireEvent.press(screen.getByLabelText('Not sure about Undertone'));
    fireEvent.press(screen.getByLabelText('Reject Undertone'));
    expect(confirm).toHaveBeenCalled(); expect(notSure).toHaveBeenCalled(); expect(reject).toHaveBeenCalled();
    expect(screen.getByLabelText('Edit Undertone')).toBeTruthy();
  });

  it('uses understandable confidence thresholds', () => {
    expect(confidenceLabel(0.8)).toBe('High confidence');
    expect(confidenceLabel(0.6)).toBe('Medium confidence');
    expect(confidenceLabel(0.4)).toBe('Low confidence');
  });
});
