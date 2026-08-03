/**
 * Static test — no active V1 API paths.
 *
 * This scans every active source file in `frontend/src` and `frontend/app`
 * for string literals matching the V1 API endpoints that were removed in the
 * Supabase cutover. If any of these paths ever come back as a string in an
 * active screen, the CI build fails here.
 *
 * "Active" excludes:
 *   - `frontend/app/subscription.tsx` and `frontend/app/paywall.tsx`,
 *     which are dead files kept only for Prompt 2 to delete. They are not
 *     linked from any navigation and no route pushes into them.
 *   - test files (`*.test.*`), which may reference V1 paths as fixtures.
 *   - This test itself.
 *
 * The check is intentionally string-based, not AST-based: a literal
 * `'/api/auth/…'` or `'/auth/login'` in an active screen is a regression
 * regardless of whether it is actually reachable at runtime, because it is
 * evidence that a V1 code path was reintroduced.
 */
import * as fs from 'fs';
import * as path from 'path';

const REPO_FRONTEND = path.resolve(__dirname, '..', '..');
const SCAN_ROOTS = [
  path.join(REPO_FRONTEND, 'src'),
  path.join(REPO_FRONTEND, 'app'),
];

const FORBIDDEN = [
  '/api/auth/',
  '/auth/login',
  '/auth/register',
  '/api/users',
  '/api/scan/',
  '/api/quiz/',
  '/api/plans/',
  '/api/recommendations',
  '/api/services',
  '/api/salon-ideas',
  '/api/subscription',
  // Bare V1 paths passed to the shared axios client. The V2 client uses
  // `/api/v2/…`; a bare `/scan/history` would be a V1 call again.
  "'/scan/history'",
  "'/scan/analyze'",
  "'/scan/preview'",
  "'/quiz/questions'",
  "'/quiz/submit'",
  "'/plans/style'",
  "'/users/me'",
  "'/users'",
  "'/subscription/status'",
  "'/subscription/create-order'",
  "'/subscription/confirm'",
  "'/salon-ideas'",
  "'/services/",
];

const EXCLUDED_FILES = new Set([
  path.join(REPO_FRONTEND, 'app', 'subscription.tsx'),
  path.join(REPO_FRONTEND, 'app', 'paywall.tsx'),
]);

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

describe('no active V1 API paths in the frontend', () => {
  const offenders: { file: string; needle: string; line: number }[] = [];
  const files: string[] = [];
  for (const root of SCAN_ROOTS) walk(root, files);
  for (const file of files) {
    if (EXCLUDED_FILES.has(file)) continue;
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

  it('finds no forbidden V1 API strings in active code', () => {
    if (offenders.length > 0) {
      const rendered = offenders
        .map((o) => `  ${path.relative(REPO_FRONTEND, o.file)}:${o.line}  →  ${o.needle}`)
        .join('\n');
      throw new Error(
        `Found ${offenders.length} active reference(s) to V1 API paths:\n${rendered}\n` +
          `Replace with the /api/v2/... equivalent. See docs/architecture/SUPABASE_CUTOVER_AUDIT.md §2.`
      );
    }
    expect(offenders).toHaveLength(0);
  });
});
