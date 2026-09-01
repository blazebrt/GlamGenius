import React from 'react';
import { Redirect } from 'expo-router';

/** Retired appearance route: keep legacy links deterministic and scan-first. */
export default function MyAppearanceRedirect() {
  return <Redirect href="/scan-product" />;
}
