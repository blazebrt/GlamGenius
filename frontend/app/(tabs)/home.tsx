import React from 'react';
import { Redirect } from 'expo-router';

// This file is deprecated. 'today' is the new default tab.
export default function HomeScreen() {
  return <Redirect href="/(tabs)/today" />;
}
