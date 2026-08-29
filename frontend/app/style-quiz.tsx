import React from 'react';
import { Redirect } from 'expo-router';

/** Retired quiz entry point; appearance context now lives in My Appearance. */
export default function StyleQuizRedirect() {
  return <Redirect href="/my-appearance" />;
}
