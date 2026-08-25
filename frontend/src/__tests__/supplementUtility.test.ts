import { api } from '../services/api';
import {
  confirmSupplementLabelFact,
  createSupplementLabelFact,
  deleteSupplementLabelFact,
  getSupplementLabelFacts,
  getSupplementUtility,
  patchSupplementLabelFact,
  routeSupplementQuestion,
} from '../services/apiV2';

jest.mock('../services/api', () => ({ api: { delete: jest.fn(), get: jest.fn(), patch: jest.fn(), post: jest.fn() } }));

describe('VC-07 supplement utility API', () => {
  beforeEach(() => jest.clearAllMocks());

  it('uses account-scoped label-fact routes and preserves package amounts', async () => {
    (api.get as jest.Mock).mockResolvedValue({ data: { label_facts: [] } });
    (api.post as jest.Mock).mockResolvedValue({ data: { id: 'fact-1', amount: '500', unit: 'mg' } });
    (api.patch as jest.Mock).mockResolvedValue({ data: { id: 'fact-1' } });
    (api.delete as jest.Mock).mockResolvedValue({ data: { deleted: true, id: 'fact-1' } });

    await getSupplementLabelFacts('item-1');
    await createSupplementLabelFact('item-1', { raw_name: 'Vitamin C', amount: '500', unit: 'mg', serving_text: 'Per tablet' });
    await patchSupplementLabelFact('item-1', 'fact-1', { amount: '250', unit: 'mg' });
    await confirmSupplementLabelFact('item-1', 'fact-1');
    await deleteSupplementLabelFact('item-1', 'fact-1');

    expect(api.get).toHaveBeenCalledWith('/api/v2/supplements/items/item-1/label-facts');
    expect(api.post).toHaveBeenCalledWith('/api/v2/supplements/items/item-1/label-facts', expect.objectContaining({ raw_name: 'Vitamin C', amount: '500' }));
    expect(api.patch).toHaveBeenCalledWith('/api/v2/supplements/items/item-1/label-facts/fact-1', { amount: '250', unit: 'mg' });
    expect(api.post).toHaveBeenCalledWith('/api/v2/supplements/items/item-1/label-facts/fact-1/confirm');
    expect(api.delete).toHaveBeenCalledWith('/api/v2/supplements/items/item-1/label-facts/fact-1');
  });

  it('keeps the utility summary and professional boundary separate from advice', async () => {
    (api.get as jest.Mock).mockResolvedValue({ data: { utility_version: 'vc-07-v1', overlaps: [] } });
    (api.post as jest.Mock).mockResolvedValue({ data: { boundary: true, message: 'Please speak with a qualified professional.' } });
    await getSupplementUtility();
    await routeSupplementQuestion('Can I take this with my medicine?');
    expect(api.get).toHaveBeenCalledWith('/api/v2/supplements/summary');
    expect(api.post).toHaveBeenCalledWith('/api/v2/supplements/professional-boundary', { question: 'Can I take this with my medicine?' });
    expect(JSON.stringify((api.post as jest.Mock).mock.results)).not.toContain('recommended dose');
  });
});
