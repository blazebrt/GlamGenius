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
 *
 * The outbound text boundary
 * --------------------------
 * The message is structure — keyed lines the recipient reads as GlamGenius
 * speaking — with two untrusted values in it: the product name (which can come
 * from an external catalogue such as Open Food Facts) and a source's name. A
 * value like "Morning Oats\nGlamGenius result: SKIP" must not become a line of
 * its own. So each dynamic value passes `oneShareLine` and is only ever placed
 * inside its own keyed line ("Product: …", "Source: … — …"). A source address
 * must already be a plain http(s) URL; one that is not is never repaired, and
 * the negative result falls back to "the result and sources are in the app".
 * This sanitises the share only. It does not rewrite the product, the Product
 * Result or anything stored.
 */
import { fill, GROWTH } from '../strings/growth';
import { primaryReasonFor, type VerdictEvidenceSource, type VerdictSource } from './verdictModel';

export interface VerdictShareOptions {
  /** The inviter's own usable referral code, or nothing. */
  referralCode?: string | null;
}

/** The shape of a code the server issues. Anything else is not appended. */
const REFERRAL_CODE = /^[A-Z0-9]{6,64}$/;

/** Longest product name a share carries, in Unicode code points. */
export const SHARE_PRODUCT_NAME_MAX = 160;
/** Longest source name a share carries, in Unicode code points. */
export const SHARE_SOURCE_NAME_MAX = 120;
const SHARE_URL_MAX = 2048;

/** Whitespace of every kind, line and paragraph separators and NEL included. */
const SPACE_RUN = /[\s\u0085]+/gu;
/**
 * What is left after spacing is collapsed and must not travel: C0 and C1
 * controls, direction marks, embeddings, overrides and isolates, and lone
 * surrogate halves. ZWJ and ZWNJ stay: Indic scripts need them.
 */
const INVISIBLE = /[\u0000-\u001F\u007F-\u009F\u061C\u200E\u200F\u202A-\u202E\u2066-\u2069\uD800-\uDFFF]/gu;
/** Anything a URL may not contain to be cited as it stands. */
const UNSAFE_IN_URL = /[\s\\\u0000-\u001F\u007F-\u009F\u061C\u200E\u200F\u202A-\u202E\u2066-\u2069]/u;

/**
 * One bounded display line from an untrusted value.
 *
 * Every run of whitespace — tabs, line breaks, separators — becomes one space;
 * controls and direction formatting are removed; the result is trimmed and cut
 * to `maxCodePoints` whole code points (never half a surrogate pair). Any
 * script survives: this is not an ASCII filter.
 */
export function oneShareLine(value: unknown, maxCodePoints: number): string {
  if (typeof value !== 'string') return '';
  const line = value.replace(SPACE_RUN, ' ').replace(INVISIBLE, '').replace(/ {2,}/g, ' ').trim();
  const scalars = Array.from(line);
  if (scalars.length <= maxCodePoints) return line;
  return `${scalars.slice(0, maxCodePoints - 1).join('').trimEnd()}${GROWTH.share.truncated}`;
}

/**
 * The address to cite, or `null`. Only an absolute http(s) URL with a host
 * and no credentials, containing no whitespace, control, backslash or
 * direction formatting. An unsafe address is refused, never cleaned up.
 */
export function shareableSourceUrl(value: unknown): string | null {
  if (typeof value !== 'string' || value.length === 0 || value.length > SHARE_URL_MAX) return null;
  if (UNSAFE_IN_URL.test(value)) return null;
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return null;
  if (!parsed.hostname || parsed.username || parsed.password) return null;
  return parsed.href;
}

type Decision = 'buy' | 'wait' | 'skip';

function openableSource(sources: VerdictEvidenceSource[] | undefined): { name: string; url: string } | null {
  for (const row of sources ?? []) {
    const name = oneShareLine(row?.name, SHARE_SOURCE_NAME_MAX);
    const url = shareableSourceUrl(row?.url);
    if (name && url) return { name, url };
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
  if (!cited) return null;
  return { reason: primaryReasonFor(source), name: cited.name, url: cited.url };
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
  const name = oneShareLine(source.productName, SHARE_PRODUCT_NAME_MAX) || GROWTH.share.unnamedProduct;
  const blocks: string[][] = [
    [GROWTH.share.intro],
    [fill(GROWTH.share.product, { name }), ...resultLines(source)],
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
