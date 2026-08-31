/**
 * The environment on screen.
 *
 * Two rules are checked here rather than described: the decision is shown with
 * its reason beneath it, and the readings never appear as a row of numbers a
 * person is left to interpret.
 */
import React from 'react';
import { render, screen } from '@testing-library/react-native';

import { EnvironmentDecisionCard } from '../components/today/TodayPieces';
import type { PlanAction } from '../services/apiV2';

const decision: PlanAction = {
  id: 'a1',
  module: 'skincare',
  action_type: 'environment_decision',
  title: 'Exfoliation and retinoids are deferred to the next clean day.',
  body: 'Air is very poor (NAQI 340, CPCB category Very Poor). Exfoliation and retinoids are held until the air is Satisfactory or better; barrier support takes their place today.',
  priority: 25,
  relevance: 'Worth a cleanse when you get home.',
  inventory_item_id: null,
  completed: false,
  completed_at: null,
};

describe('the environment decision', () => {
  it('shows the decision with its reason beneath it', () => {
    render(<EnvironmentDecisionCard action={decision} />);
    expect(screen.getByText(decision.title)).toBeTruthy();
    expect(screen.getByText(decision.body)).toBeTruthy();
  });

  it('states the reading and the published category', () => {
    render(<EnvironmentDecisionCard action={decision} />);
    expect(screen.getByText(/NAQI 340/)).toBeTruthy();
    expect(screen.getByText(/CPCB category Very Poor/)).toBeTruthy();
  });

  it('carries the one supporting note and no more', () => {
    render(<EnvironmentDecisionCard action={decision} />);
    expect(screen.getByText('Worth a cleanse when you get home.')).toBeTruthy();
  });

  it('is a decision, not a task to tick off', () => {
    render(<EnvironmentDecisionCard action={decision} />);
    expect(screen.queryByRole('checkbox')).toBeNull();
  });

  it('never claims a health outcome', () => {
    render(<EnvironmentDecisionCard action={decision} />);
    const text = JSON.stringify(screen.toJSON()).toLowerCase();
    for (const phrase of ['damaging your skin', 'damage', 'protects', 'prevents', 'harm']) {
      expect(text).not.toContain(phrase);
    }
  });

  it('shows no reading it was not given', () => {
    render(<EnvironmentDecisionCard action={{ ...decision, relevance: '' }} />);
    const text = JSON.stringify(screen.toJSON()).toLowerCase();
    // Not a dashboard: humidity, UV and temperature are not listed beside the AQI.
    expect(text).not.toContain('humidity:');
    expect(text).not.toContain('uv index:');
    expect(text).not.toContain('°c');
  });
});
