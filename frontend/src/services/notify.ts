import { Alert, Platform } from 'react-native';

const IS_WEB = Platform.OS === 'web';

export type NotifyButton = {
  text: string;
  onPress?: () => void;
  style?: 'default' | 'cancel' | 'destructive';
};

/**
 * Show a message to the user, on a phone or in a browser.
 *
 * React Native's Alert.alert does nothing at all on web — no dialog, no error,
 * it just silently returns. So a screen that uses it directly shows web users
 * nothing when something goes wrong. This routes web to the browser's own
 * dialogs and leaves phones on the native alert.
 */
export function notify(title: string, message: string, buttons?: NotifyButton[]) {
  if (!IS_WEB) {
    Alert.alert(title, message, buttons as any);
    return;
  }

  if (!buttons?.length) {
    window.alert(`${title}\n\n${message}`);
    return;
  }

  const action = buttons.find((b) => b.style !== 'cancel') || buttons[0];
  if (buttons.length === 1) {
    window.alert(`${title}\n\n${message}`);
    action?.onPress?.();
    return;
  }

  if (window.confirm(`${title}\n\n${message}`)) {
    action?.onPress?.();
  }
}
