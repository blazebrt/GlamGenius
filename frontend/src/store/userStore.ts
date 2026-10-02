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
import { beginDeviceRemovalForEndedSession } from '../services/logoutDeviceCleanup';
import {
  acceptRegistrationFact,
  authAccountId,
  beginExplicitSignIn,
  beginRegistrationFact,
  currentAuth,
  enterRegistrationFlow,
  enterSignUpRegistrationFlow,
  isAuthTicket,
  isCurrentAuth,
  isQuarantinedSession,
  openAuthGeneration,
  predatesExplicitSignIn,
  quarantineSession,
  registrationFactIsCurrent,
  registrationFlowOwns,
  releaseSessionQuarantine,
  requestDispatch,
  sessionKeyOf,
  type AuthTicket,
  type RegistrationFlow,
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
 *
 * So the old copy is deleted only once the keychain is proven to hold the same
 * value: written, read back, compared (``setItemVerified``). A write that
 * failed, or that reads back as anything else, leaves the old copy where it
 * is; this call still answers with it, and the next read tries again. Nothing
 * here treats "the write did not throw" as "the write landed".
 */
async function readStoredChallenge(): Promise<string | null> {
  const secure = await secureSessionStorage.getItem(CHALLENGE_STORAGE_KEY);
  if (secure) {
    // Only an older build ever wrote the old location, so with a readable
    // keychain copy any value left there is stale. Removing it is safe, and a
    // secret that has been migrated should not stay in an unencrypted file.
    try {
      await AsyncStorage.removeItem(CHALLENGE_STORAGE_KEY);
    } catch {
      // Best effort; the next read tries again.
    }
    return secure;
  }

  let legacy: string | null = null;
  try {
    legacy = await AsyncStorage.getItem(CHALLENGE_STORAGE_KEY);
  } catch {
    return null;
  }
  if (legacy && (await secureSessionStorage.setItemVerified(CHALLENGE_STORAGE_KEY, legacy))) {
    try {
      await AsyncStorage.removeItem(CHALLENGE_STORAGE_KEY);
    } catch {
      // The keychain has it; the old copy goes on the next read.
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
function adoptSession(session: Session, options: { explicit?: boolean } = {}): boolean {
  // Whatever session is adopted now is not the one the app ended: Supabase
  // holds this one instead, or someone signed in on purpose.
  releaseSessionQuarantine();
  const nextId = session.user.id;
  if (nextId === authAccountId()) {
    const previous = useUserStore.getState().session;
    if (options.explicit || isNewSessionOfSameAccount(previous, session)) {
      // The same person, but a new session: work and responses tied to the
      // one it replaces lose their authority. The profile and registration
      // state belong to the account and stay as they are.
      openAuthGeneration(nextId);
    }
    useUserStore.setState({ session, userId: nextId });
    return false;
  }
  // The email lets a sign-up flow waiting for this account bind to it now.
  openAuthGeneration(nextId, session.user.email);
  useUserStore.setState({
    session,
    userId: nextId,
    user: emptyProfile(nextId, session.user.email ?? undefined),
    isAdmin: false,
    registrationState: 'resolving',
  });
  return true;
}

/**
 * Whether ``next`` is a different Supabase session from ``previous`` for the
 * same account: their ``session_id`` claims differ. A token refresh keeps the
 * claim, so it is never a new session.
 *
 * When either token cannot be read, an automatic event is treated as the same
 * session. That fails closed: at worst a stale 401 from before could still end
 * the session, which signs the person out; it never acts across accounts. A
 * deliberate sign-in does not depend on this (see ``beginExplicitSignIn``).
 */
function isNewSessionOfSameAccount(previous: Session | null, next: Session): boolean {
  const before = sessionKeyOf(previous?.access_token);
  const after = sessionKeyOf(next.access_token);
  return before !== null && after !== null && before !== after;
}

/**
 * Whether a 401 rejected the session that is current now, and so may end it.
 *
 * A 401 rejects the bearer token its request presented, not whatever session
 * happens to be current when it lands. It speaks for the current session only
 * if it is the same account and the same Supabase session: equal
 * ``session_id``, however the token has been refreshed since. When the
 * session cannot be read on either side, the session epoch decides: the
 * request must have been made in the current generation.
 */
function rejectsCurrentSession(
  sent: { accountId: string; accessToken: string },
  ticket: AuthTicket | null,
): boolean {
  const current = useUserStore.getState().session;
  if (!current || current.user.id !== sent.accountId || authAccountId() !== sent.accountId) return false;
  const sentKey = sessionKeyOf(sent.accessToken);
  const currentKey = sessionKeyOf(current.access_token);
  if (sentKey !== null && currentKey !== null) return sentKey === currentKey;
  return !ticket || ticket.generation === currentAuth().generation;
}

/**
 * Whether a session Supabase reports is one the app ended itself (an accepted
 * 401, or sign-out) merely continuing: a token refresh, a recovered or initial
 * session, a user update. Such a session is not taken back automatically.
 */
function isEndedSession(session: Session): boolean {
  return isQuarantinedSession(session.user.id, session.access_token);
}

/**
 * End the session locally, now: quarantine it, then sign out.
 *
 * ``rejected`` is the credential a request was sent with, when a 401 rejected
 * it; otherwise the store's own session is the one ended. A 401 only gets here
 * when it rejected the current session (``rejectsCurrentSession``), so both
 * name the same session. Supabase may keep that session while its own
 * sign-out runs, or for good if the sign-out fails. The quarantine means none
 * of its automatic events can sign it back in.
 */
function endSessionLocally(rejected?: { accountId: string; accessToken: string } | null): void {
  const session = useUserStore.getState().session;
  const ended = rejected ?? (session ? { accountId: session.user.id, accessToken: session.access_token } : null);
  if (ended) quarantineSession(ended.accountId, ended.accessToken);
  clearIdentity();
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
 * later event superseded it), or a registration flow owned by this same
 * identity may be deciding the account itself; in both cases it does nothing.
 * A flow owned by any other identity, current or stale, does not hold it back.
 */
function scheduleReconciliation(ticket: AuthTicket): void {
  if (scheduledGeneration === ticket.generation) return;
  scheduledGeneration = ticket.generation;
  setTimeout(() => {
    if (scheduledGeneration === ticket.generation) scheduledGeneration = null;
    if (!isCurrentAuth(ticket) || registrationFlowOwns(ticket)) return;
    void reconcileRegistration();
  }, 0);
}

/**
 * End a registration flow. If the identity it owned is still current, no other
 * flow owns it, and it is still ``resolving``, that identity now gets the check
 * its own flow held back. This is what happens after a registration that
 * failed part-way.
 *
 * A flow that has gone stale ends without touching anything: the identity
 * that replaced its owner was never held back by it and reconciles on its own.
 */
function endRegistrationFlow(flow: RegistrationFlow): void {
  flow.leave();
  const owner = flow.owner();
  if (!owner || !isCurrentAuth(owner) || registrationFlowOwns(owner)) return;
  if (useUserStore.getState().registrationState !== 'resolving') return;
  void reconcileRegistration();
}

/** The answer to a registration whose identity changed while it was out. */
const staleRegistration = (): AuthResult => ({ ok: false, code: 'unknown' });

/**
 * Forget a reservation challenge, but only if it is still ``challenge``.
 *
 * The challenge storage is one slot per phone. A registration that finishes
 * after another identity has taken over may only clear the challenge it spent
 * itself, never one the newer identity has put there since.
 */
async function clearChallengeIfStill(challenge: string): Promise<void> {
  if (useUserStore.getState().pendingChallenge === challenge) {
    useUserStore.setState({ pendingChallenge: null });
  }
  let stored: string | null = null;
  try {
    stored = await secureSessionStorage.getItem(CHALLENGE_STORAGE_KEY);
  } catch {
    stored = null;
  }
  if (stored === null || stored === challenge) await writeStoredChallenge(null);
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
    // Supabase holds no session any more, so the one ended locally cannot
    // continue. Signed out is also a decision: at start-up it settles who
    // requests made before it belong to.
    releaseSessionQuarantine();
    if (authAccountId() !== '' || useUserStore.getState().session || currentAuth().generation === 0) {
      clearIdentity();
    }
    return;
  }
  // The event name cannot tell a new sign-in from a continuation: Supabase
  // sends SIGNED_IN when it recovers a stored session too. The session can: a
  // refresh keeps its session_id, and a new sign-in gets a new one.
  if (isEndedSession(session)) return;
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
      if (!session || isEndedSession(session)) {
        if (authAccountId() !== '' || currentAuth().generation === 0) clearIdentity();
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
    // competing check while this flow finalises the account. The flow owns
    // nothing yet. It binds to the identity Supabase opens for this email,
    // synchronously, inside that SIGNED_IN, and holds back nobody else.
    const flow = enterSignUpRegistrationFlow(email);
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
      // Normally already bound, by the SIGNED_IN event. If Supabase announced
      // nothing, or kept the same account, bind to the identity it returned.
      flow.bind(currentAuth());
      return await get().finishPendingRegistration();
    } catch (err) {
       
      console.error('reserveAndRegister error:', err);
      return { ok: false, code: 'network', message: 'Network error.' };
    } finally {
      endRegistrationFlow(flow);
      set({ loading: false });
    }
  },

  finishPendingRegistration: async () => {
    // The account this finalisation is for. It is fixed here, and nothing
    // below may land on, or be sent as, any other account.
    const owner = currentAuth();
    const flow = owner.accountId ? enterRegistrationFlow(owner) : null;
    try {
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
      if (!flow || !isCurrentAuth(owner)) {
        // No signed-in account started this, or it has already been replaced.
        // Nothing is sent: finalisation is only ever made as its own account.
        return owner.accountId
          ? staleRegistration()
          : { ok: false, code: 'unknown', message: 'Could not finish registration.' };
      }
      try {
        // Called for its effect: the account row is created server-side. The
        // response body carries nothing this screen needs, but a failure must
        // still reach the catch below, so the await stays. Account-bound: it is
        // sent with the owner's own token or not at all.
        await finalizeRegistration(challenge, { expectedAccountId: owner.accountId });
      } catch (err: any) {
        if (!isCurrentAuth(owner)) return staleRegistration();
        const detail = err?.response?.data?.detail;
        const code = detail?.code as string | undefined;
        if (code === 'reservation_expired' || code === 'reservation_invalid') {
          await clearChallengeIfStill(challenge);
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
      // The owner's account now exists and its challenge is spent. Clear that
      // challenge, and only that one, whoever is signed in by now.
      await clearChallengeIfStill(challenge);
      // Taken after finalisation completed, so it is newer than any request
      // that started before the account existed, including a REGISTRATION_REQUIRED
      // still on its way back.
      const fact = beginRegistrationFact(owner);
      if (!isCurrentAuth(owner)) {
        // The identity changed while the account was being created. Nothing
        // about that account may land on whoever is signed in now, and the
        // caller must not take this person to onboarding.
        return staleRegistration();
      }
      // The newest registration fact for this identity, so an older
      // REGISTRATION_REQUIRED can no longer overwrite it.
      acceptRegistrationFact(fact);
      set({ registrationState: 'registered' });
      await get().fetchUser();
      return { ok: true };
    } finally {
      if (flow) endRegistrationFlow(flow);
    }
  },

  login: async (email, password) => {
    set({ loading: true });
    // A deliberate sign-in: from here, nothing sent under the session held
    // until now has any say over the session this sign-in establishes, even
    // if their tokens cannot be told apart.
    beginExplicitSignIn();
    let adopted = false;
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
      // Supabase has usually delivered SIGNED_IN already. Adopting it here, as
      // an explicit sign-in, still opens its own session epoch, so a response
      // to a request made under an earlier session of the same account cannot
      // end this one.
      adoptSession(data.session, { explicit: true });
      adopted = true;
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
      // A sign-in that established nothing leaves whoever was current as they
      // were. Starting it opened a new generation, which set aside any check
      // that identity had in flight, so one still undecided gets it again.
      if (!adopted && authAccountId() !== '' && get().registrationState === 'resolving') {
        void reconcileRegistration();
      }
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
      // Account-bound: this person's attributes are sent as this person or
      // not at all, never as whoever has signed in since.
      await patchAppearanceProfile(attributes, { expectedAccountId: ticket.accountId });
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
    // The one credential that may still remove this phone's notification
    // device for the account signing out, read before anything changes. It
    // lives only in this call: never stored, queued or logged.
    const signingOut = get().session?.access_token ?? null;
    // First, synchronously: every request still out for this account is stale
    // from here, so none of them can put it back. The session is quarantined
    // as well, so if Supabase's sign-out below is slow or fails, its own token
    // refresh cannot sign this account back in.
    endSessionLocally();
    // Then, best effort, ask the server to remove this installation's device
    // for that account only. Only a local read happens before the request is
    // handed to the network; nothing below waits for its answer, so a slow,
    // failing or offline cleanup never delays or undoes the sign-out.
    try {
      void (await beginDeviceRemovalForEndedSession(signingOut)).settled;
    } catch {
      // Never fatal: logout has already happened.
    }
    try {
      // Supabase reports some failures as ``{ error }`` rather than throwing.
      // Either way the local sign-out stands; nothing here waits on it.
      const result = await supabase.auth.signOut();
      if (result?.error) console.error('signOut error:', result.error.message ?? result.error);
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

setUnauthorizedHandler((rejected) => {
  // The same authority as sign-out: a ``/me`` still in flight cannot restore
  // the session after this, and neither can Supabase's own events for the
  // session the server just rejected.
  endSessionLocally(rejected);
  useUserStore.setState({ pendingChallenge: null });
  void writeStoredChallenge(null);
});

setRegistrationRequiredHandler((stamp) => {
  const ticket = isAuthTicket(stamp) ? stamp : currentAuth();
  if (!acceptRegistrationFact(ticket)) return;
  useUserStore.setState({ registrationState: 'registration_pending', isAdmin: false });
});

/** A request made before the store existed began before anything was decided. */
const BEFORE_ANY_DECISION: AuthTicket = { generation: 0, accountId: '', fact: 0 };

setAuthResponseAuthority({
  stamp: () => currentAuth(),
  // A request goes out as the identity it began under, anonymously if it began
  // signed out, or not at all (see ``requestDispatch``).
  dispatchAs: (stamp, sessionAccountId) =>
    requestDispatch(isAuthTicket(stamp) ? stamp : BEFORE_ANY_DECISION, sessionAccountId),
  // A 401 ends the session only if it rejected the session that is current
  // now. A request made before a deliberate sign-in began has no say over what
  // that sign-in established. A request sent with a credential must have been
  // sent as the current session (see ``rejectsCurrentSession``). Otherwise the
  // request must belong to the current generation, so an earlier account's or
  // an earlier session's 401 cannot sign out the current one.
  acceptsUnauthorized: (stamp, sentAs) => {
    const ticket = isAuthTicket(stamp) ? stamp : null;
    if (ticket && predatesExplicitSignIn(ticket)) return false;
    if (sentAs) return rejectsCurrentSession(sentAs, ticket);
    return !ticket || ticket.generation === currentAuth().generation;
  },
  // REGISTRATION_REQUIRED moves someone to the registration screen only if it
  // is about them, is not older than the last registration fact, and no
  // registration flow owned by that same identity is deciding it right now.
  // A flow that belongs to any other identity has no say.
  acceptsRegistrationRequired: (stamp) =>
    isAuthTicket(stamp)
      ? registrationFactIsCurrent(stamp) && !registrationFlowOwns(stamp)
      : !registrationFlowOwns(currentAuth()),
});
