import { getInstallationId } from '../services/deviceIdentity';
import { notificationTarget } from '../navigation/notifications';
import AsyncStorage from '@react-native-async-storage/async-storage';

jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(), setItem: jest.fn(), removeItem: jest.fn(),
}));

describe('VC-09 notification boundaries', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (AsyncStorage.getItem as jest.Mock).mockResolvedValue('11111111-1111-4111-8111-111111111111');
  });

  it('routes only allowlisted targets and safely falls back malformed Event Ready data', () => {
    expect(notificationTarget({ destination: '/event-ready', eventId: 'event-1' })).toEqual({ destination: '/event-ready', params: { eventId: 'event-1' } });
    expect(notificationTarget({ destination: '/event-ready' })).toEqual({ destination: '/(tabs)/plan' });
    expect(notificationTarget({ destination: 'https://evil.invalid' })).toEqual({ destination: '/(tabs)/today' });
  });

  it('keeps one stable installation identity', async () => {
    (AsyncStorage.getItem as jest.Mock).mockResolvedValueOnce(null).mockResolvedValueOnce('11111111-1111-4111-8111-111111111111');
    const first = await getInstallationId();
    const second = await getInstallationId();
    expect(first).toMatch(/^[0-9a-f-]{36}$/);
    expect(second).toBe('11111111-1111-4111-8111-111111111111');
  });

});
