import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';

import {
  MaintenanceEmpty,
  MaintenanceRow,
  MaintenanceSetup,
  isValidInterval,
  isValidPastDate,
  missingFact,
  statusLabel,
} from '../components/care/MaintenancePieces';
import { MaintenanceKindStatus } from '../services/apiV2';

const BOUNDS = { min_days: 3, max_days: 365 };
const TODAY = '2026-03-16';

const kind = (overrides: Partial<MaintenanceKindStatus> = {}): MaintenanceKindStatus => ({
  kind: 'haircut',
  label: 'Haircut',
  domain: 'hair_care',
  description: 'Keeping your usual cut in the shape you like.',
  status: 'not_tracked',
  reason: 'not_tracked',
  tracked: false,
  reminders_enabled: false,
  interval_days: null,
  suggested_interval_days: 42,
  lead_days: null,
  last_done_on: null,
  next_due_on: null,
  days_until_due: null,
  ...overrides,
});

const handlers = () => ({
  bounds: BOUNDS,
  today: TODAY,
  onToggleExpanded: jest.fn(),
  onTrack: jest.fn(),
  onUntrack: jest.fn(),
  onRecordToday: jest.fn(),
  onSaveCadence: jest.fn(),
  onClearCadence: jest.fn(),
  onSaveLastDate: jest.fn(),
  onForgetLastDate: jest.fn(),
  onToggleReminders: jest.fn(),
});

describe('Maintenance timing UI', () => {
  it('an untracked kind offers only to start tracking', () => {
    const props = handlers();
    render(<MaintenanceRow kind={kind()} {...props} />);
    expect(screen.getByLabelText('Track Haircut')).toBeTruthy();
    expect(screen.queryByLabelText('Record Haircut today')).toBeNull();
    fireEvent.press(screen.getByLabelText('Track Haircut'));
    expect(props.onTrack).toHaveBeenCalledTimes(1);
  });

  it('tracking without a rhythm asks for one instead of assuming the preset', () => {
    const props = handlers();
    const row = kind({ tracked: true, status: 'needs_cadence', reason: 'no_cadence_set' });
    expect(missingFact(row)).toBe('cadence');
    render(<MaintenanceRow kind={row} {...props} />);
    expect(screen.getByText('Add your usual timing')).toBeTruthy();
    // Nothing to mark done while there is no schedule.
    expect(screen.queryByLabelText('Record Haircut today')).toBeNull();
    fireEvent.press(screen.getByLabelText('Set up Haircut'));
    expect(props.onToggleExpanded).toHaveBeenCalledTimes(1);
  });

  it('a rhythm without a date asks for the date', () => {
    const row = kind({ tracked: true, status: 'needs_anchor', interval_days: 42 });
    expect(missingFact(row)).toBe('last_date');
    render(<MaintenanceRow kind={row} {...handlers()} />);
    expect(screen.getByText(/Add your last date/)).toBeTruthy();
  });

  it('a configured kind shows its own rhythm and can be recorded', () => {
    const props = handlers();
    const row = kind({ tracked: true, status: 'due', interval_days: 30, days_until_due: -2 });
    render(<MaintenanceRow kind={row} {...props} />);
    expect(screen.getByText(/every 30 days/)).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Record Haircut today'));
    fireEvent.press(screen.getByLabelText('Stop tracking Haircut'));
    expect(props.onRecordToday).toHaveBeenCalledTimes(1);
    expect(props.onUntrack).toHaveBeenCalledTimes(1);
  });

  it('the setup panel lets you choose, accept or clear a rhythm', () => {
    const props = handlers();
    const row = kind({ tracked: true, status: 'needs_cadence' });
    render(
      <MaintenanceSetup kind={row} onClose={jest.fn()} {...props} />,
    );
    fireEvent.changeText(screen.getByLabelText('Days between each Haircut'), '30');
    fireEvent.press(screen.getByLabelText('Save timing for Haircut'));
    expect(props.onSaveCadence).toHaveBeenCalledWith(30);

    fireEvent.press(screen.getByLabelText('Use the suggested timing for Haircut'));
    expect(props.onSaveCadence).toHaveBeenLastCalledWith(42);
    expect(screen.getByText(/only a suggestion/)).toBeTruthy();
  });

  it('an existing rhythm can be cleared', () => {
    const props = handlers();
    render(
      <MaintenanceSetup
        kind={kind({ tracked: true, status: 'needs_anchor', interval_days: 30 })}
        onClose={jest.fn()}
        {...props}
      />,
    );
    fireEvent.press(screen.getByLabelText('Clear your timing for Haircut'));
    expect(props.onClearCadence).toHaveBeenCalledTimes(1);
  });

  it('a historical last date can be entered, and a future one cannot be saved', () => {
    const props = handlers();
    render(
      <MaintenanceSetup
        kind={kind({ tracked: true, status: 'needs_anchor', interval_days: 42 })}
        onClose={jest.fn()}
        {...props}
      />,
    );
    const field = screen.getByLabelText('Date you last did Haircut');
    fireEvent.changeText(field, '2026-03-06');
    fireEvent.press(screen.getByLabelText('Save last date for Haircut'));
    expect(props.onSaveLastDate).toHaveBeenCalledWith('2026-03-06');

    fireEvent.changeText(field, '2026-12-31');
    fireEvent.press(screen.getByLabelText('Save last date for Haircut'));
    expect(props.onSaveLastDate).toHaveBeenCalledTimes(1);
  });

  it('a wrongly recorded date can be removed, not only corrected forwards', () => {
    const props = handlers();
    render(
      <MaintenanceSetup
        kind={kind({ tracked: true, status: 'due', interval_days: 42, last_done_on: '2026-01-02' })}
        onClose={jest.fn()}
        {...props}
      />,
    );
    fireEvent.press(screen.getByLabelText('Remove the recorded date for Haircut'));
    expect(props.onForgetLastDate).toHaveBeenCalledWith('2026-01-02');
  });

  it('saving an earlier correction replaces the old date rather than sitting behind it', () => {
    const props = handlers();
    render(
      <MaintenanceSetup
        kind={kind({ tracked: true, status: 'due', interval_days: 42, last_done_on: '2026-03-10' })}
        onClose={jest.fn()}
        {...props}
      />,
    );
    const field = screen.getByLabelText('Date you last did Haircut');
    // The field is prefilled with the anchor; editing it means "correct this".
    expect(field.props.value).toBe('2026-03-10');
    fireEvent.changeText(field, '2026-02-01');
    fireEvent.press(screen.getByLabelText('Save last date for Haircut'));
    expect(props.onSaveLastDate).toHaveBeenCalledWith('2026-02-01');
  });

  it('reminders are a switch, and off is the starting point', () => {
    const props = handlers();
    render(
      <MaintenanceSetup
        kind={kind({ tracked: true, status: 'not_due', interval_days: 42, days_until_due: 10 })}
        onClose={jest.fn()}
        {...props}
      />,
    );
    const toggle = screen.getByLabelText('Reminders for Haircut');
    expect(toggle.props.value).toBe(false);
    expect(screen.getByText('Off unless you turn it on.')).toBeTruthy();
    fireEvent(toggle, 'valueChange', true);
    expect(props.onToggleReminders).toHaveBeenCalledWith(true);
  });

  it('validates a rhythm against the bounds the API reported', () => {
    expect(isValidInterval('30', BOUNDS)).toBe(true);
    expect(isValidInterval('3', BOUNDS)).toBe(true);
    expect(isValidInterval('2', BOUNDS)).toBe(false);
    expect(isValidInterval('366', BOUNDS)).toBe(false);
    expect(isValidInterval('', BOUNDS)).toBe(false);
    expect(isValidInterval('ten', BOUNDS)).toBe(false);
  });

  it('rejects malformed and future dates before they reach the API', () => {
    expect(isValidPastDate('2026-03-06', TODAY)).toBe(true);
    expect(isValidPastDate(TODAY, TODAY)).toBe(true);
    expect(isValidPastDate('2026-03-17', TODAY)).toBe(false);
    expect(isValidPastDate('2026-02-30', TODAY)).toBe(false);
    expect(isValidPastDate('06-03-2026', TODAY)).toBe(false);
    expect(isValidPastDate('', TODAY)).toBe(false);
  });

  it('reads timing plainly, never as a reprimand', () => {
    expect(statusLabel(kind({ tracked: true, status: 'due', days_until_due: -9 }))).toBe('Due by your rhythm');
    expect(statusLabel(kind({ tracked: true, status: 'coming_up', days_until_due: 1 }))).toBe('Due tomorrow');
    expect(statusLabel(kind({ tracked: true, status: 'coming_up', days_until_due: 4 }))).toBe('Due in 4 days');
    for (const status of ['due', 'coming_up', 'not_due', 'needs_cadence', 'needs_anchor', 'not_tracked'] as const) {
      const text = statusLabel(kind({ tracked: true, status, days_until_due: 3 })).toLowerCase();
      for (const word of ['overdue', 'late', 'missed', 'failed', 'neglect', 'bad', 'poor', 'should', 'salon', 'book']) {
        expect(text).not.toContain(word);
      }
    }
  });

  it('every control carries a semantic label and a usable target', () => {
    render(
      <MaintenanceSetup
        kind={kind({ tracked: true, status: 'needs_anchor', interval_days: 42 })}
        onClose={jest.fn()}
        {...handlers()}
      />,
    );
    for (const label of [
      'Days between each Haircut',
      'Save timing for Haircut',
      'Date you last did Haircut',
      'Save last date for Haircut',
      'Reminders for Haircut',
      'Close Haircut settings',
    ]) {
      expect(screen.getByLabelText(label)).toBeTruthy();
    }
  });

  it('controls are inert while a save is in flight', () => {
    const props = handlers();
    render(
      <MaintenanceRow
        kind={kind({ tracked: true, status: 'due', interval_days: 30, days_until_due: -1 })}
        busy
        {...props}
      />,
    );
    fireEvent.press(screen.getByLabelText('Record Haircut today'));
    expect(props.onRecordToday).not.toHaveBeenCalled();
  });

  it('the empty state invites without nagging', () => {
    render(<MaintenanceEmpty />);
    expect(screen.getByText('Nothing tracked yet')).toBeTruthy();
    expect(screen.getByText(/upkeep you already do/)).toBeTruthy();
  });
});
