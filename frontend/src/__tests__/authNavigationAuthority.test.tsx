/**
 * Where sign-in and registration send someone.
 *
 * The scanner is the product home. An account that is already registered
 * lands there, from password login, from the auth callback and from the
 * registration screen. A newly created account goes to onboarding, exactly
 * once, and nothing sends it to the scanner first. The retired Today tab is
 * not a destination anywhere.
 *
 * The static checks read the route files themselves, so a rename cannot
 * quietly point sign-in at a screen that no longer exists.
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'fs';
import { join } from 'path';
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react-native';

const mockRouter = {
  replace: jest.fn(),
  push: jest.fn(),
  back: jest.fn(),
  canGoBack: jest.fn(() => false),
};

jest.mock('expo-router', () => ({
  useRouter: () => mockRouter,
  router: mockRouter,
  Redirect: () => null,
}));

jest.mock('../services/supabase', () => ({
  supabase: {
    auth: {
      onAuthStateChange: jest.fn(() => ({ data: { subscription: { unsubscribe: jest.fn() } } })),
      getSession: jest.fn(async () => ({ data: { session: null }, error: null })),
      signInWithPassword: jest.fn(),
      signUp: jest.fn(),
      signOut: jest.fn(async () => ({ error: null })),
      resetPasswordForEmail: jest.fn(),
    },
  },
  getAccessToken: jest.fn(async () => null),
  getAccessIdentity: jest.fn(async () => null),
  signOut: jest.fn(async () => undefined),
}));

jest.mock('../services/apiV2', () => ({
  getMe: jest.fn(),
  finalizeRegistration: jest.fn(),
  reserveInvite: jest.fn(),
  claimScanDevice: jest.fn(),
  patchAppearanceProfile: jest.fn(),
  getReservationStats: jest.fn(async () => ({ totals: { total: 0, active: 0, consumed: 0, expired: 0 } })),
}));

/* eslint-disable import/first */
import AuthCallback from '../../app/(auth)/callback';
import AuthWelcome from '../../app/(auth)/welcome';
import RegistrationIncomplete from '../../app/(auth)/registration-incomplete';
import AdminDashboard from '../../app/admin';
import { useUserStore, type AuthResult } from '../store/userStore';
import {
  FIRST_REGISTRATION_ROUTE,
  PRODUCT_HOME_ROUTE,
  REGISTRATION_INCOMPLETE_ROUTE,
  SIGNED_OUT_ROUTE,
  routeForRegistrationState,
} from '../navigation/authRoutes';
/* eslint-enable import/first */

const appRoot = join(__dirname, '..', '..', 'app');

/** The file expo-router serves for a route, or null if there is none. */
function routeFile(route: string): string | null {
  const segments = route.replace(/^\//, '').split('/');
  for (const extension of ['.tsx', '.ts']) {
    const candidate = join(appRoot, ...segments) + extension;
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

function appSources(dir = appRoot): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return appSources(path);
    return /\.(tsx?|jsx?)$/.test(name) ? [path] : [];
  });
}

const replaced = () => mockRouter.replace.mock.calls.map((call) => call[0]);

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const originalActions = {
  login: useUserStore.getState().login,
  reserveAndRegister: useUserStore.getState().reserveAndRegister,
  finishPendingRegistration: useUserStore.getState().finishPendingRegistration,
  logout: useUserStore.getState().logout,
};

beforeEach(() => {
  jest.clearAllMocks();
  useUserStore.setState({
    ...originalActions,
    initialized: true,
    session: null,
    user: null,
    userId: '',
    registrationState: 'signed_out',
    pendingChallenge: null,
    isAdmin: false,
    loading: false,
  });
});

// ---------------------------------------------------------------------------
// The route authority, statically
// ---------------------------------------------------------------------------
describe('the canonical route authority', () => {
  it('points every post-auth destination at a route file that exists', () => {
    for (const route of [
      PRODUCT_HOME_ROUTE,
      FIRST_REGISTRATION_ROUTE,
      REGISTRATION_INCOMPLETE_ROUTE,
      SIGNED_OUT_ROUTE,
    ]) {
      expect(routeFile(route)).not.toBeNull();
    }
    expect(PRODUCT_HOME_ROUTE).toBe('/scan-product');
    expect(FIRST_REGISTRATION_ROUTE).toBe('/onboarding');
  });

  it('keeps the retired Today tab retired: no file, no alias, no reference', () => {
    expect(existsSync(join(appRoot, '(tabs)', 'today.tsx'))).toBe(false);
    expect(routeFile('/today')).toBeNull();
    expect(routeFile('/(tabs)/today')).toBeNull();
    const offenders = appSources().filter((path) => /\(tabs\)\/today|['"`]\/today['"`]/.test(readFileSync(path, 'utf8')));
    expect(offenders).toEqual([]);
  });

  it('routes the auth screens through the authority, not literals', () => {
    for (const screenFile of ['callback.tsx', 'welcome.tsx', 'registration-incomplete.tsx']) {
      const source = readFileSync(join(appRoot, '(auth)', screenFile), 'utf8');
      expect(source).toContain("from '../../src/navigation/authRoutes'");
      expect(source).not.toMatch(/router\.replace\('\/(scan-product|onboarding)'\)/);
    }
  });

  it('maps each settled registration state, and waits while resolving', () => {
    expect(routeForRegistrationState('registered')).toBe('/scan-product');
    expect(routeForRegistrationState('registration_pending')).toBe('/(auth)/registration-incomplete');
    expect(routeForRegistrationState('signed_out')).toBe('/(auth)/welcome');
    expect(routeForRegistrationState('resolving')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// The auth callback
// ---------------------------------------------------------------------------
describe('the auth callback', () => {
  it('2. sends a registered account to the scanner', () => {
    useUserStore.setState({ registrationState: 'registered' });
    render(<AuthCallback />);
    expect(replaced()).toEqual(['/scan-product']);
  });

  it('5. sends a registration-required identity to registration-incomplete', () => {
    useUserStore.setState({ registrationState: 'registration_pending' });
    render(<AuthCallback />);
    expect(replaced()).toEqual(['/(auth)/registration-incomplete']);
  });

  it('6. sends a signed-out callback to welcome', () => {
    useUserStore.setState({ registrationState: 'signed_out' });
    render(<AuthCallback />);
    expect(replaced()).toEqual(['/(auth)/welcome']);
  });

  it('stays on "Finishing sign-in…" while the identity is resolving, then routes once', () => {
    useUserStore.setState({ registrationState: 'resolving' });
    render(<AuthCallback />);
    expect(screen.getByTestId('auth-callback')).toBeTruthy();
    expect(replaced()).toEqual([]);

    act(() => useUserStore.setState({ registrationState: 'registered' }));
    expect(replaced()).toEqual(['/scan-product']);
  });

  it('does nothing before the store has initialised', () => {
    useUserStore.setState({ initialized: false, registrationState: 'signed_out' });
    render(<AuthCallback />);
    expect(replaced()).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Password login and new registration from the welcome screen
// ---------------------------------------------------------------------------
describe('the welcome screen', () => {
  // Each field is typed in its own render pass (fireEvent wraps each in act),
  // so the submit handler sees the values, as it would for a real person.
  async function fillAndSubmit(fields: Record<string, string>) {
    for (const [testID, value] of Object.entries(fields)) {
      fireEvent.changeText(screen.getByTestId(testID), value);
    }
    await act(async () => {
      fireEvent.press(screen.getByTestId('auth-submit'));
    });
  }

  it('1. sends a registered login to the scanner', async () => {
    const login = jest.fn(async (): Promise<AuthResult> => ({ ok: true }));
    useUserStore.setState({ login });
    render(<AuthWelcome />);
    await fillAndSubmit({ 'auth-email-input': 'a@example.com', 'auth-password-input': 'secret' });
    expect(login).toHaveBeenCalledWith('a@example.com', 'secret');
    expect(replaced()).toEqual(['/scan-product']);
  });

  it('5. sends a login that needs registration to registration-incomplete', async () => {
    useUserStore.setState({ login: jest.fn(async (): Promise<AuthResult> => ({ ok: false, code: 'invite_required' })) });
    render(<AuthWelcome />);
    await fillAndSubmit({ 'auth-email-input': 'a@example.com', 'auth-password-input': 'secret' });
    expect(replaced()).toEqual(['/(auth)/registration-incomplete']);
  });

  it('4. sends a newly created account to onboarding', async () => {
    useUserStore.setState({
      reserveAndRegister: jest.fn(async (): Promise<AuthResult> => ({ ok: true })),
    });
    render(<AuthWelcome />);
    fireEvent.press(screen.getByTestId('auth-switch'));
    await fillAndSubmit({
      'auth-name-input': 'New Person',
      'auth-invite-input': 'INVITE1',
      'auth-email-input': 'new@example.com',
      'auth-password-input': 'secret',
    });
    expect(replaced()).toEqual(['/onboarding']);
  });
});

// ---------------------------------------------------------------------------
// The registration-incomplete screen
// ---------------------------------------------------------------------------
describe('the registration-incomplete screen', () => {
  it('3. sends an account that is already registered to the scanner', () => {
    useUserStore.setState({
      session: { user: { id: 'account-a' } } as never,
      registrationState: 'registered',
    });
    render(<RegistrationIncomplete />);
    expect(replaced()).toEqual(['/scan-product']);
  });

  it('4. finishing a registration goes to onboarding exactly once, with no scanner redirect', async () => {
    const finalised = deferred<AuthResult>();
    const finishPendingRegistration = jest.fn(async () => {
      // The store reports "registered" before the finish returns, exactly as
      // the real finalisation does. That state change must not win the race.
      useUserStore.setState({ registrationState: 'registered' });
      return finalised.promise;
    });
    useUserStore.setState({
      session: { user: { id: 'account-a' } } as never,
      registrationState: 'registration_pending',
      pendingChallenge: 'challenge-1',
      finishPendingRegistration,
    });
    render(<RegistrationIncomplete />);
    expect(replaced()).toEqual([]);

    await act(async () => {
      fireEvent.press(screen.getByTestId('registration-finish'));
    });
    expect(useUserStore.getState().registrationState).toBe('registered');
    expect(replaced()).toEqual([]);

    await act(async () => {
      finalised.resolve({ ok: true });
    });
    expect(replaced()).toEqual(['/onboarding']);
  });

  it('a signed-out visitor is sent to welcome', () => {
    useUserStore.setState({ session: null, registrationState: 'signed_out' });
    render(<RegistrationIncomplete />);
    expect(replaced()).toEqual(['/(auth)/welcome']);
  });

  it('stays put while the identity is resolving', () => {
    useUserStore.setState({
      session: { user: { id: 'account-a' } } as never,
      registrationState: 'resolving',
    });
    render(<RegistrationIncomplete />);
    expect(replaced()).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// The admin guard no longer points at the retired tab
// ---------------------------------------------------------------------------
describe('the admin guard', () => {
  it('sends a registered non-admin to the scanner', () => {
    useUserStore.setState({ registrationState: 'registered', isAdmin: false });
    render(<AdminDashboard />);
    expect(replaced()).toEqual(['/scan-product']);
  });

  it('waits, rather than signing anyone out, while the identity is resolving', () => {
    useUserStore.setState({ registrationState: 'resolving', isAdmin: false });
    render(<AdminDashboard />);
    expect(replaced()).toEqual([]);
  });
});
