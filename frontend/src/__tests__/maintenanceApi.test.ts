import { api } from '../services/api';
import {
  forgetMaintenanceDone,
  recordMaintenanceDone,
  replaceMaintenanceDone,
} from '../services/apiV2';

jest.mock('../services/api', () => ({
  api: {
    delete: jest.fn(),
    patch: jest.fn(),
    post: jest.fn(),
  },
}));

describe('maintenance date API', () => {
  beforeEach(() => jest.clearAllMocks());

  it('uses one PATCH for a date correction, while create and remove retain their routes', async () => {
    (api.patch as jest.Mock).mockResolvedValue({ data: { kinds: [] } });
    (api.post as jest.Mock).mockResolvedValue({ data: { kinds: [] } });
    (api.delete as jest.Mock).mockResolvedValue({ data: { kinds: [], removed: true } });

    await replaceMaintenanceDone('haircut', '2026-03-10', '2026-02-01');
    expect(api.patch).toHaveBeenCalledWith(
      '/api/v2/maintenance/haircut/history/2026-03-10',
      { done_on: '2026-02-01' },
    );
    expect(api.post).not.toHaveBeenCalled();
    expect(api.delete).not.toHaveBeenCalled();

    await recordMaintenanceDone('haircut', { done_on: '2026-03-10' });
    expect(api.post).toHaveBeenCalledWith(
      '/api/v2/maintenance/haircut/done',
      { done_on: '2026-03-10' },
    );
    await forgetMaintenanceDone('haircut', '2026-03-10');
    expect(api.delete).toHaveBeenCalledWith('/api/v2/maintenance/haircut/done/2026-03-10');
  });
});
