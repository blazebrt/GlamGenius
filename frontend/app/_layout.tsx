import React from 'react';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';

export default function RootLayout() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <StatusBar style="light" />
        <Stack
          screenOptions={{
            headerShown: false,
            contentStyle: { backgroundColor: '#0A0A0A' },
            animation: 'slide_from_right',
          }}
        >
          <Stack.Screen name="index" />
          <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
          <Stack.Screen name="scan" options={{ presentation: 'modal' }} />
          <Stack.Screen name="quiz" />
          <Stack.Screen name="recommendations" />
          <Stack.Screen name="service-details" />
        </Stack>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
