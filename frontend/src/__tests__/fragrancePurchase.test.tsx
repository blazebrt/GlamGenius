import React from 'react';
import { render, screen } from '@testing-library/react-native';
import { FragranceShoppingResult } from '../components/shopping/FragranceShoppingPieces';

describe('Fragrance Purchase routing', () => {
  it('renders customer-facing context, owned alternatives and missing information without Style purchase UI', () => {
    const check = {
      fragrance_purchase_check_version: 'v3-05.9' as const, strategy: 'fragrance_purchase' as const, candidate_truth: { candidate: { id: 'test' } },
      collection_context: { owned_options_to_use_first: [{ owned_item_id: 'owned-1', display_name: 'Office Floral', brand: 'House', remaining_percent: 60 }], same_family_owned: [{ owned_item_id: 'owned-2', display_name: 'Woody Reserve' }], coverage: { covered: ['business_meeting'], unknown: [], uncovered: [] } },
      verdict: { fragrance_purchase_verdict_version: 'v3-05.9' as const, verdict: 'buy' as const, missing_information: ['draft_owned_context'] }
    } as any;
    render(<FragranceShoppingResult check={check} onReset={jest.fn()} onDecide={jest.fn()} />); 
    expect(screen.getByLabelText('Fragrance intended use')).toBeTruthy(); 
    expect(screen.getByLabelText('Owned fragrance alternatives')).toBeTruthy(); 
    expect(screen.getByLabelText('Same family supporting information')).toBeTruthy(); 
    expect(screen.getByText('Some perfume entries still need confirmation before they can count as owned.')).toBeTruthy(); 
    expect(screen.queryByText(/ROI|ingredients|routine|score/i)).toBeNull();
  });
});
