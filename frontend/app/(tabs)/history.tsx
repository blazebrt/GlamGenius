import React from 'react';
import { Redirect } from 'expo-router';

/** Retired history tab kept only for deterministic legacy deep links. */
export default function HistoryRedirect() {
  return <Redirect href="/progress" />;
}
