import React, { useEffect } from 'react';
import { Stack, useRouter } from 'expo-router';
import * as Notifications from 'expo-notifications';
import { StatusBar } from 'expo-status-bar';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { View, ActivityIndicator, Text, StyleSheet, Platform } from 'react-native';
import {
  useFonts,
  PlayfairDisplay_400Regular,
  PlayfairDisplay_500Medium,
  PlayfairDisplay_700Bold,
} from '@expo-google-fonts/playfair-display';
import {
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
  Inter_700Bold,
} from '@expo-google-fonts/inter';
import { COLORS } from '../src/theme/colors';
import { useUserStore } from '../src/store/userStore';
import { useConfigStore } from '../src/store/configStore';
import { ErrorBoundary } from '../src/components/ErrorBoundary';
import { initSentry, wrapRoot } from '../src/monitoring';
import { notificationTarget } from '../src/navigation/notifications';

// Fire Sentry init before the root component mounts so a rendering error
// inside <RootLayout /> itself is still captured. Safe to call with no
// DSN configured — returns quietly and the app continues.
initSentry();

function RootLayout() {
  const router = useRouter();
  const { initializeUser } = useUserStore();
  const loadConfig = useConfigStore((s) => s.load);

  const [fontsLoaded] = useFonts({
    PlayfairDisplay_400Regular,
    PlayfairDisplay_500Medium,
    PlayfairDisplay_700Bold,
    Inter_400Regular,
    Inter_500Medium,
    Inter_600SemiBold,
    Inter_700Bold,
  });

  useEffect(() => {
    initializeUser().catch((err) => console.warn('init user failed', err));
    // Loaded once so all screens share the same server-authoritative config.
    loadConfig().catch((err) => console.warn('load config failed', err));
    // Both action references come from Zustand singleton stores and are
    // stable across renders. This effect must run exactly once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (Platform.OS === 'web' || typeof Notifications.addNotificationResponseReceivedListener !== 'function') return undefined;
    const open = (response: Notifications.NotificationResponse) => {
      const target = notificationTarget(response.notification.request.content.data);
      if (target.params) router.push({ pathname: target.destination, params: target.params });
      else router.push(target.destination as never);
    };
    const subscription = Notifications.addNotificationResponseReceivedListener(open);
    // Expo retains the response that launched a cold app. Consume it once so
    // a tap is not lost before the listener is attached.
    void Notifications.getLastNotificationResponseAsync?.().then((response) => { if (response) open(response); }).catch(() => undefined);
    return () => subscription.remove();
  }, [router]);

  if (!fontsLoaded) {
    return (
      <View style={styles.loadingContainer}>
        <Text style={styles.loadingLogo}>GlamGenius</Text>
        <Text style={styles.loadingTagline}>YOUR APPEARANCE · ORGANISED</Text>
        <ActivityIndicator size="large" color={COLORS.primary} style={{ marginTop: 24 }} />
      </View>
    );
  }

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <StatusBar style="dark" />
        <ErrorBoundary
          onReset={() => {
            if (Platform.OS === 'web' && typeof window !== 'undefined') {
              window.location.href = '/';
            }
          }}
        >
          <Stack
            screenOptions={{
              headerShown: false,
              contentStyle: { backgroundColor: COLORS.background },
              animation: Platform.OS === 'web' ? 'none' : 'slide_from_right',
            }}
          >
            <Stack.Screen name="index" />
            {/* The scanner is the launch screen: camera first, no account. */}
            <Stack.Screen name="scan-product" options={{ animation: 'none' }} />
            <Stack.Screen name="intro" />
            <Stack.Screen name="(tabs)" />
            <Stack.Screen name="(auth)" />
            <Stack.Screen name="scan" options={{ presentation: 'fullScreenModal' }} />
            <Stack.Screen name="onboarding" />
            <Stack.Screen name="my-appearance" />
            <Stack.Screen name="inventory-add" />
            {/* One shelf photo, one tap per thing on it. */}
            <Stack.Screen name="inventory-batch" options={{ presentation: 'fullScreenModal' }} />
            <Stack.Screen name="inventory-item" />
            <Stack.Screen name="inventory-insights" />
            <Stack.Screen name="event-add" />
            <Stack.Screen name="event-ready" />
            <Stack.Screen name="notifications" />
            <Stack.Screen name="admin-knowledge" />
          </Stack>
        </ErrorBoundary>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: COLORS.background,
  },
  loadingLogo: {
    fontSize: 36,
    fontWeight: '700',
    color: COLORS.textPrimary,
  },
  loadingTagline: {
    fontSize: 12,
    color: COLORS.primary,
    letterSpacing: 3,
    marginTop: 8,
  },
});

// Wrap the root so React render errors reach Sentry when a DSN is
// configured. When there is no DSN this returns the component unchanged.
export default wrapRoot(RootLayout);
