import { api } from '../services/api';
import {
  pauseCareProduct, preferCareProduct, resumeCareProduct, simplifyCareRoutine, unpreferCareProduct,
} from '../services/apiV2';

describe('VC-01 Care control routes', () => {
  afterEach(() => jest.restoreAllMocks());

  it('uses the canonical server-owned routes without account or selection payloads', async () => {
    const post = jest.spyOn(api, 'post').mockResolvedValue({ data: { changed: true, status: 'ok', message: 'Saved.' } } as any);
    await simplifyCareRoutine();
    await pauseCareProduct('item-1');
    await resumeCareProduct('item-1');
    await preferCareProduct('item-1');
    await unpreferCareProduct('item-1');
    expect(post.mock.calls).toEqual([
      ['/api/v2/routines/simplify'],
      ['/api/v2/routines/products/item-1/pause'],
      ['/api/v2/routines/products/item-1/resume'],
      ['/api/v2/routines/products/item-1/prefer'],
      ['/api/v2/routines/products/item-1/unprefer'],
    ]);
    for (const [, body] of post.mock.calls) {
      expect(body).toBeUndefined();
    }
  });
});
