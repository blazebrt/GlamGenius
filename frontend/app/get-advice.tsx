import React from 'react';
import { Redirect } from 'expo-router';

/** Retired advice entry point; occasion styling lives in Style Me. */
export default function GetAdviceRedirect() {
  return <Redirect href="/style-me" />;
}
