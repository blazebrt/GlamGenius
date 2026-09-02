import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';
import { Linking } from 'react-native';

import { OfficialRecords } from '../components/verdict/OfficialRecords';

const records = {
  authority: 'FSSAI / FoSCoS', record_type: 'food_recall' as const,
  source_url: 'https://foscos.fssai.gov.in/food-recall', last_successful_check_at: '2026-08-02T00:00:00+00:00',
  records: [{ recall_id: 'SYN-RECALL-002', recall_status: 'Published', recall_start_date: '2026-08-01', reason: 'Reason from the official record.', source_url: 'https://foscos.fssai.gov.in/food-recall', match_state: 'matched' as const }],
};

describe('official records on the canonical verdict surface', () => {
  it('renders an exact official record with accessible authority, identity and status', () => {
    render(<OfficialRecords officialRecords={records} />);
    expect(screen.getByText('Official FSSAI record')).toBeTruthy();
    expect(screen.getByText(/SYN-RECALL-002/)).toBeTruthy();
    expect(screen.getByLabelText(/Official FSSAI record.*SYN-RECALL-002.*Published/)).toBeTruthy();
  });

  it('opens the supplied official source and renders nothing when there is no exact record', () => {
    const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(true);
    render(<OfficialRecords officialRecords={records} />);
    fireEvent.press(screen.getByLabelText('Open official FSSAI record'));
    expect(open).toHaveBeenCalledWith('https://foscos.fssai.gov.in/food-recall');
    open.mockRestore();
    const view = render(<OfficialRecords officialRecords={{ ...records, records: [] }} />);
    expect(view.toJSON()).toBeNull();
  });
});
