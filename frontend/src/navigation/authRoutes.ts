import type { RegistrationState } from '../store/userStore';

/**
 * Where sign-in and registration send someone. The one authority for it.
 *
 * The scanner is the product home (see app/index.tsx and the Step 1 shell), so
 * an account that is already registered always lands there. The retired Today
 * tab is not a destination and there is no alias for it.
 *
 * A newly created account is the one exception: it goes to onboarding, once.
 * The code that finalised the registration navigates there itself, and no
 * "registered" effect may send that person to the scanner first.
 */
export const PRODUCT_HOME_ROUTE = '/scan-product' as const;
export const FIRST_REGISTRATION_ROUTE = '/onboarding' as const;
export const REGISTRATION_INCOMPLETE_ROUTE = '/(auth)/registration-incomplete' as const;
export const SIGNED_OUT_ROUTE = '/(auth)/welcome' as const;

export type AuthRoute =
  | typeof PRODUCT_HOME_ROUTE
  | typeof REGISTRATION_INCOMPLETE_ROUTE
  | typeof SIGNED_OUT_ROUTE;

/**
 * The destination for a settled registration state, or null while the
 * current identity is still being resolved. Null means stay where you are:
 * routing on a guess is how a person gets sent to the wrong screen.
 */
export function routeForRegistrationState(state: RegistrationState): AuthRoute | null {
  switch (state) {
    case 'registered':
      return PRODUCT_HOME_ROUTE;
    case 'registration_pending':
      return REGISTRATION_INCOMPLETE_ROUTE;
    case 'signed_out':
      return SIGNED_OUT_ROUTE;
    case 'resolving':
      return null;
  }
}
