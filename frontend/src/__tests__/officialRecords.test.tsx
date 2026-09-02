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

  it('separates when this record was observed from when the source was last checked', () => {
    // A record still listed in the newest export says so plainly.
    render(<OfficialRecords officialRecords={{
      ...records,
      records: [{ ...records.records[0], source_last_seen_at: '2026-08-02T00:00:00+00:00', seen_in_latest_successful_check: true }],
    }} />);
    expect(screen.getByText('Record observed in latest checked FSSAI export')).toBeTruthy();
    expect(screen.getByText(/Official records last checked: 2026-08-02/)).toBeTruthy();
  });

  it('dates a record missing from the latest export without characterising its absence', () => {
    render(<OfficialRecords officialRecords={{
      ...records,
      records: [{ ...records.records[0], source_last_seen_at: '2026-08-01T00:00:00+00:00', seen_in_latest_successful_check: false }],
    }} />);
    // Absence from one download is not withdrawal, and the copy never says it is.
    expect(screen.getByText(/Record last observed in FSSAI export: 2026-08-01/)).toBeTruthy();
    expect(screen.getByText(/Official records last checked: 2026-08-02/)).toBeTruthy();
    expect(screen.queryByText('Record observed in latest checked FSSAI export')).toBeNull();
    for (const forbidden of [/withdrawn/i, /cleared/i, /no longer/i, /resolved/i, /safe now/i, /disappeared/i]) {
      expect(screen.queryByText(forbidden)).toBeNull();
    }
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
