/**
 * Turning a Step 14 purchase check into what the Product Result shows.
 *
 * Two jobs, both pure:
 *
 * 1. **One dominant decision.** The verdict block at the top of the screen is
 *    the Product Result's. The purchase check changes it in exactly one case:
 *    an exact, governed official record matches the pack in this person's hand
 *    and the server placed its WAIT ceiling. Then the block shows that answer
 *    and says why, so the screen never shows BUY above a section explaining
 *    why the answer is not BUY. In every other case the check is context, and
 *    the block is untouched.
 * 2. **The purchase context rows.** Only the rows whose authority has
 *    something to say. The section never repeats the verdict word on its own.
 *
 * A check is used only when it describes the exact thing on screen: the same
 * barcode, the same label version and fingerprint, the same reference/physical
 * view. A stale or mismatched check is dropped, never partially applied.
 */
import { PURCHASE_OS, fill, type PurchaseVerdict } from '../strings/purchaseOs';
import type { PurchaseOsAuthority, PurchaseOsCheck } from './apiV2';
import type { ColourBand, VerdictView } from './verdictModel';

export const OFFICIAL_RECORD_REASON = 'official_record_matches_pack';
export const OFFICIAL_RECORDS_AUTHORITY = 'official_records';

const BAND_BY_VERDICT: Record<PurchaseVerdict, ColourBand> = {
  buy: 'green',
  wait: 'yellow',
  skip: 'red',
};

export interface ShownIdentity {
  barcode: string;
  referenceView: boolean;
  labelVersion: { versionNumber: number; contentFingerprint: string } | null;
}

/** Whether this check is about exactly what the screen is showing. */
export function purchaseCheckMatches(check: PurchaseOsCheck | null, shown: ShownIdentity): check is PurchaseOsCheck {
  if (!check || check.contract_version !== 'step-14-v1' || check.context?.kind !== 'scan') return false;
  const identity = check.identity;
  if (!identity || identity.barcode !== shown.barcode) return false;
  if (Boolean(identity.reference_view) !== shown.referenceView) return false;
  if (!shown.labelVersion) return identity.state === 'insufficient';
  return identity.state === 'exact'
    && identity.label_version === shown.labelVersion.versionNumber
    && identity.content_fingerprint === shown.labelVersion.contentFingerprint;
}

/** True only for the one cross-authority rule: the governed official-record ceiling. */
export function officialCeilingApplies(check: PurchaseOsCheck): boolean {
  const decision = check.decision;
  // The server applies the ceiling only under physical-pack authority; the
  // screen refuses it anywhere else too, so a reference view can never gain it.
  if (check.identity.reference_view || check.identity.physical_pack_context !== true) return false;
  return decision.state === 'decided'
    && decision.verdict !== null
    && decision.primary_reason_code === OFFICIAL_RECORD_REASON
    && decision.primary_reason_authority === OFFICIAL_RECORDS_AUTHORITY;
}

/** The verdict block to show: the Product Result's, unless the official ceiling applies. */
export function dominantView(view: VerdictView, check: PurchaseOsCheck | null): VerdictView {
  if (!check || !officialCeilingApplies(check)) return view;
  const verdict = check.decision.verdict as PurchaseVerdict;
  const word = PURCHASE_OS.verdictWord[verdict];
  const reason = PURCHASE_OS.context.why.officialRecord;
  return {
    ...view,
    band: BAND_BY_VERDICT[verdict],
    verdict: word,
    decisionAction: word,
    primaryReason: reason,
    action: reason,
    spoken: fill(PURCHASE_OS.context.a11y.answer, { verdict: word, reason }),
  };
}

export interface ContextRow {
  key: 'why' | 'official' | 'owned' | 'prior' | 'changes' | 'better';
  title: string;
  lines: string[];
  source?: { name: string; url: string };
}

const authority = (check: PurchaseOsCheck, name: string): PurchaseOsAuthority | undefined =>
  check.authorities.find((row) => row.authority === name);

const decisionWord = (value: string | undefined | null): string | null => {
  const key = (value || '').toLowerCase();
  return key in PURCHASE_OS.verdictWord ? PURCHASE_OS.verdictWord[key as PurchaseVerdict] : null;
};

function whyLine(check: PurchaseOsCheck): string {
  const { decision, identity } = check;
  const why = PURCHASE_OS.context.why;
  if (identity.reference_view) return why.referenceView;
  if (decision.state === 'decided') {
    return decision.primary_reason_authority === OFFICIAL_RECORDS_AUTHORITY ? why.officialRecord : why.productResult;
  }
  return decision.primary_reason_code === 'identity_insufficient' ? why.identityInsufficient : why.notEnoughInformation;
}

/** The screen-reader summary: the answer and its reason in words, never a colour. */
export function answerLabel(check: PurchaseOsCheck): string {
  const reason = whyLine(check);
  const verdict = check.decision.state === 'decided' ? decisionWord(check.decision.verdict) : null;
  return verdict
    ? fill(PURCHASE_OS.context.a11y.answer, { verdict, reason })
    : fill(PURCHASE_OS.context.a11y.noAnswer, { reason });
}

/** Only the rows whose authority is available, in the order the brief sets. */
export function contextRows(check: PurchaseOsCheck): ContextRow[] {
  const copy = PURCHASE_OS.context;
  const rows: ContextRow[] = [{ key: 'why', title: copy.why.title, lines: [whyLine(check)] }];
  // A reference view is not holding the pack: nothing pack-specific follows.
  if (check.identity.reference_view) return rows;

  const official = authority(check, OFFICIAL_RECORDS_AUTHORITY);
  if (official && (official.status === 'applied' || official.status === 'consistent_with_decision') && official.source) {
    rows.push({
      key: 'official', title: copy.official.title,
      lines: [official.status === 'applied' ? copy.official.ceiling : copy.official.consistent],
      source: official.source,
    });
  }

  if (check.ownership?.state === 'owned_exact_version') {
    rows.push({ key: 'owned', title: copy.owned.title, lines: [copy.owned.exactVersion] });
  }

  const memory = check.memory;
  if (memory && memory.kind === 'scan_decision') {
    const lines: string[] = [];
    const current = decisionWord(memory.current_decision?.decision);
    if (memory.state === 'prior_exact_decision' && current) {
      lines.push(fill(copy.prior.exactVersion, { decision: current }));
    }
    const earlier = memory.earlier_version_decision;
    const earlierWord = decisionWord(earlier?.decision);
    if (earlier && earlierWord && earlier.applies_to_current_version === false) {
      lines.push(fill(copy.prior.earlierVersion, { decision: earlierWord, version: earlier.label_version }));
    }
    if (memory.state === 'history_incomplete') lines.push(copy.prior.historyIncomplete);
    if (lines.length) rows.push({ key: 'prior', title: copy.prior.title, lines });
  }

  const changes: string[] = [];
  if (authority(check, 'label_change')?.status === 'changed') changes.push(copy.changes.label);
  if (authority(check, 'regulatory_change')?.status === 'changed') changes.push(copy.changes.regulatory);
  if (changes.length) rows.push({ key: 'changes', title: copy.changes.title, lines: [...changes, copy.changes.contextOnly] });

  if (check.alternative?.status === 'available' && check.alternative.candidate) {
    rows.push({ key: 'better', title: copy.better.title, lines: [copy.better.shownBelow] });
  }
  return rows;
}
