/**
 * Step 15 guards: what consumer growth may never become.
 *
 * Static on purpose. Each of these is a line somebody could cross in one
 * import or one string, and a behavioural test would only notice after the
 * fact.
 */
import * as fs from 'fs';
import * as path from 'path';

import { consumeFreshScanRequest, requestFreshScan } from '../services/scanSession';
import { newClientEventId } from '../services/growth';

const ROOT = path.resolve(__dirname, '../..');
const read = (relative: string) => fs.readFileSync(path.join(ROOT, relative), 'utf8');
const code = (relative: string) => read(relative).replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');

const STEP15_FILES = [
  'src/services/verdictShare.ts',
  'src/services/growth.ts',
  'src/services/scanSession.ts',
  'src/strings/growth.ts',
];

// eslint-disable-next-line @typescript-eslint/no-require-imports
const ts = require('typescript') as typeof import('typescript');
const PROSE_PROPS = new Set(['accessibilityLabel', 'accessibilityHint', 'title', 'placeholder', 'label']);
const readsAsProse = (value: string) => {
  const text = value.trim();
  return text.split(/\s+/).length >= 2 && (/^[A-Z]/.test(text) || /[.!?]$/.test(text));
};
function strayProse(relative: string, text: string = read(relative)): string[] {
  const source = ts.createSourceFile(relative, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const stray: string[] = [];
  const visit = (node: import('typescript').Node) => {
    if (ts.isJsxText(node) && /[A-Za-z]/.test(node.getText())) stray.push(node.getText().trim());
    if (ts.isJsxAttribute(node) && node.initializer && ts.isStringLiteral(node.initializer)
        && PROSE_PROPS.has(node.name.getText())) stray.push(node.initializer.text);
    if ((ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
        && !ts.isImportDeclaration(node.parent) && readsAsProse(node.text)) stray.push(node.text);
    ts.forEachChild(node, visit);
  };
  visit(source);
  return stray;
}

/** The Step 15 blocks of a file that predates Step 15, between two markers. */
function between(relative: string, start: string, end: string): string {
  const text = read(relative);
  const from = text.indexOf(start);
  const to = text.indexOf(end, from + start.length);
  expect(from).toBeGreaterThanOrEqual(0);
  expect(to).toBeGreaterThan(from);
  return text.slice(from, to);
}

describe('Step 15 — the scanner stays first', () => {
  it('keeps the old appearance onboarding retired', () => {
    const onboarding = code('app/onboarding.tsx');
    expect(onboarding).toContain('<Redirect href="/scan-product" />');
    expect(onboarding).not.toMatch(/useState|TextInput|question|quiz|skin tone|colour|style/i);
  });

  it('has exactly two tabs, Scan and You, and no growth, discover or invite tab', () => {
    const layout = code('app/(tabs)/_layout.tsx');
    const screens = [...layout.matchAll(/<Tabs\.Screen\s+name="([^"]+)"/g)].map((match) => match[1]);
    expect(screens).toEqual(['scan', 'you']);
    for (const name of fs.readdirSync(path.join(ROOT, 'app'))) {
      expect(name).not.toMatch(/growth|discover|invite|referral|feed|leaderboard|trending|search/i);
    }
    for (const name of fs.readdirSync(path.join(ROOT, 'app', '(tabs)'))) {
      expect(name).not.toMatch(/growth|discover|invite|referral|feed|leaderboard|trending|search/i);
    }
    expect(code('app/(tabs)/scan.tsx')).toContain('<Redirect href="/scan-product" />');
  });

  it('asks the scanner for a fresh start only once per request', () => {
    expect(consumeFreshScanRequest()).toBe(false);
    requestFreshScan();
    expect(consumeFreshScanRequest()).toBe(true);
    expect(consumeFreshScanRequest()).toBe(false);
    const scanner = code('app/scan-product.tsx');
    expect(scanner).toMatch(/useFocusEffect\(\s*useCallback\(\(\) => \{\s*if \(consumeFreshScanRequest\(\)\) scanAgain\(\);/);
  });
});

describe('Step 15 — every new customer word is keyed', () => {
  it.each(STEP15_FILES.filter((file) => !file.startsWith('src/strings')))('%s carries no customer prose of its own', (relative) => {
    expect(strayProse(relative)).toEqual([]);
  });

  it('the Product Result reads its Step 15 words from the key file', () => {
    const block = between('app/verdict.tsx', '// Step 15. The share is built', 'const openReport');
    expect(strayProse('verdict-share-block.tsx', block)).toEqual([]);
    const link = between('app/verdict.tsx', '{/* Step 15. One quiet link', '<TouchableOpacity\n              accessibilityRole="button" accessibilityLabel={S.primary.ingredients}');
    expect(strayProse('verdict-link-block.tsx', link)).toEqual([]);
    expect(link).toContain('GROWTH.scanAgain.action');
    expect(strayProse('app/verdict.tsx')).toEqual([]);
  });

  it('the scanner’s Step 15 addition has no words at all', () => {
    const block = between('app/scan-product.tsx', '// Step 15.', 'useFocusEffect(\n    useCallback(() => {\n      if (stage');
    expect(strayProse('scan-block.tsx', block)).toEqual([]);
  });
});

describe('Step 15 — the share never reads pack or person context', () => {
  it('builds from the Product Result source only', () => {
    const share = code('src/services/verdictShare.ts');
    const imports = [...share.matchAll(/from '([^']+)'/g)].map((match) => match[1]).sort();
    expect(imports).toEqual(['../strings/growth', './verdictModel']);
    for (const forbidden of ['officialRecords', 'labelVersion', 'labelChange', 'communityObservations', 'purchaseCheck', 'dominantView', 'memory', 'ownership', 'watch', 'household', 'subject', 'everydayNumber', 'barcode', 'batch', 'licence']) {
      expect(share).not.toContain(forbidden);
    }
    const onShare = between('app/verdict.tsx', 'const onShare = useCallback', 'const scanAnother');
    expect(onShare).not.toMatch(/\bview\b|purchaseCheck|memory/);
    expect(onShare).toContain('buildVerdictShareText(source, { referralCode })');
  });
});

describe('Step 15 — no permission, SDK, push, ad or shop', () => {
  const manifest = JSON.parse(read('package.json')) as { dependencies?: Record<string, string>; devDependencies?: Record<string, string> };
  const packages = Object.keys({ ...manifest.dependencies, ...manifest.devDependencies });

  it('adds no contacts, WhatsApp, advertising, attribution or growth SDK', () => {
    const banned = /contacts|whatsapp|branch|appsflyer|adjust|mixpanel|amplitude|segment|customerio|onesignal|facebook|fbsdk|google-mobile-ads|admob|expo-ads|tracking-transparency|advertising|kochava|singular|clevertap|moengage|webengage/i;
    expect(packages.filter((name) => banned.test(name))).toEqual([]);
  });

  it('asks for no contact, WhatsApp or notification permission, and sends no push', () => {
    const files = [...STEP15_FILES, 'app/verdict.tsx'];
    for (const file of files) {
      const text = code(file);
      expect(text).not.toMatch(/whatsapp:\/\/|wa\.me|Contacts|getPermissionsAsync|requestPermissionsAsync|expo-notifications|scheduleNotification|registerForPush/i);
    }
    for (const file of STEP15_FILES) {
      expect(code(file)).not.toMatch(/notify|Notification|pushToken|sendPush|push_token|expoPush/);
    }
  });

  it('carries no Commerce, reward or ranking vocabulary', () => {
    const banned = /\b(affiliate|retailer|buy now|cart|checkout|seller|offer|coupon|stock|price|order|payment|commission|reward|coins?|points|credits?|leaderboard|streak|rank|trending|popular)\b/i;
    for (const file of STEP15_FILES) expect(code(file)).not.toMatch(banned);
  });

  it('no Step 15 copy links anywhere but the licence and the Open Food Facts source', () => {
    const copy = code('src/strings/growth.ts');
    expect(copy).not.toMatch(/https?:\/\/(?!world\.openfoodfacts\.org|opendatacommons\.org)/);
    expect(copy).not.toMatch(/glamgenius:\/\//);
  });
});

describe('Step 15 — telemetry carries no identifier', () => {
  it('mints a random version-4 operation id, never a device id', () => {
    const ids = new Set(Array.from({ length: 50 }, () => newClientEventId()));
    expect(ids.size).toBe(50);
    for (const id of ids) expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    const growth = code('src/services/growth.ts');
    expect(growth).not.toMatch(/deviceKey|device_id|readStoredDevice|installation|advertising|idfa|gaid/i);
  });

  it('types exactly the two events the server accepts', () => {
    const growth = code('src/services/growth.ts');
    const names = [...growth.matchAll(/name: '(growth\.[a-z_]+)'/g)].map((match) => match[1]).sort();
    expect(names).toEqual(['growth.product_result_share', 'growth.scan_again']);
    expect(growth).not.toMatch(/barcode|productName|product_name|email|recipient|destination|activityType/);
  });
});
