import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import { Alert, Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import NotificationsScreen from '../../app/notifications';
import * as apiV2 from '../services/apiV2';

jest.mock('expo-router', () => ({ useRouter: () => ({ back: jest.fn() }) }));
jest.mock('expo-notifications', () => ({
  __esModule: true, AndroidImportance: { DEFAULT: 3 }, setNotificationChannelAsync: jest.fn(), getPermissionsAsync: jest.fn(), requestPermissionsAsync: jest.fn(), getExpoPushTokenAsync: jest.fn(),
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
  setNotificationChannelAsync: jest.Mock; getPermissionsAsync: jest.Mock; requestPermissionsAsync: jest.Mock; getExpoPushTokenAsync: jest.Mock;
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
    (apiV2.getNotificationPreferences as jest.Mock).mockResolvedValue({ preferences, recent: [], current_device_registered: false });
    (apiV2.patchNotificationPreferences as jest.Mock).mockResolvedValue({ preferences });
  });

  it('does not request native permission merely by opening Notifications', async () => {
    render(<NotificationsScreen />);
    await waitFor(() => expect(screen.getByText('Notifications')).toBeTruthy());
    await waitFor(() => expect(apiV2.getNotificationPreferences).toHaveBeenCalledWith('11111111-1111-4111-8111-111111111111'));
    expect(mockNotifications.requestPermissionsAsync).not.toHaveBeenCalled();
  });

  it('requests permission only after explicit native enable and registers the stable device', async () => {
    const originalPlatform = Platform.OS;
    Object.defineProperty(Platform, 'OS', { configurable: true, value: 'android' });
    mockNotifications.getPermissionsAsync.mockResolvedValue({ status: 'denied' });
    mockNotifications.requestPermissionsAsync.mockResolvedValue({ status: 'granted' });
    mockNotifications.getExpoPushTokenAsync.mockResolvedValue({ data: 'ExponentPushToken[test]' });
    (apiV2.registerNotificationDevice as jest.Mock).mockResolvedValue({ native_push_enabled: true, current_device_registered: true });
    render(<NotificationsScreen />);
    await screen.findByText('Native push on this device');
    const toggle = screen.UNSAFE_getByProps({ accessibilityLabel: 'Native push on this device' });
    await waitFor(() => expect(toggle.props.disabled).toBeFalsy());
    await act(async () => { fireEvent(toggle, 'valueChange', true); await Promise.resolve(); });
    await waitFor(() => expect(mockNotifications.requestPermissionsAsync).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(apiV2.registerNotificationDevice).toHaveBeenCalledWith(expect.objectContaining({ device_key: '11111111-1111-4111-8111-111111111111' })));
    expect(toggle.props.value).toBe(true);
    expect(mockNotifications.setNotificationChannelAsync.mock.invocationCallOrder[0]).toBeLessThan(
      mockNotifications.requestPermissionsAsync.mock.invocationCallOrder[0],
    );
    Object.defineProperty(Platform, 'OS', { configurable: true, value: originalPlatform });
  });

  it('does not obtain a token when native permission is denied', async () => {
    mockNotifications.getPermissionsAsync.mockResolvedValue({ status: 'denied' });
    mockNotifications.requestPermissionsAsync.mockResolvedValue({ status: 'denied' });
    render(<NotificationsScreen />);
    await screen.findByText('Native push on this device');
    const toggle = screen.UNSAFE_getByProps({ accessibilityLabel: 'Native push on this device' });
    await act(async () => { fireEvent(toggle, 'valueChange', true); await Promise.resolve(); });
    await waitFor(() => expect(mockNotifications.requestPermissionsAsync).toHaveBeenCalledTimes(1));
    expect(mockNotifications.getExpoPushTokenAsync).not.toHaveBeenCalled();
    expect(apiV2.registerNotificationDevice).not.toHaveBeenCalled();
  });

  it('disabling one of two devices does not disable the account', async () => {
    (apiV2.getNotificationPreferences as jest.Mock).mockResolvedValue({ preferences: { ...preferences, native_push_enabled: true }, recent: [], current_device_registered: true });
    (apiV2.unregisterNotificationDevice as jest.Mock).mockResolvedValue({
      device_key: '11111111-1111-4111-8111-111111111111', removed: true,
      active_devices_remaining: true, native_push_enabled: true,
      current_device_registered: false,
    });
    render(<NotificationsScreen />);
    await screen.findByText('Native push on this device');
    const toggle = screen.UNSAFE_getByProps({ accessibilityLabel: 'Native push on this device' });
    await act(async () => { fireEvent(toggle, 'valueChange', false); await Promise.resolve(); });
    await waitFor(() => expect(apiV2.unregisterNotificationDevice).toHaveBeenCalledWith('11111111-1111-4111-8111-111111111111'));
    expect(apiV2.patchNotificationPreferences).not.toHaveBeenCalled();
    expect(toggle.props.value).toBe(false);
  });

  it('keeps the current-device switch off when only another device is registered', async () => {
    (apiV2.getNotificationPreferences as jest.Mock).mockResolvedValue({
      preferences: { ...preferences, native_push_enabled: true }, recent: [], current_device_registered: false,
    });
    render(<NotificationsScreen />);
    await screen.findByText('Native push on this device');
    const toggle = screen.UNSAFE_getByProps({ accessibilityLabel: 'Native push on this device' });
    await waitFor(() => expect(toggle.props.value).toBe(false));
  });

  it('renders the current-device switch on when this device is registered', async () => {
    (apiV2.getNotificationPreferences as jest.Mock).mockResolvedValue({
      preferences: { ...preferences, native_push_enabled: true }, recent: [], current_device_registered: true,
    });
    render(<NotificationsScreen />);
    const toggle = await screen.findByLabelText('Native push on this device');
    await waitFor(() => expect(toggle.props.value).toBe(true));
  });

  it('turns off the last device without an account-wide patch', async () => {
    (apiV2.getNotificationPreferences as jest.Mock).mockResolvedValue({
      preferences: { ...preferences, native_push_enabled: true }, recent: [], current_device_registered: true,
    });
    (apiV2.unregisterNotificationDevice as jest.Mock).mockResolvedValue({
      device_key: '11111111-1111-4111-8111-111111111111', removed: true,
      active_devices_remaining: false, native_push_enabled: false, current_device_registered: false,
    });
    render(<NotificationsScreen />);
    const toggle = await screen.findByLabelText('Native push on this device');
    await waitFor(() => expect(toggle.props.value).toBe(true));
    await act(async () => { fireEvent(toggle, 'valueChange', false); await Promise.resolve(); });
    await waitFor(() => expect(toggle.props.value).toBe(false));
    expect(apiV2.patchNotificationPreferences).not.toHaveBeenCalled();
  });

  it('preserves the current-device switch when unregister fails', async () => {
    const alertSpy = jest.spyOn(Alert, 'alert').mockImplementation(() => undefined);
    (apiV2.getNotificationPreferences as jest.Mock).mockResolvedValue({
      preferences: { ...preferences, native_push_enabled: true }, recent: [], current_device_registered: true,
    });
    (apiV2.unregisterNotificationDevice as jest.Mock).mockRejectedValue(new Error('offline'));
    render(<NotificationsScreen />);
    const toggle = await screen.findByLabelText('Native push on this device');
    await waitFor(() => expect(toggle.props.value).toBe(true));
    await act(async () => { fireEvent(toggle, 'valueChange', false); await Promise.resolve(); });
    await waitFor(() => expect(alertSpy).toHaveBeenCalledWith('Could not save', 'Notifications remain unchanged.'));
    expect(toggle.props.value).toBe(true);
  });

  it('renders truthful recent notification history', async () => {
    (apiV2.getNotificationPreferences as jest.Mock).mockResolvedValue({ preferences, current_device_registered: false, recent: [
      { id: '1', title: 'Care reminder', status: 'provider_accepted', suppressed_reason: null },
      { id: '2', title: 'Style reminder', status: 'suppressed', suppressed_reason: 'quiet_hours' },
    ] });
    render(<NotificationsScreen />);
    await waitFor(() => expect(screen.getByText('Care reminder')).toBeTruthy());
    expect(screen.getByText('Sent')).toBeTruthy();
    expect(screen.getByText('Not sent — quiet hours')).toBeTruthy();
  });
});
