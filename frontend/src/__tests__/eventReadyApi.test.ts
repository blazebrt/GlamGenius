import { api } from '../services/api';
import { addCalendarEvent, generateEventReady, getEventReady, getUpcomingEvents, patchCalendarEvent, setEventReadyActionComplete, setEventReadyLook, type EventReadyCare, type EventReadyStatus, type HairWashCadenceStatus } from '../services/apiV2';

describe('VC-02 Event Ready API contract', () => {
  afterEach(() => jest.restoreAllMocks());

  it('uses account-scoped canonical routes and preserves null/boolean bodies', async () => {
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: { event_ready_version: 'vc-02-v1' } } as never);
    const post = jest.spyOn(api, 'post').mockResolvedValue({ data: { event_ready_version: 'vc-02-v1' } } as never);
    const patch = jest.spyOn(api, 'patch').mockResolvedValue({ data: { event_ready_version: 'vc-02-v1' } } as never);

    await getEventReady('event-1');
    await generateEventReady('event-1');
    await setEventReadyLook('event-1', null);
    await setEventReadyActionComplete('event-1', 'action-1', false);

    expect(get).toHaveBeenCalledWith('/api/v2/planner/events/event-1/ready');
    expect(post).toHaveBeenNthCalledWith(1, '/api/v2/planner/events/event-1/ready/generate');
    expect(patch).toHaveBeenCalledWith('/api/v2/planner/events/event-1/ready/look', { look_id: null });
    expect(post).toHaveBeenNthCalledWith(2, '/api/v2/planner/events/event-1/ready/actions/action-1/complete', { completed: false });
  });

  it('keeps not_generated, Care-null, and all Hair cadence states in the typed contract', () => {
    const status: EventReadyStatus = 'not_generated';
    const care: EventReadyCare | null = null;
    const cadenceStatuses: HairWashCadenceStatus[] = ['due', 'not_due', 'needs_anchor', 'unscheduled'];
    expect(status).toBe('not_generated');
    expect(care).toBeNull();
    expect(cadenceStatuses).toHaveLength(4);
  });

  it('uses the upcoming, manual event, and canonical correction routes without account injection', async () => {
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: { timezone: 'Asia/Kolkata', events: [] } } as never);
    const post = jest.spyOn(api, 'post').mockResolvedValue({ data: { event: { id: 'event-1' }, created: true, plan: {} } } as never);
    const patch = jest.spyOn(api, 'patch').mockResolvedValue({ data: { id: 'event-1' } } as never);

    await getUpcomingEvents();
    await addCalendarEvent({ title: 'Wedding', starts_at: '2030-09-12T12:00:00.000Z', occasion_key: 'wedding', all_day: false });
    await patchCalendarEvent('event-1', { occasion_key: 'wedding' });

    expect(get).toHaveBeenCalledWith('/api/v2/planner/events/upcoming', { params: { days: 90, limit: 20 } });
    expect(post).toHaveBeenCalledWith('/api/v2/today/events', expect.objectContaining({ title: 'Wedding', occasion_key: 'wedding' }));
    expect(post.mock.calls[0][1]).not.toHaveProperty('account_id');
    expect(patch).toHaveBeenCalledWith('/api/v2/integrations/calendar/events/event-1', { occasion_key: 'wedding' });
  });
});
