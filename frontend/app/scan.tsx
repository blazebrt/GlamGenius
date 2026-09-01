import React from 'react';
import { Redirect } from 'expo-router';

/** Legacy scan entry point now returns to the scanner. */
export default function ScanRedirect() {
  return <Redirect href="/scan-product" />;
}
