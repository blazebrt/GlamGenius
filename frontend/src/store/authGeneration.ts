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
let registrationFlows = 0;

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
 */
export function openAuthGeneration(nextAccountId: string): AuthTicket {
  generation += 1;
  accountId = nextAccountId;
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

/**
 * Mark an invite registration as in progress until the returned function is
 * called.
 *
 * While one is running, that flow alone decides the new account's
 * registration state. A background check does not start, and a
 * REGISTRATION_REQUIRED from a request that raced the finalisation cannot
 * move the person off the path to onboarding.
 */
export function enterRegistrationFlow(): () => void {
  registrationFlows += 1;
  let left = false;
  return () => {
    if (left) return;
    left = true;
    registrationFlows -= 1;
  };
}

export function registrationFlowActive(): boolean {
  return registrationFlows > 0;
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
