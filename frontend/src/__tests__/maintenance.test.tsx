import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';

import { MaintenanceEmpty, MaintenanceRow, statusLabel } from '../components/care/MaintenancePieces';
import { MaintenanceKindStatus } from '../services/apiV2';

const kind = (overrides: Partial<MaintenanceKindStatus> = {}): MaintenanceKindStatus => ({
  kind: 'haircut',
  label: 'Haircut',
  domain: 'hair_care',
  description: 'Keeping your usual cut in the shape you like.',
  status: 'not_tracked',
  reason: 'not_tracked',
  tracked: false,
  reminders_enabled: false,
  interval_days: 42,
  interval_is_custom: false,
  last_done_on: null,
  next_due_on: null,
  days_until_due: null,
  ...overrides,
});

describe('Maintenance timing UI', () => {
  it('an untracked kind offers only to start tracking', () => {
    const track = jest.fn(); const untrack = jest.fn(); const record = jest.fn();
    render(
      <MaintenanceRow kind={kind()} onTrack={track} onUntrack={untrack} onRecordToday={record} />,
    );
    expect(screen.getByLabelText('Track Haircut')).toBeTruthy();
    expect(screen.queryByLabelText('Record Haircut today')).toBeNull();
    fireEvent.press(screen.getByLabelText('Track Haircut'));
    expect(track).toHaveBeenCalledTimes(1);
  });

  it('a tracked kind can record a date or stop being tracked', () => {
    const track = jest.fn(); const untrack = jest.fn(); const record = jest.fn();
    render(
      <MaintenanceRow
        kind={kind({ tracked: true, status: 'due', days_until_due: -3 })}
        onTrack={track}
        onUntrack={untrack}
        onRecordToday={record}
      />,
    );
    fireEvent.press(screen.getByLabelText('Record Haircut today'));
    fireEvent.press(screen.getByLabelText('Stop tracking Haircut'));
    expect(record).toHaveBeenCalledTimes(1);
    expect(untrack).toHaveBeenCalledTimes(1);
  });

  it('a kind with no recorded date asks for one instead of guessing', () => {
    expect(statusLabel(kind({ tracked: true, status: 'needs_anchor' }))).toBe('Add your last date');
    render(
      <MaintenanceRow
        kind={kind({ tracked: true, status: 'needs_anchor' })}
        onTrack={jest.fn()}
        onUntrack={jest.fn()}
        onRecordToday={jest.fn()}
      />,
    );
    expect(screen.getByText('Add your last date')).toBeTruthy();
  });

  it('reads timing plainly, never as a reprimand', () => {
    expect(statusLabel(kind({ tracked: true, status: 'due', days_until_due: -9 }))).toBe('Due now');
    expect(statusLabel(kind({ tracked: true, status: 'coming_up', days_until_due: 1 }))).toBe('Due tomorrow');
    expect(statusLabel(kind({ tracked: true, status: 'coming_up', days_until_due: 4 }))).toBe('Due in 4 days');
    for (const status of ['due', 'coming_up', 'not_due', 'needs_anchor', 'not_tracked'] as const) {
      const text = statusLabel(kind({ tracked: true, status, days_until_due: 3 })).toLowerCase();
      for (const word of ['overdue', 'late', 'missed', 'failed', 'bad', 'poor']) {
        expect(text).not.toContain(word);
      }
    }
  });

  it('the empty state invites without nagging', () => {
    render(<MaintenanceEmpty />);
    expect(screen.getByText('Nothing tracked yet')).toBeTruthy();
    expect(screen.getByText(/upkeep you already do/)).toBeTruthy();
  });
});
