import React, { useEffect } from 'react';
import { Stack } from 'expo-router';
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

// Fire Sentry init before the root component mounts so a rendering error
// inside <RootLayout /> itself is still captured. Safe to call with no
// DSN configured — returns quietly and the app continues.
initSentry();

function RootLayout() {
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
    // Loaded once, before any screen can decide whether to offer a payment.
    // The store fails closed, so a failure here hides paid actions rather than
    // showing them.
    loadConfig().catch((err) => console.warn('load config failed', err));
    // Both action references come from Zustand singleton stores and are
    // stable across renders. This effect must run exactly once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!fontsLoaded) {
    return (
      <View style={styles.loadingContainer}>
        <Text style={styles.loadingLogo}>GlamGenius</Text>
        <Text style={styles.loadingTagline}>SKIN · HAIR · STYLE</Text>
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
            <Stack.Screen name="(tabs)" />
            <Stack.Screen name="(auth)" />
            <Stack.Screen name="scan" options={{ presentation: 'fullScreenModal' }} />
            <Stack.Screen name="style-quiz" />
            <Stack.Screen name="get-advice" />
            <Stack.Screen name="recommendations" />
            <Stack.Screen name="onboarding" />
            <Stack.Screen name="my-appearance" />
            <Stack.Screen name="inventory-add" />
            <Stack.Screen name="inventory-item" />
            <Stack.Screen name="inventory-insights" />
            <Stack.Screen name="event-add" />
            <Stack.Screen name="event-ready" />
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
