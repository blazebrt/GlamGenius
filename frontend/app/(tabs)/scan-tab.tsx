import React from 'react';
import { Redirect } from 'expo-router';

/** Legacy tab path now returns to the canonical scanner. */
export default function ScanTabRedirect() {
  return <Redirect href="/scan-product" />;
}
