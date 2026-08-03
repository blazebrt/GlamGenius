import React from 'react';
import { Tabs } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { View, StyleSheet, Platform } from 'react-native';
import { COLORS, FONTS } from '../../src/theme/colors';

/**
 * Primary navigation: Today · Inventory · Style Me · Planner · You.
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
      <Tabs.Screen
        name="today"
        options={{
          title: 'Today',
          tabBarIcon: ({ color }) => <Ionicons name="sunny-outline" size={22} color={color} />,
        }}
      />
      <Tabs.Screen
        name="inventory"
        options={{
          title: 'Inventory',
          tabBarIcon: ({ color }) => <Ionicons name="archive-outline" size={22} color={color} />,
        }}
      />
      <Tabs.Screen
        name="style-me-tab"
        options={{
          title: 'Style Me',
          tabBarIcon: () => (
            <View style={styles.centreButton}>
              <Ionicons name="sparkles" size={24} color={COLORS.white} />
            </View>
          ),
          tabBarLabel: () => null,
        }}
      />
      <Tabs.Screen
        name="planner"
        options={{
          title: 'Planner',
          tabBarIcon: ({ color }) => <Ionicons name="calendar-outline" size={22} color={color} />,
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          title: 'You',
          tabBarIcon: ({ color }) => <Ionicons name="person-outline" size={22} color={color} />,
        }}
      />

      {/* Still routable, just not tabs. */}
      <Tabs.Screen name="home" options={{ href: null }} />
      <Tabs.Screen name="scan-tab" options={{ href: null }} />
      <Tabs.Screen name="history" options={{ href: null }} />
      <Tabs.Screen name="services" options={{ href: null }} />
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
  centreButton: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: COLORS.primary,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 20,
  },
});
