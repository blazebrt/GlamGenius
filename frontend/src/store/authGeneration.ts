/**
 * The auth generation: the one answer to "is this asynchronous result still
 * about the person who is signed in now?"
 *
 * Anything that starts while one identity is signed in and finishes after an
 * `await` can outlive that identity: the person signs out, a 401 ends the
 * session, or a different account signs in. A result that arrives after that
 * must change nothing: no profile, no registration state, no admin flag and no
 * device claim.
 *
 * So every identity transition opens a new generation, and asynchronous work
 * carries the ticket it started with. That covers sign-out, session
 * invalidation, and A to B. Before the work writes anything account-derived it
 * asks `isCurrentAuth(ticket)`: the same generation and the same account, or
 * nothing.
 *
 * Registration facts need one more rule, because they can race inside a single
 * identity. A `/me` that started before an invite was finalised can still
 * answer REGISTRATION_REQUIRED after the account exists. Facts are therefore
 * ordered by when their request started, and a fact from an older request
 * never overwrites one from a newer request.
 *
 * An invite registration in progress decides its own account's registration
 * state, so it holds back that account's background check and routing while
 * it runs. That authority belongs to one identity, never to the app: a flow is
 * owned by the generation and account it started for, and it has no say over
 * any other identity, including one that signs in while it is still running.
 *
 * Requests keep the identity they began under (``requestDispatch``). The API
 * client stamps each request the moment it is made. When its token is ready,
 * the request goes out as that same account, anonymously if it began signed
 * out, or not at all. A later token for the same account is fine; any other
 * account's is not.
 *
 * A session the app has ended itself stays ended (``quarantineSession``).
 * After an accepted 401 or a sign-out, Supabase can keep that session for a
 * while, or for good if its own sign-out fails. Its automatic events (a token
 * refresh, a recovered session) must not bring the account back. Only a new
 * session, or an explicit sign-in, can.
 *
 * This module holds no React or Zustand state; the user store is its only
 * writer.
 */

export interface AuthTicket {
  /** Changes whenever the signed-in identity changes, sign-out included. */
  readonly generation: number;
  /** The account the generation belongs to; '' when signed out. */
  readonly accountId: string;
  /** Registration-fact order: a request that started later has a larger number. */
  readonly fact: number;
}

let generation = 0;
let accountId = '';
let factClock = 0;
let appliedFact = 0;

/** The identity a registration flow decides. */
interface FlowOwner {
  readonly generation: number;
  readonly accountId: string;
}

interface FlowRecord {
  owner: FlowOwner | null;
  /** A sign-up flow started before its account existed: the email it is creating. */
  readonly signUpEmail: string | null;
}

let nextFlowId = 0;
const liveFlows = new Map<number, FlowRecord>();

const normalizeEmail = (email: string | null | undefined): string =>
  (email ?? '').trim().toLowerCase();

/** The identity that is current right now, as a ticket. */
export function currentAuth(): AuthTicket {
  return { generation, accountId, fact: factClock };
}

/** The account the current generation belongs to ('' when signed out). */
export function authAccountId(): string {
  return accountId;
}

/**
 * A new identity, or none. Every ticket issued before this call is stale from
 * the moment it returns, so work still in flight can no longer write.
 *
 * ``email`` is the new identity's own address. A sign-up flow waiting for the
 * account it is creating binds to it here, in the same synchronous step that
 * opens the generation. So no check scheduled for that identity can ever see
 * it unowned, whatever order timers and promises then run in.
 */
export function openAuthGeneration(nextAccountId: string, email?: string | null): AuthTicket {
  generation += 1;
  accountId = nextAccountId;
  const address = normalizeEmail(email);
  if (nextAccountId && address) {
    for (const flow of liveFlows.values()) {
      if (flow.owner === null && flow.signUpEmail === address) {
        flow.owner = { generation, accountId: nextAccountId };
      }
    }
  }
  return currentAuth();
}

/** Same generation and same, signed-in account. */
export function isCurrentAuth(ticket: AuthTicket): boolean {
  return (
    ticket.accountId !== '' &&
    ticket.generation === generation &&
    ticket.accountId === accountId
  );
}

/**
 * A ticket for a request whose answer will decide registration state. Call it
 * immediately before the request is made.
 */
export function beginRegistrationFact(from: AuthTicket): AuthTicket {
  factClock += 1;
  return { generation: from.generation, accountId: from.accountId, fact: factClock };
}

/**
 * Whether a registration fact may be applied, and if so record that it was.
 *
 * It must be about the current identity. No fact from a request that started
 * after it may have been applied already.
 */
export function acceptRegistrationFact(ticket: AuthTicket): boolean {
  if (!registrationFactIsCurrent(ticket)) return false;
  appliedFact = ticket.fact;
  return true;
}

/** ``acceptRegistrationFact`` without recording anything: may this fact still land? */
export function registrationFactIsCurrent(ticket: AuthTicket): boolean {
  return isCurrentAuth(ticket) && ticket.fact >= appliedFact;
}

/** One invite registration in progress, and the identity it holds authority over. */
export interface RegistrationFlow {
  /**
   * Bind a flow that has no owner yet to ``ticket``'s identity. A flow that is
   * already bound keeps its owner: it is never moved to another identity.
   */
  bind(ticket: AuthTicket): void;
  /**
   * The identity this flow decides, or null while it has none. Its ``fact`` is
   * 0: it identifies the identity and is not a registration fact.
   */
  owner(): AuthTicket | null;
  /** Give up the flow's authority. Idempotent, and it affects this flow only. */
  leave(): void;
}

function openFlow(record: FlowRecord): RegistrationFlow {
  nextFlowId += 1;
  const id = nextFlowId;
  liveFlows.set(id, record);
  return {
    bind(ticket) {
      if (record.owner !== null || !ticket.accountId) return;
      record.owner = { generation: ticket.generation, accountId: ticket.accountId };
    },
    owner() {
      return record.owner ? { ...record.owner, fact: 0 } : null;
    },
    leave() {
      liveFlows.delete(id);
    },
  };
}

/**
 * An invite registration for ``ticket``'s identity, bound from the start.
 *
 * While it runs, that flow alone decides that identity's registration state.
 * Its background check does not start, and a REGISTRATION_REQUIRED from a
 * request that raced the finalisation cannot move the person off the path to
 * onboarding. It holds no authority over any other identity: once another
 * account signs in, or this one signs out, it is simply stale.
 */
export function enterRegistrationFlow(ticket: AuthTicket): RegistrationFlow {
  const flow = openFlow({ owner: null, signUpEmail: null });
  flow.bind(ticket);
  return flow;
}

/**
 * A sign-up that starts before its account exists.
 *
 * It owns nothing until the identity for ``email`` is opened, and binds to that
 * identity alone, synchronously, as it is opened (see ``openAuthGeneration``).
 * Unbound, it holds nothing back for anyone.
 */
export function enterSignUpRegistrationFlow(email: string): RegistrationFlow {
  return openFlow({ owner: null, signUpEmail: normalizeEmail(email) || null });
}

/** Whether a live registration flow owns exactly ``ticket``'s generation and account. */
export function registrationFlowOwns(ticket: AuthTicket): boolean {
  if (!ticket.accountId) return false;
  for (const flow of liveFlows.values()) {
    const owner = flow.owner;
    if (owner && owner.generation === ticket.generation && owner.accountId === ticket.accountId) {
      return true;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// Request identity
// ---------------------------------------------------------------------------

/** How a request may be sent: with the session's token, without one, or not at all. */
export type RequestDispatch = 'session' | 'anonymous' | 'refuse';

/**
 * How a request that began under ``stamp`` may be sent, now that its token is
 * ready and the session it would use belongs to ``sessionAccountId`` (null:
 * Supabase holds no session).
 *
 * - Began as an account: it goes out only as that same account, and only if
 *   no identity transition has happened since. A refreshed token for the same
 *   account is fine; another account's token, or none, is refused. Nothing is
 *   ever sent anonymously in its place.
 * - Began signed out: it stays anonymous. Whoever has signed in since, it
 *   does not take their token.
 * - Began before the app had decided who is signed in (generation 0, while
 *   the stored session is still being read at start-up): it belongs to the
 *   first identity the app then settles on, and to no later one.
 */
export function requestDispatch(stamp: AuthTicket, sessionAccountId: string | null): RequestDispatch {
  let started: AuthTicket = stamp;
  if (stamp.generation === 0) {
    // Nothing decided yet: the stored session is the one the app is adopting.
    if (generation === 0) return sessionAccountId ? 'session' : 'anonymous';
    // Decided exactly once since: that first decision is whom it began for.
    if (generation !== 1) return 'refuse';
    started = currentAuth();
  }
  if (!started.accountId) return 'anonymous';
  if (!isCurrentAuth(started)) return 'refuse';
  return sessionAccountId === started.accountId ? 'session' : 'refuse';
}

// ---------------------------------------------------------------------------
// Sessions ended locally
// ---------------------------------------------------------------------------

interface QuarantinedSession {
  readonly accountId: string;
  /** The session's ``session_id``; null when its token could not be read. */
  readonly sessionKey: string | null;
}

let quarantined: QuarantinedSession | null = null;

/** Decode base64url without relying on a platform ``atob``. */
function decodeBase64Url(input: string): string | null {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
  let bits = 0;
  let value = 0;
  const bytes: number[] = [];
  for (const char of input.replace(/=+$/, '')) {
    const index = alphabet.indexOf(char === '+' ? '-' : char === '/' ? '_' : char);
    if (index < 0) return null;
    value = (value << 6) | index;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      bytes.push((value >> bits) & 0xff);
    }
  }
  try {
    return decodeURIComponent(bytes.map((byte) => `%${byte.toString(16).padStart(2, '0')}`).join(''));
  } catch {
    return null;
  }
}

/**
 * The Supabase session an access token belongs to: its ``session_id`` claim.
 * A token refresh keeps it; a new sign-in gets a new one. Null when the token
 * is not a readable JWT; callers then fall back to the account alone.
 */
export function sessionKeyOf(accessToken: string | null | undefined): string | null {
  const payload = accessToken?.split('.')[1];
  if (!payload) return null;
  const json = decodeBase64Url(payload);
  if (!json) return null;
  try {
    const claims = JSON.parse(json) as { session_id?: unknown };
    return typeof claims.session_id === 'string' && claims.session_id ? claims.session_id : null;
  } catch {
    return null;
  }
}

/**
 * The app has ended ``accountId``'s session locally (an accepted 401, or
 * sign-out). Until Supabase itself reports it gone, or a different session is
 * adopted, events that merely continue it are not a sign-in.
 */
export function quarantineSession(accountId: string, accessToken: string | null | undefined): void {
  if (!accountId) return;
  quarantined = { accountId, sessionKey: sessionKeyOf(accessToken) };
}

/**
 * Whether a session Supabase reports is the continuation of the one ended
 * locally: the same account and the same ``session_id``. When either token
 * cannot be read, the account alone decides; only an explicit sign-in or
 * Supabase's own SIGNED_OUT then lifts it.
 */
export function isQuarantinedSession(accountId: string, accessToken: string | null | undefined): boolean {
  if (!quarantined || quarantined.accountId !== accountId) return false;
  const key = sessionKeyOf(accessToken);
  if (quarantined.sessionKey === null || key === null) return true;
  return key === quarantined.sessionKey;
}

/** Supabase no longer holds the ended session, or another session was adopted. */
export function releaseSessionQuarantine(): void {
  quarantined = null;
}

/** Type guard for a ticket carried through code that only sees `unknown`. */
export function isAuthTicket(value: unknown): value is AuthTicket {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.generation === 'number' &&
    typeof candidate.accountId === 'string' &&
    typeof candidate.fact === 'number'
  );
}
