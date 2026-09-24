/**
 * Neutral session + registration store (§2 hardening spec).
 *
 * Registration state is a first-class value here: a Supabase session on its
 * own is **not** enough to enter the product. The store distinguishes:
 *
 *   'signed_out'             — no Supabase session
 *   'resolving'              — a Supabase session exists, but GlamGenius has
 *                              not yet answered for this identity. It is not
 *                              ``registered`` and not ``registration_pending``;
 *                              screens wait rather than route on it.
 *   'registration_pending'   — Supabase session exists but /api/v2/me says
 *                              REGISTRATION_REQUIRED (typically because the
 *                              user has not presented a reservation challenge
 *                              yet, or their email is unconfirmed)
 *   'registered'             — /api/v2/me returned 200
 *
 * Screens read ``registrationState`` and route accordingly. Screens must
 * never assume that ``session != null`` implies ``registered``.
 *
 * Every write of account-derived state goes through the auth generation
 * (``authGeneration.ts``). A result that arrives after sign-out, after a 401,
 * or after a different account signed in is discarded whole. The Supabase
 * auth callback itself only records the identity and schedules the check;
 * see ``handleAuthStateChange``.
 *
 * The reservation challenge earned by ``/access/reserve`` is held in
 * AsyncStorage under a namespaced key so it survives an app kill between
 * Supabase sign-up and email confirmation.
 */
import { create } from 'zustand';
import type { AuthChangeEvent, Session } from '@supabase/supabase-js';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { secureSessionStorage } from '../services/secureSessionStorage';
import { supabase } from '../services/supabase';
import {
  isRegistrationRequired,
  setAuthResponseAuthority,
  setRegistrationRequiredHandler,
  setUnauthorizedHandler,
} from '../services/api';
import {
  claimScanDevice,
  finalizeRegistration,
  reserveInvite,
  getMe,
  patchAppearanceProfile,
  type MeResponse,
} from '../services/apiV2';
import { markDeviceClaimed, tokenToClaimFor } from '../services/productScan';
import {
  acceptRegistrationFact,
  authAccountId,
  beginRegistrationFact,
  currentAuth,
  enterRegistrationFlow,
  isAuthTicket,
  isCurrentAuth,
  openAuthGeneration,
  registrationFactIsCurrent,
  registrationFlowActive,
  type AuthTicket,
} from './authGeneration';

const CHALLENGE_STORAGE_KEY = '@glamgenius/registration_challenge_v2';

/**
 * Hand this phone's earlier scans to the account whose registration was just
 * accepted, and to no other.
 *
 * It starts only from an accepted, current result, and it checks the ticket
 * again before each step. The claim request itself is bound to the account
 * (``expectedAccountId``), so it is sent as that account or not at all.
 *
 * Deliberately silent on failure: someone signing in should not be stopped
 * because a scan could not be re-filed. It is retried on the next sign-in.
 */
async function claimScanDeviceForAccount(ticket: AuthTicket): Promise<void> {
  const accountId = ticket.accountId;
  if (!accountId || !isCurrentAuth(ticket)) return;
  try {
    const token = await tokenToClaimFor(accountId);
    if (!token || !isCurrentAuth(ticket)) return;
    await claimScanDevice(token, { expectedAccountId: accountId });
    // The server attached the scans to ``accountId``; record exactly that.
    await markDeviceClaimed(accountId);
  } catch {
    // Best effort.
  }
}

export type RegistrationState =
  | 'signed_out'
  | 'resolving'
  | 'registration_pending'
  | 'registered';

export interface UserProfile {
  id: string;
  name?: string;
  email?: string;
  phone?: string;
  age?: number;
  city?: string;
  diet?: string;
  budget_range?: string;
  height_cm?: number;
  body_type?: string;
  style_vibe?: string;
  hair_type?: string;
  skin_type?: string;
  face_shape?: string;
  skin_tone?: string;
  undertone?: string;
  skin_concerns: string[];
  hair_concerns: string[];
  preferences: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

export interface AuthResult {
  ok: boolean;
  code?:
    | 'invalid_credentials'
    | 'email_in_use'
    | 'invite_required'
    | 'invite_invalid'
    | 'reservation_expired'
    | 'email_confirmation_required'
    | 'network'
    | 'unknown';
  message?: string;
  /** True when the Supabase sign-up succeeded but requires email confirmation. */
  needsEmailConfirmation?: boolean;
}

interface UserStore {
  session: Session | null;
  user: UserProfile | null;
  userId: string;
  loading: boolean;
  initialized: boolean;
  registrationState: RegistrationState;
  pendingChallenge: string | null;
  isAdmin: boolean;

  initializeUser: () => Promise<void>;
  hydrateRegistration: () => Promise<void>;
  fetchUser: () => Promise<void>;
  reserveAndRegister: (
    name: string,
    email: string,
    password: string,
    inviteCode: string
  ) => Promise<AuthResult>;
  finishPendingRegistration: () => Promise<AuthResult>;
  login: (email: string, password: string) => Promise<AuthResult>;
  updateUser: (data: Partial<UserProfile>) => Promise<void>;
  updateUserProfile: (data: Partial<UserProfile>) => void;
  logout: () => Promise<void>;
}

const emptyProfile = (id: string, email?: string): UserProfile => ({
  id,
  email,
  skin_concerns: [],
  hair_concerns: [],
  preferences: {},
});

/**
 * Read the reservation challenge, moving it into the keychain if it is still
 * in the old place.
 *
 * The challenge is a bearer secret: presented with the matching email it
 * finalises a registration and spends an invite. It lived in AsyncStorage,
 * an unencrypted file whose only protection is the app sandbox — the same
 * reason the signed-in session and the device token moved to the keychain.
 *
 * Its window is short, which is why this is defence in depth rather than an
 * open door: the reservation expires in thirty minutes, and the challenge is
 * cleared as soon as registration finishes or the server calls it expired. It
 * is the install that starts sign-up and never confirms the email that leaves
 * one sitting there.
 *
 * The migration matters for exactly that case. Someone who signed up, closed
 * the app and is waiting on a confirmation email has their only copy of the
 * challenge in the old location; dropping it would cost them the invite.
 */
async function readStoredChallenge(): Promise<string | null> {
  const secure = await secureSessionStorage.getItem(CHALLENGE_STORAGE_KEY);
  if (secure) return secure;

  let legacy: string | null = null;
  try {
    legacy = await AsyncStorage.getItem(CHALLENGE_STORAGE_KEY);
  } catch {
    return null;
  }
  if (legacy) {
    await writeStoredChallenge(legacy);
    try {
      await AsyncStorage.removeItem(CHALLENGE_STORAGE_KEY);
    } catch {
      // Leaving the old copy behind is worse than not leaving it, but losing
      // the reservation is worse still. The keychain copy is written first.
    }
  }
  return legacy;
}

async function writeStoredChallenge(value: string | null): Promise<void> {
  try {
    if (value) await secureSessionStorage.setItem(CHALLENGE_STORAGE_KEY, value);
    else await secureSessionStorage.removeItem(CHALLENGE_STORAGE_KEY);
  } catch {
    // Non-fatal: worst case the user has to re-reserve.
  }
  if (!value) {
    // Clear any pre-migration copy too, so signing out or finishing
    // registration does not leave the secret in the old place.
    try {
      await AsyncStorage.removeItem(CHALLENGE_STORAGE_KEY);
    } catch {
      // Best effort.
    }
  }
}

// ---------------------------------------------------------------------------
// Identity and registration authority
// ---------------------------------------------------------------------------

/**
 * Make the store describe ``session``'s account, synchronously. Returns true
 * when that is a different identity from the one the store held.
 *
 * The same account (a rotated token, a repeated event) keeps everything it
 * had: a token refresh is not a new person. A different account opens a new
 * auth generation first, so work still in flight for the previous account can
 * no longer write. The previous account's profile, admin flag and
 * registration decision are then replaced with the new account's empty
 * skeleton, built from the new account's own identity, and ``resolving``.
 */
function adoptSession(session: Session): boolean {
  const nextId = session.user.id;
  if (nextId === authAccountId()) {
    useUserStore.setState({ session, userId: nextId });
    return false;
  }
  openAuthGeneration(nextId);
  useUserStore.setState({
    session,
    userId: nextId,
    user: emptyProfile(nextId, session.user.email ?? undefined),
    isAdmin: false,
    registrationState: 'resolving',
  });
  return true;
}

/** Signed out, now. Every ticket issued before this call is stale. */
function clearIdentity(): void {
  openAuthGeneration('');
  useUserStore.setState({
    session: null,
    user: null,
    userId: '',
    registrationState: 'signed_out',
    isAdmin: false,
  });
}

/** Apply an accepted ``/me`` answer to the account in ``ticket``, and only that one. */
function applyMe(ticket: AuthTicket, res: MeResponse): void {
  const me = res.profile;
  // From the server, which is the only thing that decides it. This flag
  // is a routing convenience so an operator can reach the admin screens;
  // every admin endpoint still checks the caller itself, so a client that
  // lied about this would get nothing but 403s.
  const isAdmin = res.account?.is_admin === true;
  if (me && typeof me === 'object') {
    useUserStore.setState({
      // The initiating account, never whoever happens to be current by now.
      user: { ...emptyProfile(ticket.accountId), ...me },
      registrationState: 'registered',
      isAdmin,
    });
  } else {
    useUserStore.setState({ registrationState: 'registered', isAdmin });
  }
}

/** One ``/me`` check for ``ticket``'s account; its answer lands only if it is still current. */
async function resolveRegistration(ticket: AuthTicket): Promise<void> {
  let res: MeResponse;
  try {
    res = await getMe();
  } catch (err) {
    if (isRegistrationRequired(err)) {
      if (acceptRegistrationFact(ticket)) {
        useUserStore.setState({ registrationState: 'registration_pending', isAdmin: false });
      }
    } else if (isCurrentAuth(ticket)) {
      // Not an answer: stay ``resolving`` rather than guess either way.
      console.warn('hydrate registration error', err instanceof Error ? err.message : 'unknown');
    }
    return;
  }
  // Stale: this account signed out or was replaced while the request was out,
  // or a later registration fact already landed. Discard it whole.
  if (!acceptRegistrationFact(ticket)) return;
  applyMe(ticket, res);
  // The phone may have been scanning before anyone signed in. Those scans
  // belong to this person now. Best-effort: a failure here must never stop
  // someone signing in.
  void claimScanDeviceForAccount(ticket);
}

let inflightReconciliation: { generation: number; promise: Promise<void> } | null = null;

/**
 * The single registration reconciliation for the current identity.
 *
 * Callers that ask while a check for the same generation is already out join
 * it instead of sending another ``/me``. A new generation always starts fresh;
 * the old check's answer is discarded when it arrives.
 */
function reconcileRegistration(): Promise<void> {
  const ticket = currentAuth();
  if (!ticket.accountId) return Promise.resolve();
  if (inflightReconciliation && inflightReconciliation.generation === ticket.generation) {
    return inflightReconciliation.promise;
  }
  const fact = beginRegistrationFact(ticket);
  const promise: Promise<void> = resolveRegistration(fact).finally(() => {
    if (inflightReconciliation?.promise === promise) inflightReconciliation = null;
  });
  inflightReconciliation = { generation: ticket.generation, promise };
  return promise;
}

let scheduledGeneration: number | null = null;

/**
 * Run the reconciliation after the Supabase auth callback has returned.
 *
 * Supabase invokes its subscribers while holding its auth lock, and awaits
 * them. ``/me`` goes through the API client, whose request interceptor calls
 * ``supabase.auth.getSession()``, which waits for that same lock. Awaited
 * from inside the callback, the refresh would wait for the callback and the
 * callback for the refresh. A macrotask starts only after the callback has
 * returned, so the check waits for the lock like any other caller instead.
 *
 * One timer per generation. By the time it fires the ticket may be stale (a
 * later event superseded it) or a registration flow may be deciding the
 * account itself; in both cases it does nothing.
 */
function scheduleReconciliation(ticket: AuthTicket): void {
  if (scheduledGeneration === ticket.generation) return;
  scheduledGeneration = ticket.generation;
  setTimeout(() => {
    if (scheduledGeneration === ticket.generation) scheduledGeneration = null;
    if (!isCurrentAuth(ticket) || registrationFlowActive()) return;
    void reconcileRegistration();
  }, 0);
}

/**
 * Once no registration flow is running, an identity still ``resolving`` gets
 * its ordinary check. This is what happens after a registration that failed
 * part-way.
 */
function reconcileIfUnresolved(): void {
  if (registrationFlowActive()) return;
  if (useUserStore.getState().registrationState !== 'resolving') return;
  void reconcileRegistration();
}

/**
 * The Supabase ``onAuthStateChange`` subscriber.
 *
 * Deliberately synchronous, and it returns without touching the network.
 * Supabase awaits its subscribers while holding the auth lock (see
 * ``scheduleReconciliation``). So this may only record the identity it was
 * given, invalidate older work and schedule the check for later. It must
 * never call ``/me``, the API client, ``getAccessToken()``,
 * ``supabase.auth.getSession()`` or the device claim, directly or through an
 * async function's synchronous prefix.
 */
export function handleAuthStateChange(_event: AuthChangeEvent, session: Session | null): void {
  if (!session) {
    if (authAccountId() !== '' || useUserStore.getState().session) clearIdentity();
    return;
  }
  const changed = adoptSession(session);
  // A new identity, or one whose registration is still undecided, needs the
  // check. A token refresh for a settled account does not: it is the same
  // person, and nothing about them has changed.
  if (changed || useUserStore.getState().registrationState === 'resolving') {
    scheduleReconciliation(currentAuth());
  }
}

export const useUserStore = create<UserStore>((set, get) => ({
  session: null,
  user: null,
  userId: '',
  loading: false,
  initialized: false,
  registrationState: 'signed_out',
  pendingChallenge: null,
  isAdmin: false,

  initializeUser: async () => {
    const started = currentAuth();
    try {
      const { data } = await supabase.auth.getSession();
      const session = data.session;
      const stored = await readStoredChallenge();
      set({ pendingChallenge: stored });

      if (currentAuth().generation !== started.generation) {
        // An auth event decided the identity while this was reading storage,
        // and it is newer than what was read here. Only finish its check.
        await reconcileRegistration();
        return;
      }
      if (!session) {
        if (authAccountId() !== '') clearIdentity();
        else set({ session: null, user: null, userId: '', registrationState: 'signed_out' });
        return;
      }
      adoptSession(session);
      await reconcileRegistration();
    } catch (error) {
       
      console.error('Error initializing user:', error);
    } finally {
      set({ initialized: true });
    }
  },

  hydrateRegistration: async () => {
    await reconcileRegistration();
  },

  fetchUser: async () => {
    const ticket = currentAuth();
    if (!ticket.accountId) return;
    set({ loading: true });
    const fact = beginRegistrationFact(ticket);
    try {
      const res = await getMe();
      if (acceptRegistrationFact(fact)) applyMe(fact, res);
    } catch (err) {
      if (isRegistrationRequired(err) && acceptRegistrationFact(fact)) {
        set({ registrationState: 'registration_pending', isAdmin: false });
      }
    } finally {
      set({ loading: false });
    }
  },

  reserveAndRegister: async (name, email, password, inviteCode) => {
    set({ loading: true });
    // From before the Supabase sign-up: its SIGNED_IN event must not start a
    // competing check while this flow finalises the account.
    const leaveFlow = enterRegistrationFlow();
    try {
      // Step 1: reserve the invite BEFORE creating a Supabase identity.
      try {
        const reservation = await reserveInvite(inviteCode.trim(), email.trim());
        await writeStoredChallenge(reservation.challenge);
        set({ pendingChallenge: reservation.challenge });
      } catch (err: any) {
        const detail = err?.response?.data?.detail;
        const code = detail?.code === 'rate_limited' ? 'unknown' : 'invite_invalid';
        return {
          ok: false,
          code,
          message: detail?.message ?? 'This invite cannot be used.',
        };
      }

      // Step 2: Supabase sign-up. The Supabase project may require email
      // confirmation, in which case ``data.session`` will be null and the
      // caller must complete registration after the confirmation deep-link
      // returns.
      const { data, error } = await supabase.auth.signUp({
        email: email.trim(),
        password,
        options: { data: { name } },
      });
      if (error) {
        const msg = error.message || 'Registration failed.';
        return {
          ok: false,
          code: /registered|exists/i.test(msg) ? 'email_in_use' : 'unknown',
          message: msg,
        };
      }
      if (!data.session || !data.user) {
        // Email confirmation required. Keep the reservation challenge in
        // storage so we can finalise once the user completes confirmation.
        return {
          ok: false,
          code: 'email_confirmation_required',
          needsEmailConfirmation: true,
          message:
            'Please confirm your email to finish creating your account. Your invite is held for 30 minutes.',
        };
      }

      // Step 3: finalise. Since Supabase returned a session immediately we
      // can call ``/access/register`` right now.
      adoptSession(data.session);
      return await get().finishPendingRegistration();
    } catch (err) {
       
      console.error('reserveAndRegister error:', err);
      return { ok: false, code: 'network', message: 'Network error.' };
    } finally {
      leaveFlow();
      reconcileIfUnresolved();
      set({ loading: false });
    }
  },

  finishPendingRegistration: async () => {
    const leaveFlow = enterRegistrationFlow();
    try {
      const started = currentAuth();
      const challenge =
        get().pendingChallenge ?? (await readStoredChallenge());
      if (!challenge) {
        return {
          ok: false,
          code: 'invite_required',
          message:
            'Your invite reservation has been lost. Please start again from the sign-up screen.',
        };
      }
      try {
        // Called for its effect: the account row is created server-side. The
        // response body carries nothing this screen needs, but a failure must
        // still reach the catch below, so the await stays.
        await finalizeRegistration(challenge);
        await writeStoredChallenge(null);
        // Taken after finalisation completed, so it is newer than any request
        // that started before the account existed, including a REGISTRATION_REQUIRED
        // still on its way back.
        const fact = beginRegistrationFact(started);
        if (currentAuth().generation !== started.generation) {
          // The identity changed while the account was being created. Nothing
          // about that account may land on whoever is signed in now, and the
          // caller must not take this person to onboarding.
          set({ pendingChallenge: null });
          return { ok: false, code: 'unknown' };
        }
        // The newest registration fact for this identity, so an older
        // REGISTRATION_REQUIRED can no longer overwrite it.
        acceptRegistrationFact(fact);
        set({ pendingChallenge: null, registrationState: 'registered' });
        await get().fetchUser();
        return { ok: true };
      } catch (err: any) {
        const detail = err?.response?.data?.detail;
        const code = detail?.code as string | undefined;
        if (code === 'reservation_expired' || code === 'reservation_invalid') {
          await writeStoredChallenge(null);
          set({ pendingChallenge: null });
          return {
            ok: false,
            code: 'reservation_expired',
            message:
              'Your invite reservation has expired. Please start again with your invite code.',
          };
        }
        return {
          ok: false,
          code: 'unknown',
          message: detail?.message ?? 'Could not finish registration.',
        };
      }
    } finally {
      leaveFlow();
      reconcileIfUnresolved();
    }
  },

  login: async (email, password) => {
    set({ loading: true });
    try {
      const { data, error } = await supabase.auth.signInWithPassword({
        email,
        password,
      });
      if (error) {
        return { ok: false, code: 'invalid_credentials', message: error.message };
      }
      if (!data.session || !data.user) {
        return { ok: false, code: 'unknown', message: 'Sign-in failed.' };
      }
      // Supabase has usually delivered SIGNED_IN already; this is the same
      // identity then, and the check it scheduled is joined, not repeated.
      adoptSession(data.session);
      const ticket = currentAuth();
      await reconcileRegistration();
      if (!isCurrentAuth(ticket)) {
        // Signed out, or replaced by another account, while this was checking.
        return { ok: false, code: 'unknown', message: 'Sign-in failed.' };
      }
      const state = get().registrationState;
      if (state === 'resolving') {
        // The check did not get an answer; this is not a registration verdict.
        return { ok: false, code: 'network', message: 'Network error.' };
      }
      // If the account still isn't registered (rare — a former user who
      // signed up without finishing invite redemption), stay on the
      // registration-pending track. The interceptor already routed the
      // 403 REGISTRATION_REQUIRED response to the correct screen.
      if (state !== 'registered') {
        return {
          ok: false,
          code: 'invite_required',
          message:
            'Your Supabase account is signed in, but your GlamGenius registration is not complete.',
        };
      }
      return { ok: true };
    } catch (err) {
       
      console.error('login error:', err);
      return { ok: false, code: 'network', message: 'Network error.' };
    } finally {
      set({ loading: false });
    }
  },

  updateUser: async (data) => {
    const ticket = currentAuth();
    if (!ticket.accountId) return;
    set({ loading: true });
    try {
      const attributes = Object.entries(data).map(([key, value]) => ({
        key,
        value: value as string | number | string[],
      }));
      await patchAppearanceProfile(attributes);
      // The refreshed profile is only for the account that made the change.
      if (isCurrentAuth(ticket)) await get().fetchUser();
    } catch (err) {
       
      console.error('updateUser error:', err);
    } finally {
      set({ loading: false });
    }
  },

  updateUserProfile: (data) => {
    const { user } = get();
    if (user) set({ user: { ...user, ...data } });
  },

  logout: async () => {
    // First, synchronously: every request still out for this account is stale
    // from here, so none of them can put it back.
    clearIdentity();
    try {
      await supabase.auth.signOut();
    } catch (err) {
       
      console.error('signOut error:', err);
    }
    await writeStoredChallenge(null);
    set({ pendingChallenge: null });
  },
}));

// Sync Zustand with Supabase's own session change events. The subscriber is
// synchronous by design; see ``handleAuthStateChange``.
supabase.auth.onAuthStateChange(handleAuthStateChange);

setUnauthorizedHandler(() => {
  // The same authority as sign-out: a ``/me`` still in flight cannot restore
  // the session after this.
  clearIdentity();
  useUserStore.setState({ pendingChallenge: null });
  void writeStoredChallenge(null);
});

setRegistrationRequiredHandler((stamp) => {
  const ticket = isAuthTicket(stamp) ? stamp : currentAuth();
  if (!acceptRegistrationFact(ticket)) return;
  useUserStore.setState({ registrationState: 'registration_pending', isAdmin: false });
});

setAuthResponseAuthority({
  stamp: () => currentAuth(),
  // A 401 ends the session only if it answered a request made in this
  // generation; an earlier account's 401 must not sign out the current one.
  acceptsUnauthorized: (stamp) =>
    !isAuthTicket(stamp) || stamp.generation === currentAuth().generation,
  // REGISTRATION_REQUIRED moves someone to the registration screen only if it
  // is about them, is not older than the last registration fact, and no
  // registration flow is deciding the account right now.
  acceptsRegistrationRequired: (stamp) =>
    !registrationFlowActive() && (!isAuthTicket(stamp) || registrationFactIsCurrent(stamp)),
});
