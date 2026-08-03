/**
 * Static test — no payment or V1 remnant terms in active frontend source.
 *
 * §4 of the Supabase hardening spec: every payment concept has been
 * removed. This scans active `.ts`/`.tsx` files under `frontend/src` and
 * `frontend/app` for identifiers and strings that were deliberately deleted
 * and must never come back.
 *
 * Excluded: test files (they may reference removed terms in absence
 * assertions), this file itself, and the audit docs.
 */
import * as fs from 'fs';
import * as path from 'path';

const REPO_FRONTEND = path.resolve(__dirname, '..', '..');
const SCAN_ROOTS = [
  path.join(REPO_FRONTEND, 'src'),
  path.join(REPO_FRONTEND, 'app'),
];

// Case-sensitive substrings. Includes both TS identifiers and UI copy that
// used to accompany the removed features.
const FORBIDDEN = [
  'razorpay',
  'Razorpay',
  'SUBSCRIPTIONS_UNAVAILABLE',
  'SubscriptionsUnavailable',
  'SUBSCRIPTIONS_AVAILABLE',
  'billingAvailable',
  'billing_available',
  'plus_monthly',
  'plus_yearly',
  'event_pass',
  'paywall',
  'checkout',
  '/api/subscription',
  // Signed-out scan-preview remnants explicitly banned by §4
  'Try a free check',
  'No account needed',
  'Create free account',
  'Free — saves',
];

const isTestFile = (p: string) =>
  /\.test\.(t|j)sx?$/.test(p) || p.includes(`${path.sep}__tests__${path.sep}`);

function walk(dir: string, out: string[] = []): string[] {
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
      walk(full, out);
    } else if (/\.(t|j)sx?$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

describe('no payment or scan-preview remnants in the active frontend', () => {
  const offenders: { file: string; needle: string; line: number }[] = [];
  const files: string[] = [];
  for (const root of SCAN_ROOTS) walk(root, files);
  for (const file of files) {
    if (isTestFile(file)) continue;
    const text = fs.readFileSync(file, 'utf8');
    const lines = text.split('\n');
    for (const needle of FORBIDDEN) {
      lines.forEach((line, idx) => {
        if (line.includes(needle)) {
          offenders.push({ file, needle, line: idx + 1 });
        }
      });
    }
  }

  it('finds no forbidden payment or preview strings in active code', () => {
    if (offenders.length > 0) {
      const rendered = offenders
        .map((o) => `  ${path.relative(REPO_FRONTEND, o.file)}:${o.line}  →  ${o.needle}`)
        .join('\n');
      throw new Error(
        `Found ${offenders.length} active reference(s) to removed payment/preview terms:\n${rendered}\n` +
          `See §4 in docs/stabilisation/SUPABASE_HARDENING_REPORT.md.`
      );
    }
    expect(offenders).toHaveLength(0);
  });
});
