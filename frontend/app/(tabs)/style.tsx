import React from 'react';
import { Redirect } from 'expo-router';

/** The retired Style route remains deterministic for legacy deep links. */
export default function StyleRedirect() {
  return <Redirect href="/scan-product" />;
}
