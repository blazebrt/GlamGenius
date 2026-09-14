jest.mock('../services/api', () => ({
  api: { get: jest.fn() },
}));

import { api } from '../services/api';
import { getPurchaseGuard } from '../services/apiV2';

describe('getPurchaseGuard', () => {
  it('uses the authenticated shared V2 client and exact candidate route', async () => {
    (api.get as jest.Mock).mockResolvedValue({ data: { candidate_id: 'candidate-123' } });
    await expect(getPurchaseGuard('candidate-123')).resolves.toEqual({ candidate_id: 'candidate-123' });
    expect(api.get).toHaveBeenCalledWith('/api/v2/shopping/candidates/candidate-123/purchase-guard');
  });
});
