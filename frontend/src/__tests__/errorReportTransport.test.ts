/**
 * Where a label-error report is actually sent.
 *
 * The endpoint authenticates the device, not the account: the person who
 * notices a wrong number on a shelf is often not signed in. Sending these
 * through the account client failed for want of the header and, for anybody
 * who *was* signed in, the shared 401 interceptor signed them out on the way.
 */
import { makeReport, submitReport } from '../services/errorReports';
import { postDeviceForm } from '../services/productScan';
import { api } from '../services/api';

jest.mock('../services/productScan', () => ({
  postDeviceForm: jest.fn(() => Promise.resolve()),
}));

jest.mock('../services/api', () => ({
  api: { post: jest.fn(() => Promise.resolve({ data: {} })) },
}));

jest.mock('@react-native-async-storage/async-storage', () => {
  const store: Record<string, string> = {};
  return {
    getItem: jest.fn((k: string) => Promise.resolve(k in store ? store[k] : null)),
    setItem: jest.fn((k: string, v: string) => { store[k] = v; return Promise.resolve(); }),
    removeItem: jest.fn((k: string) => { delete store[k]; return Promise.resolve(); }),
  };
});

describe('a label-error report', () => {
  beforeEach(() => jest.clearAllMocks());

  it('goes out as the device, never through the account client', async () => {
    const report = makeReport({
      barcode: '8901234567890',
      subject: 'sugar',
      reason: 'wrong_number',
      photo_uri: null,
    });

    const sent = await submitReport(report);

    expect(sent).toBe(true);
    expect(postDeviceForm).toHaveBeenCalledTimes(1);
    expect(api.post).not.toHaveBeenCalled();

    const [path, form] = (postDeviceForm as jest.Mock).mock.calls[0];
    expect(path).toContain('/reports/label-error');
    expect(form).toBeInstanceOf(FormData);
  });

  it('is kept when it cannot be sent, so a shop with no signal loses nothing', async () => {
    (postDeviceForm as jest.Mock).mockRejectedValueOnce(new Error('offline'));
    const report = makeReport({
      barcode: null, subject: 'grade', reason: 'wrong_grade', photo_uri: null,
    });

    expect(await submitReport(report)).toBe(false);
  });
});
