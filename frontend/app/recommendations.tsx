import React from 'react';
import { Redirect } from 'expo-router';

/** Retired recommendation entry point; occasion styling lives in Style Me. */
export default function RecommendationsRedirect() {
  return <Redirect href="/style-me" />;
}
