import React, { useEffect } from 'react';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { View, ActivityIndicator, Text, StyleSheet } from 'react-native';
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

export default function RootLayout() {
  const { initializeUser } = useUserStore();

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
    initializeUser();
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
        <Stack
          screenOptions={{
            headerShown: false,
            contentStyle: { backgroundColor: COLORS.background },
            animation: 'slide_from_right',
          }}
        >
          <Stack.Screen name="index" />
          <Stack.Screen name="(tabs)" />
          <Stack.Screen name="(auth)/welcome" />
          <Stack.Screen name="scan" options={{ presentation: 'fullScreenModal' }} />
          <Stack.Screen name="style-quiz" />
          <Stack.Screen name="get-advice" />
          <Stack.Screen name="recommendations" />
          <Stack.Screen name="service-details" />
          <Stack.Screen name="subscription" />
        </Stack>
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
