import React from 'react';
import { Redirect } from 'expo-router';

// Legacy Home remains routable but the scanner is the only customer home.
export default function HomeScreen() {
  return <Redirect href="/scan-product" />;
}
