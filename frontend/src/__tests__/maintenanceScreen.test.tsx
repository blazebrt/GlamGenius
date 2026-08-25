import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import MaintenanceScreen from '../../app/(tabs)/services';
import * as apiV2 from '../services/apiV2';

jest.mock('expo-router', () => ({
  useFocusEffect: (callback: () => void) => jest.requireActual<typeof React>('react').useEffect(callback, []),
}));

const overview = (last_done_on: string | null) => ({
  version: 'v3-03.1', catalogue_version: 'v1', plan_date: '2026-03-16',
  note: 'Timing reminders.', interval_bounds: { min_days: 3, max_days: 365 },
  due: [], coming_up: [], needs_cadence: [], needs_anchor: ['haircut'],
  kinds: [{
    kind: 'haircut', label: 'Haircut', domain: 'hair_care',
    description: 'Keeping your usual cut in the shape you like.', status: 'needs_anchor',
    reason: 'no_recorded_date', tracked: true, reminders_enabled: false,
    interval_days: 42, suggested_interval_days: 42, lead_days: null,
    last_done_on, next_due_on: null, days_until_due: null,
  }],
});

describe('maintenance date correction screen', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(apiV2, 'getMaintenance').mockResolvedValue(overview('2026-03-10') as any);
    jest.spyOn(apiV2, 'replaceMaintenanceDone').mockResolvedValue(overview('2026-02-01') as any);
    jest.spyOn(apiV2, 'recordMaintenanceDone').mockResolvedValue(overview('2026-03-10') as any);
    jest.spyOn(apiV2, 'forgetMaintenanceDone').mockResolvedValue({ ...overview(null), removed: true } as any);
  });

  afterEach(() => jest.restoreAllMocks());

  it('corrects an existing date with one replacement request and keeps the edit on failure', async () => {
    const replace = apiV2.replaceMaintenanceDone as jest.Mock;
    replace.mockRejectedValueOnce(new Error('offline'));
    render(<MaintenanceScreen />);
    await waitFor(() => expect(screen.getByLabelText('Set up Haircut')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Set up Haircut'));
    const field = screen.getByLabelText('Date you last did Haircut');
    fireEvent.changeText(field, '2026-02-01');
    fireEvent.press(screen.getByLabelText('Save last date for Haircut'));
    expect(replace).toHaveBeenCalledWith('haircut', '2026-03-10', '2026-02-01');
    expect(apiV2.forgetMaintenanceDone).not.toHaveBeenCalled();
    expect(apiV2.recordMaintenanceDone).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByText('That change did not save. Please try again.')).toBeTruthy());
    expect(field.props.value).toBe('2026-02-01');
  });

  it('uses record for an initial date and DELETE for explicit removal', async () => {
    (apiV2.getMaintenance as jest.Mock).mockResolvedValueOnce(overview(null) as any);
    render(<MaintenanceScreen />);
    await waitFor(() => expect(screen.getByLabelText('Set up Haircut')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Set up Haircut'));
    const field = screen.getByLabelText('Date you last did Haircut');
    fireEvent.changeText(field, '2026-03-10');
    fireEvent.press(screen.getByLabelText('Save last date for Haircut'));
    expect(apiV2.recordMaintenanceDone).toHaveBeenCalledWith('haircut', { done_on: '2026-03-10' });
    expect(apiV2.replaceMaintenanceDone).not.toHaveBeenCalled();

    (apiV2.getMaintenance as jest.Mock).mockResolvedValueOnce(overview('2026-03-10') as any);
    render(<MaintenanceScreen />);
    await waitFor(() => expect(screen.getAllByLabelText('Set up Haircut').length).toBeGreaterThan(0));
    fireEvent.press(screen.getAllByLabelText('Set up Haircut')[0]);
    fireEvent.press(screen.getAllByLabelText('Remove the recorded date for Haircut')[0]);
    expect(apiV2.forgetMaintenanceDone).toHaveBeenCalledWith('haircut', '2026-03-10');
  });
});
