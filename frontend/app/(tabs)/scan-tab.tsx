import React from 'react';
import { Redirect } from 'expo-router';

/** Retired tab kept only for deterministic legacy deep links. */
export default function ScanTabRedirect() {
  return <Redirect href="/my-appearance" />;
}
