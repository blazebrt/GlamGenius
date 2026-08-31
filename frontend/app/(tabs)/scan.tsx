import React from 'react';
import { Redirect } from 'expo-router';

/** Primary tab entry: the scanner is the product home. */
export default function ScanTab() {
  return <Redirect href="/scan-product" />;
}
