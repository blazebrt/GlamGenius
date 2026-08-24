import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';

import {
  DayCard, GoogleCalendarControl, LAUNDRY_LABELS, LaundryPanel, RepetitionPanel, SwapBar, UpcomingEvents, UpcomingEventCard, WeekEmpty, repeatedOn,
} from '../components/planner/PlannerPieces';
import { LookPiece, PlannerDay, WeeklyPlan } from '../services/apiV2';

const piece = (name: string, slot: LookPiece['slot'] = 'clothing'): LookPiece => ({
  id: `p-${name}`, slot, ownership: 'owned', inventory_item_id: `inv-${name}`,
  display_name: name, brand: null, category: 'wardrobe', subcategory: null,
  role_note: '', score: 0.8, owned: true, label: 'In your inventory',
});

const day = (overrides: Partial<PlannerDay> = {}): PlannerDay => ({
  plan_date: '2026-08-03', weekday: 'Monday', locked: false, note: null,
  headline: 'Monday: office', status: 'ready', confidence: 0.7,
  weather: {
    id: 'w', for_date: '2026-08-03', condition: 'rainy', temp_min_c: null, temp_max_c: null,
    precipitation_chance: 80, humidity: null, location: null, provider: 'manual', source: 'user_declared',
  },
  owned_items: [piece('Navy formal shirt'), piece('Black derby shoes', 'shoes')],
  optional_addition_count: 0, ...overrides,
});

const week = (overrides: Partial<WeeklyPlan> = {}): WeeklyPlan => ({
  week_start: '2026-08-03', status: 'ready', version: 1,
  days: [
    day(),
    day({ plan_date: '2026-08-04', weekday: 'Tuesday', owned_items: [piece('White shirt'), piece('Black derby shoes', 'shoes')] }),
    day({ plan_date: '2026-08-05', weekday: 'Wednesday', locked: true }),
  ],
  repetition: {
    repeated_items: [{ display_name: 'Black derby shoes', dates: ['2026-08-03', '2026-08-04'], times: 2 }],
    note: 'Repeating something is normal. This is here so you can see it, not so you avoid it.',
  },
  laundry: [{ item_id: 'inv-1', state: 'in_wash', available_from: '2026-08-06' }],
  ...overrides,
});

describe('Weekly planner UI', () => {
  const event = {
    id: 'event-1', title: 'Wedding', starts_at: '2026-09-01T18:30:00Z', local_time: '00:00', local_date: '2026-09-02',
    ends_at: null, all_day: false, location: 'Hall', occasion_key: 'wedding' as const, dress_code_hint: null,
    inference_confidence: 1, user_confirmed: true, provider: 'manual', source: 'user_declared', status: 'active',
  };

  it('shows upcoming event cards and opens the exact event', () => {
    const open = jest.fn(); const add = jest.fn();
    render(<UpcomingEvents events={[event]} onEventPress={open} onAdd={add} />);
    expect(screen.getAllByText('Wedding').length).toBeGreaterThan(0);
    expect(screen.getByText('2026-09-02 · 00:00')).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Open event Wedding'));
    fireEvent.press(screen.getByLabelText('Add an event'));
    expect(open).toHaveBeenCalledWith(event);
    expect(add).toHaveBeenCalled();
  });

  it('shows unconfirmed and empty upcoming states without hiding the weekly planner', () => {
    render(<UpcomingEventCard event={{ ...event, user_confirmed: false, occasion_key: null }} onPress={jest.fn()} />);
    expect(screen.getByText('Event type needs confirmation')).toBeTruthy();
    render(<UpcomingEvents events={[]} onEventPress={jest.fn()} onAdd={jest.fn()} />);
    expect(screen.getByText('No important events here yet.')).toBeTruthy();
  });

  it('distinguishes an upcoming load failure from a successful empty response', () => {
    const retry = jest.fn();
    render(<UpcomingEvents events={[]} error="We could not load upcoming events right now." onRetry={retry} onEventPress={jest.fn()} onAdd={jest.fn()} />);
    expect(screen.getByText('We could not load upcoming events right now.')).toBeTruthy();
    expect(screen.queryByText('No important events here yet.')).toBeNull();
    fireEvent.press(screen.getByLabelText('Retry upcoming events'));
    expect(retry).toHaveBeenCalled();
  });

  it('offers calm connect, refresh and disconnect controls for Google Calendar', () => {
    const connect = jest.fn(); const sync = jest.fn(); const disconnect = jest.fn();
    render(<GoogleCalendarControl status="disconnected" busy={false} onConnect={connect} onSync={sync} onDisconnect={disconnect} />);
    expect(screen.getByText('Connect Google Calendar')).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Connect Google Calendar'));
    expect(connect).toHaveBeenCalledTimes(1);

    render(<GoogleCalendarControl status="connected" label="Google Calendar" lastSyncedAt="2026-08-10T10:00:00Z" onConnect={connect} onSync={sync} onDisconnect={disconnect} />);
    fireEvent.press(screen.getByLabelText('Refresh Google Calendar'));
    fireEvent.press(screen.getByLabelText('Disconnect Google Calendar'));
    expect(sync).toHaveBeenCalledTimes(1);
    expect(disconnect).toHaveBeenCalledTimes(1);
  });

  it('shows a calendar failure on the calendar card itself', () => {
    const noop = jest.fn();
    render(
      <GoogleCalendarControl
        status="connected"
        message="Google Calendar could not refresh right now."
        onConnect={noop}
        onSync={noop}
        onDisconnect={noop}
      />,
    );
    expect(screen.getByText('Google Calendar could not refresh right now.')).toBeTruthy();
  });

  it('tells you how a pending disconnect actually gets finished', () => {
    const noop = jest.fn();
    render(
      <GoogleCalendarControl status="revocation_pending" onConnect={noop} onSync={noop} onDisconnect={noop} />,
    );
    expect(screen.getByText(/Tap Retry to finish removing our access/)).toBeTruthy();
    expect(screen.getByLabelText('Retry Google Calendar disconnect')).toBeTruthy();
  });

  it('an ungenerated week invites you to build one', () => {
    const generate = jest.fn();
    render(<WeekEmpty onGenerate={generate} />);
    expect(screen.getByText('Plan your week')).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Generate this week'));
    expect(generate).toHaveBeenCalled();
  });

  it('shows a busy state instead of letting you press twice', () => {
    render(<WeekEmpty onGenerate={jest.fn()} busy />);
    expect(screen.getByText(/building your week/i)).toBeTruthy();
  });

  it('a day shows its outfit, weather and lock state', () => {
    render(<DayCard day={day()} />);
    expect(screen.getByText('Monday')).toBeTruthy();
    expect(screen.getByText('Navy formal shirt · Black derby shoes')).toBeTruthy();
    expect(screen.getByLabelText('Weather rainy')).toBeTruthy();
    expect(screen.queryByLabelText('Monday is locked')).toBeNull();

    render(<DayCard day={day({ plan_date: '2026-08-05', weekday: 'Wednesday', locked: true })} />);
    expect(screen.getByLabelText('Wednesday is locked')).toBeTruthy();
  });

  it('a day can be locked and unlocked', () => {
    const lock = jest.fn();
    render(<DayCard day={day()} onLock={lock} />);
    fireEvent.press(screen.getByLabelText('Lock Monday'));
    expect(lock).toHaveBeenCalled();

    render(<DayCard day={day({ plan_date: '2026-08-05', weekday: 'Wednesday', locked: true })} onLock={jest.fn()} />);
    expect(screen.getByLabelText('Unlock Wednesday')).toBeTruthy();
  });

  it('a locked day offers no regenerate control', () => {
    render(<DayCard day={day({ locked: true })} onRegenerate={jest.fn()} onLock={jest.fn()} />);
    expect(screen.queryByLabelText('Regenerate Monday')).toBeNull();

    render(<DayCard day={day({ plan_date: '2026-08-04', weekday: 'Tuesday' })} onRegenerate={jest.fn()} />);
    expect(screen.getByLabelText('Regenerate Tuesday')).toBeTruthy();
  });

  it('repeats are surfaced on the day, not hidden', () => {
    render(<DayCard day={day()} repeats={['Black derby shoes']} />);
    expect(screen.getByLabelText('Repeats on Monday')).toBeTruthy();
    expect(screen.getByText(/also worn another day: black derby shoes/i)).toBeTruthy();
  });

  it('maps repeated items back onto the days they fall on', () => {
    const byDate = repeatedOn(week());
    expect(byDate['2026-08-03']).toEqual(['Black derby shoes']);
    expect(byDate['2026-08-04']).toEqual(['Black derby shoes']);
    expect(byDate['2026-08-05']).toBeUndefined();
  });

  it('a day with nothing planned says so', () => {
    render(<DayCard day={day({ owned_items: [], status: 'needs_inventory' })} />);
    expect(screen.getByText(/nothing planned for this day yet/i)).toBeTruthy();
  });

  it('optional additions are counted so you know what you would need to buy', () => {
    render(<DayCard day={day({ optional_addition_count: 2 })} />);
    expect(screen.getByText(/2 optional additions you do not own/i)).toBeTruthy();
  });

  it('moving a day offers every unlocked day as a target', () => {
    const pick = jest.fn(); const cancel = jest.fn();
    const plan = week();
    render(<SwapBar from={plan.days[0]} days={plan.days} onPick={pick} onCancel={cancel} />);

    expect(screen.getByText('Move Monday to…')).toBeTruthy();
    expect(screen.getByLabelText('Swap with Tuesday')).toBeTruthy();
    // The locked Wednesday and Monday itself are not offered.
    expect(screen.queryByLabelText('Swap with Wednesday')).toBeNull();
    expect(screen.queryByLabelText('Swap with Monday')).toBeNull();

    fireEvent.press(screen.getByLabelText('Swap with Tuesday'));
    expect(pick).toHaveBeenCalledWith('2026-08-04');
    fireEvent.press(screen.getByLabelText('Cancel move'));
    expect(cancel).toHaveBeenCalled();
  });

  it('explains when every other day is locked rather than showing an empty row', () => {
    const plan = week({ days: [day(), day({ plan_date: '2026-08-04', weekday: 'Tuesday', locked: true })] });
    render(<SwapBar from={plan.days[0]} days={plan.days} onPick={jest.fn()} onCancel={jest.fn()} />);
    expect(screen.getByText(/every other day is locked/i)).toBeTruthy();
  });

  it('repetition is presented as information, never as a rule', () => {
    render(<RepetitionPanel plan={week()} />);
    expect(screen.getByText('• Black derby shoes — 2 days')).toBeTruthy();
    expect(screen.getByText(/normal.*so you can see it/i)).toBeTruthy();
  });

  it('nothing is rendered when nothing repeats', () => {
    const view = render(<RepetitionPanel plan={week({ repetition: { repeated_items: [], note: '' } })} />);
    expect(view.toJSON()).toBeNull();
  });

  it('unavailable items are listed with when they come back', () => {
    render(<LaundryPanel plan={week()} />);
    expect(screen.getByText(/in the wash until 2026-08-06/i)).toBeTruthy();
    expect(LAUNDRY_LABELS.in_wash).toBe('In the wash');
    expect(Object.keys(LAUNDRY_LABELS)).toHaveLength(4);
  });

  it('renders no laundry panel when everything is available', () => {
    const view = render(<LaundryPanel plan={week({ laundry: [] })} />);
    expect(view.toJSON()).toBeNull();
  });
});
