/**
 * The text a Product Result share hands to the system share sheet.
 *
 * Plain text through the platform's own sheet, so WhatsApp and every other app
 * the person already has are there without an SDK, a contact list, a recipient
 * or a record of which app they chose. No brand logo: a product name is text,
 * and a negative result never becomes a branded visual accusation.
 *
 * What may leave the app (Step 15)
 * --------------------------------
 * A share is read by somebody who is not holding the sender's pack, so it is
 * built from the canonical Product Result alone — the product science that is
 * the same for everybody — and from nothing that describes the sender:
 *
 *   - It takes the Product Result `source` and **not** the on-screen view. The
 *     view can carry Step 14's official-record WAIT ceiling, which is a fact
 *     about the exact pack in the sender's hand (a licence and lot matched to
 *     their confirmed capture). Sent to someone else it would claim their pack
 *     matches that record. So this function has no parameter it could arrive
 *     through, and it never reads official records, label or regulatory
 *     change, memory, shelf ownership, Product Watch or the household.
 *   - The product name, as the Product Result shows it. With the Open Food
 *     Facts attribution, fixed wording and links, whenever that data is in play.
 *   - The decision: BUY is stated. WAIT or SKIP is stated only together with
 *     its primary reason and that reason's named, openable, published source;
 *     without one, the share says where the result and its sources are instead.
 *     "Not graded" and "not enough information" are stated as what they are.
 *   - Never the everyday number. It is a label figure from one label version
 *     (possibly one person's photograph of one pack), and the share cannot carry
 *     which version or where it came from; the recipient's pack may differ.
 *   - An invite code only when the caller passes a well-formed one. Sharing
 *     never depends on it.
 */
import { fill, GROWTH } from '../strings/growth';
import { primaryReasonFor, type VerdictEvidenceSource, type VerdictSource } from './verdictModel';

export interface VerdictShareOptions {
  /** The inviter's own usable referral code, or nothing. */
  referralCode?: string | null;
}

/** The shape of a code the server issues. Anything else is not appended. */
const REFERRAL_CODE = /^[A-Z0-9]{6,64}$/;
const OPENABLE = /^https?:\/\/\S+$/i;

type Decision = 'buy' | 'wait' | 'skip';

function openableSource(sources: VerdictEvidenceSource[] | undefined): VerdictEvidenceSource | null {
  for (const row of sources ?? []) {
    if (row && typeof row.name === 'string' && row.name.trim() && typeof row.url === 'string' && OPENABLE.test(row.url.trim())) {
      return row;
    }
  }
  return null;
}

/**
 * The primary reason and its source, or nothing.
 *
 * Only the row the decision itself names (`reasonKey`), only when the rule
 * behind it is published, and only with a source somebody can open.
 */
function sourcedReason(source: VerdictSource): { reason: string; name: string; url: string } | null {
  const key = source.decision?.reasonKey;
  const row = (source.negatives ?? []).find((factor) => factor.key === key);
  if (!key || !row || row.evidence?.status !== 'published') return null;
  const cited = openableSource(row.sources);
  if (!cited || !cited.url) return null;
  return { reason: primaryReasonFor(source), name: cited.name.trim(), url: cited.url.trim() };
}

function resultLines(source: VerdictSource): string[] {
  if (source.outcome === 'not_graded') return [GROWTH.share.notGraded];
  const action = source.decision?.action as Decision | null | undefined;
  if (source.outcome !== 'graded' || !action || !(action in GROWTH.share.decisionWord)) {
    return [GROWTH.share.notEnoughInformation];
  }
  const decision = fill(GROWTH.share.decision, { decision: GROWTH.share.decisionWord[action] });
  const reason = sourcedReason(source);
  if (reason) {
    return [decision, reason.reason, fill(GROWTH.share.reasonSource, { name: reason.name, url: reason.url })];
  }
  // A negative answer without its source does not leave the app.
  return action === 'buy' ? [decision] : [GROWTH.share.resultInApp];
}

export function buildVerdictShareText(source: VerdictSource, options: VerdictShareOptions = {}): string {
  const product = source.productName?.trim() || GROWTH.share.unnamedProduct;
  const blocks: string[][] = [
    [GROWTH.share.intro],
    [product, ...resultLines(source)],
  ];
  if (source.attribution) blocks.push([GROWTH.share.odblAttribution, GROWTH.share.odblLinks]);
  blocks.push([GROWTH.share.ownPack]);
  const code = options.referralCode?.trim() ?? '';
  blocks.push(
    REFERRAL_CODE.test(code)
      ? [GROWTH.share.privateBeta, fill(GROWTH.share.inviteCode, { code })]
      : [GROWTH.share.privateBeta],
  );
  return blocks.map((lines) => lines.join('\n')).join('\n\n');
}
