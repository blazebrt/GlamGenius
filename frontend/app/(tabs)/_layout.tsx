import React from 'react';
import { Tabs } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { StyleSheet, Platform } from 'react-native';
import { COLORS, FONTS } from '../../src/theme/colors';
import { LEGACY_HIDDEN_TAB_ROUTES } from '../../src/navigation/finalIA';

/**
 * The scanner is the primary product surface. Account/profile is the only
 * secondary destination in this shell; retained domain routes stay hidden.
 *
 * Home and Today are one screen now — opening the app should answer "what do I
 * wear today", not present a menu. The skin check, salon ideas and history are
 * still routes, reached from Today and from You rather than from a tab, because
 * five tabs is the most a thumb can hit reliably.
 */
export default function TabLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarStyle: styles.tabBar,
        tabBarActiveTintColor: COLORS.primary,
        tabBarInactiveTintColor: COLORS.textMuted,
        tabBarLabelStyle: styles.tabBarLabel,
      }}
    >
      <Tabs.Screen name="scan" options={{ title: 'Scan', tabBarIcon: ({ color }) => <Ionicons name="barcode-outline" size={22} color={color} /> }} />
      <Tabs.Screen
        name="you"
        options={{
          title: 'You',
          tabBarIcon: ({ color }) => <Ionicons name="person-outline" size={22} color={color} />,
        }}
      />

      {(['today', 'style', 'care', 'plan'] as const).map((name) => <Tabs.Screen key={name} name={name} options={{ href: null }} />)}

      {/* Compatibility routes remain routable but are never primary tabs. */}
      {LEGACY_HIDDEN_TAB_ROUTES.map((name) => <Tabs.Screen key={name} name={name} options={{ href: null }} />)}
    </Tabs>
  );
}

const styles = StyleSheet.create({
  tabBar: {
    backgroundColor: COLORS.background,
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
    height: Platform.OS === 'ios' ? 88 : 64,
    paddingBottom: Platform.OS === 'ios' ? 28 : 8,
    paddingTop: 8,
  },
  tabBarLabel: {
    fontSize: 11,
    fontFamily: FONTS.family.bodyMedium,
  },
});
