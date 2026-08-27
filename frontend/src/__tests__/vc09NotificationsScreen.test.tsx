import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import NotificationsScreen from '../../app/notifications';
import * as apiV2 from '../services/apiV2';

jest.mock('expo-router', () => ({ useRouter: () => ({ back: jest.fn() }) }));
jest.mock('expo-notifications', () => ({
  __esModule: true, getPermissionsAsync: jest.fn(), requestPermissionsAsync: jest.fn(), getExpoPushTokenAsync: jest.fn(),
}));
jest.mock('expo-constants', () => ({ __esModule: true, default: { expoConfig: { extra: { eas: { projectId: 'project-1' } } } } }));
jest.mock('../services/apiV2', () => ({
  getNotificationPreferences: jest.fn(), patchNotificationPreferences: jest.fn(),
  registerNotificationDevice: jest.fn(), unregisterNotificationDevice: jest.fn(),
}));
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(), setItem: jest.fn(), removeItem: jest.fn(),
}));

const mockNotifications = jest.requireMock('expo-notifications') as {
  getPermissionsAsync: jest.Mock; requestPermissionsAsync: jest.Mock; getExpoPushTokenAsync: jest.Mock;
};

const preferences = {
  enabled: true, native_push_enabled: false, preferred_hour: 9,
  quiet_hours: { start: 21, end: 7 }, modules: {},
  topics: { today_style: true, care: true, event_preparation: true, maintenance: true },
};

describe('VC-09 Notifications screen', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (AsyncStorage.getItem as jest.Mock).mockResolvedValue('11111111-1111-4111-8111-111111111111');
    (apiV2.getNotificationPreferences as jest.Mock).mockResolvedValue({ preferences });
    (apiV2.patchNotificationPreferences as jest.Mock).mockResolvedValue({ preferences });
  });

  it('does not request native permission merely by opening Notifications', async () => {
    render(<NotificationsScreen />);
    await waitFor(() => expect(screen.getByText('Notifications')).toBeTruthy());
    expect(mockNotifications.requestPermissionsAsync).not.toHaveBeenCalled();
  });

  it('requests permission only after explicit native enable and registers the stable device', async () => {
    mockNotifications.getPermissionsAsync.mockResolvedValue({ status: 'denied' });
    mockNotifications.requestPermissionsAsync.mockResolvedValue({ status: 'granted' });
    mockNotifications.getExpoPushTokenAsync.mockResolvedValue({ data: 'ExponentPushToken[test]' });
    (apiV2.registerNotificationDevice as jest.Mock).mockResolvedValue({ native_push_enabled: true });
    render(<NotificationsScreen />);
    const toggle = await screen.findByRole('switch', { name: 'Native push on this device' });
    await waitFor(() => expect(toggle.props.disabled).toBeFalsy());
    await act(async () => {
      toggle.props.onChange({ nativeEvent: { value: true } });
      await Promise.resolve();
    });
    await waitFor(() => expect(mockNotifications.requestPermissionsAsync).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(apiV2.registerNotificationDevice).toHaveBeenCalledWith(expect.objectContaining({ device_key: '11111111-1111-4111-8111-111111111111' })));
  });
});
