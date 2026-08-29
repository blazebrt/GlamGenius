import React from 'react';
import { Redirect } from 'expo-router';

/** Retired scan entry point; review appearance context in My Appearance. */
export default function ScanRedirect() {
  return <Redirect href="/my-appearance" />;
}
