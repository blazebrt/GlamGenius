import React from 'react';
import { Redirect } from 'expo-router';

/**
 * The former appearance questionnaire is retired. Preserve the route so an
 * old link opens the scanner rather than reviving appearance collection.
 */
export default function OnboardingRedirect() {
  return <Redirect href="/scan-product" />;
}
