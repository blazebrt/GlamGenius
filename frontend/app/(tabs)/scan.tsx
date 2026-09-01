import React from 'react';
import { Redirect } from 'expo-router';

/** The canonical primary-tab entry always returns to the one scanner. */
export default function ScanTab() {
  return <Redirect href="/scan-product" />;
}
