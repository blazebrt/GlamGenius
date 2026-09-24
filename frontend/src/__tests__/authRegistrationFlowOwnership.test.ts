/**
 * A registration flow holds authority over one identity, and only that one.
 *
 * These are the rules of the authority itself (``authGeneration.ts``), with
 * no store and no network. The store-level races, run through the real API
 * client, are in ``authRequestIdentity.test.ts``.
 */
import {
  currentAuth,
  enterRegistrationFlow,
  enterSignUpRegistrationFlow,
  openAuthGeneration,
  registrationFlowOwns,
} from '../store/authGeneration';

describe('a registration flow is owned by one identity', () => {
  it('owns the generation and account it was entered for, and no later identity', () => {
    const a = openAuthGeneration('account-a');
    const flow = enterRegistrationFlow(a);
    expect(registrationFlowOwns(a)).toBe(true);

    const b = openAuthGeneration('account-b');
    // Still live, but A's flow has no say over B.
    expect(registrationFlowOwns(b)).toBe(false);

    // Nor over A signing in again: that is a new generation.
    const aAgain = openAuthGeneration('account-a');
    expect(registrationFlowOwns(aAgain)).toBe(false);
    flow.leave();
  });

  it('entered while signed out, it owns nothing', () => {
    const signedOut = openAuthGeneration('');
    const flow = enterRegistrationFlow(signedOut);
    expect(flow.owner()).toBeNull();
    expect(registrationFlowOwns(currentAuth())).toBe(false);
    flow.leave();
  });

  it('once bound, is never moved to another identity', () => {
    const a = openAuthGeneration('account-a');
    const flow = enterRegistrationFlow(a);
    const b = openAuthGeneration('account-b');
    flow.bind(b);
    expect(registrationFlowOwns(b)).toBe(false);
    expect(flow.owner()).toMatchObject({ generation: a.generation, accountId: 'account-a' });
    flow.leave();
  });
});

describe('E. overlapping flows cannot clear each other', () => {
  it('two flows for the same identity: leaving one, even twice, keeps the other', () => {
    const a = openAuthGeneration('account-a');
    const first = enterRegistrationFlow(a);
    const second = enterRegistrationFlow(a);

    first.leave();
    first.leave();
    expect(registrationFlowOwns(a)).toBe(true);

    second.leave();
    expect(registrationFlowOwns(a)).toBe(false);
  });

  it('a stale flow ending cannot clear the current identity\'s flow', () => {
    const a = openAuthGeneration('account-a');
    const stale = enterRegistrationFlow(a);
    const b = openAuthGeneration('account-b');
    const current = enterRegistrationFlow(b);

    stale.leave();
    expect(registrationFlowOwns(b)).toBe(true);

    current.leave();
    expect(registrationFlowOwns(b)).toBe(false);
  });

  it('the current identity\'s flow ending does not hand authority to a stale one', () => {
    const a = openAuthGeneration('account-a');
    const stale = enterRegistrationFlow(a);
    const b = openAuthGeneration('account-b');
    const current = enterRegistrationFlow(b);

    current.leave();
    expect(registrationFlowOwns(b)).toBe(false);
    expect(registrationFlowOwns(currentAuth())).toBe(false);
    stale.leave();
  });
});

describe('a sign-up flow started before its account exists', () => {
  it('owns nothing until the identity for its own email is opened', () => {
    openAuthGeneration('');
    const flow = enterSignUpRegistrationFlow('  New@Example.com ');
    expect(flow.owner()).toBeNull();
    expect(registrationFlowOwns(currentAuth())).toBe(false);

    // Someone else signing in meanwhile is not held back by it.
    const other = openAuthGeneration('account-other', 'other@example.com');
    expect(registrationFlowOwns(other)).toBe(false);
    expect(flow.owner()).toBeNull();

    // The account it is creating: bound in the same step that opens it.
    const created = openAuthGeneration('account-new', 'new@example.com');
    expect(registrationFlowOwns(created)).toBe(true);
    expect(flow.owner()).toMatchObject({ generation: created.generation, accountId: 'account-new' });

    // Later identities, including the same account signing in again, are not its.
    openAuthGeneration('');
    const again = openAuthGeneration('account-new', 'new@example.com');
    expect(registrationFlowOwns(again)).toBe(false);
    flow.leave();
  });

  it('ending unbound leaves nothing behind', () => {
    const flow = enterSignUpRegistrationFlow('late@example.com');
    flow.leave();
    const late = openAuthGeneration('account-late', 'late@example.com');
    expect(registrationFlowOwns(late)).toBe(false);
  });
});
