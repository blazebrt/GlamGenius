import { api } from '../services/api';
import { generateEventReady, getEventReady, setEventReadyActionComplete, setEventReadyLook, type EventReadyCare, type EventReadyStatus, type HairWashCadenceStatus } from '../services/apiV2';

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
});
