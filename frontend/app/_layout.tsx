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
import { COLORS, FONTS } from '../src/theme/colors';
import { useUserStore } from '../src/store/userStore';

export default function RootLayout() {
  const { initializeUser } = useUserStore();
  
  const [fontsLoaded] = useFonts({
    // Premium Editorial Serif for Headings
    PlayfairDisplay_400Regular,
    PlayfairDisplay_500Medium,
    PlayfairDisplay_700Bold,
    // Geometric Sans for Body & Data
    Inter_400Regular,
    Inter_500Medium,
    Inter_600SemiBold,
    Inter_700Bold,
  });

  // Initialize user from AsyncStorage on app start
  useEffect(() => {
    initializeUser();
  }, []);

  if (!fontsLoaded) {
    return (
      <View style={styles.loadingContainer}>
        <View style={styles.loadingContent}>
          <Text style={styles.loadingLogo}>GlamGenius</Text>
          <Text style={styles.loadingTagline}>MEDICAL BEAUTY</Text>
          <ActivityIndicator size="large" color={COLORS.primary} style={{ marginTop: 24 }} />
        </View>
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
          <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
          <Stack.Screen name="scan" options={{ presentation: 'fullScreenModal' }} />
          <Stack.Screen name="style-quiz" />
          <Stack.Screen name="get-advice" />
          <Stack.Screen name="quiz" />
          <Stack.Screen name="recommendations" />
          <Stack.Screen name="service-details" />
          <Stack.Screen name="cart" />
          <Stack.Screen name="checkout" />
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
  loadingContent: {
    alignItems: 'center',
  },
  loadingLogo: {
    fontSize: 36,
    fontWeight: '700',
    color: COLORS.textPrimary,
    letterSpacing: -0.5,
  },
  loadingTagline: {
    fontSize: 12,
    color: COLORS.primary,
    letterSpacing: 3,
    marginTop: 8,
  },
});
