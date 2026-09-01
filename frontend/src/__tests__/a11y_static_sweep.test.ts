/**
 * Static accessibility no-regression check (Fix 17, WP6).
 *
 * This test does NOT walk a real device — that is the owner action documented in
 * docs/product/DEVICE_SWEEP_PROTOCOL.md. What it does do, on every CI run, is grep the
 * screens under app/ for interactive components without an accessibilityLabel prop.
 *
 * At the time WP6 landed, the app has 45 pre-existing interactive elements without an
 * accessibilityLabel. Rather than blocking the WP6 merge on fixing every one of them
 * (which is exactly the work the physical-device sweep is designed to plan), this
 * test freezes the count as a BASELINE:
 *
 *   * A CI run whose count > BASELINE fails — a new PR added a violation.
 *   * A CI run whose count === BASELINE passes.
 *   * A CI run whose count < BASELINE prints a friendly reminder to lower BASELINE
 *     and passes — the developer who reduced the count records the new floor.
 *
 * The rule is deliberately imperfect: it uses static-text matching, not a JSX parser.
 * A component whose accessibilityLabel is set programmatically will show as a false
 * positive; the reviewer marks such cases in the ALLOWLIST below.
 *
 * The point is not to catch every violation — the device sweep does that. The point is
 * to catch a regression the day it lands, before it reaches a device.
 */
import * as fs from 'fs';
import * as path from 'path';

/**
 * The number of interactive elements without accessibilityLabel at the time WP6 landed.
 * Lower this — never raise it — as the physical-device sweep and follow-up PRs fix
 * screens. A PR that reduces the count updates this constant in the same diff.
 */
const BASELINE = 8;

/** Screens / files we deliberately do not enforce this check on. */
const ALLOWLIST: string[] = [
  // Add absolute paths (relative to repo root) here with a reviewer comment
  // when a file legitimately does not need accessibilityLabel enforcement,
  // e.g. a pure layout wrapper. Keep the list short.
];

/** Components that must carry an accessibilityLabel on every use. */
const INTERACTIVE_COMPONENTS = [
  'Pressable',
  'TouchableOpacity',
  'TouchableHighlight',
  'TouchableWithoutFeedback',
];

function walk(dir: string, files: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name === '.expo') continue;
      walk(full, files);
    } else if (entry.isFile() && (entry.name.endsWith('.tsx') || entry.name.endsWith('.jsx'))) {
      files.push(full);
    }
  }
  return files;
}

function findViolations(source: string, filepath: string): string[] {
  const violations: string[] = [];
  // Very rough regex: match `<Component ` and then, up to the matching `>`, check for accessibilityLabel.
  // We only flag block-opener uses, not the closing tag or the import.
  for (const component of INTERACTIVE_COMPONENTS) {
    const re = new RegExp(`<${component}(?![A-Za-z0-9_])([\\s\\S]*?)>`, 'g');
    let match: RegExpExecArray | null;
    while ((match = re.exec(source)) !== null) {
      const attributes = match[1] || '';
      if (!/accessibilityLabel\s*=/.test(attributes) && !/accessibilityLabelledBy\s*=/.test(attributes)) {
        // Get a 1-based line number for the report.
        const upto = source.slice(0, match.index);
        const line = upto.split('\n').length;
        violations.push(`${filepath}:${line}: <${component}> without accessibilityLabel`);
      }
    }
  }
  return violations;
}

describe('a11y static sweep (Fix 17, WP6)', () => {
  it('interactive elements without accessibilityLabel do not regress beyond the WP6 baseline', () => {
    const appDir = path.resolve(__dirname, '..', '..', 'app');
    const files = walk(appDir).filter(
      (f) => !ALLOWLIST.some((allowed) => f.endsWith(allowed))
    );

    const violations: string[] = [];
    for (const file of files) {
      const src = fs.readFileSync(file, 'utf8');
      violations.push(...findViolations(src, file.replace(path.resolve(__dirname, '..', '..'), '')));
    }

    if (violations.length > BASELINE) {
      const message = [
        `Regression: found ${violations.length} interactive elements without accessibilityLabel.`,
        `The WP6 baseline is ${BASELINE}; the new elements are somewhere in the list below.`,
        ...violations.slice(0, 50).map((v) => '  ' + v),
        violations.length > 50 ? `  ...and ${violations.length - 50} more.` : '',
        '',
        'Fix the offending element by adding accessibilityLabel="…" that describes what it does.',
        'Do NOT raise BASELINE. If the violation is legitimate, add the file to ALLOWLIST with a reviewer comment.',
      ].join('\n');
      throw new Error(message);
    }

    if (violations.length < BASELINE) {
      // Friendly nudge — does not fail the build. When the count drops,
      // the developer lowers BASELINE in the same PR that made the fix.
      console.warn(
        `a11y baseline improved: violations=${violations.length}, BASELINE=${BASELINE}. ` +
        `Lower BASELINE in this PR to lock in the improvement.`
      );
    }
  });
});

