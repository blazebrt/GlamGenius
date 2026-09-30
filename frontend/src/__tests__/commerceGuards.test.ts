/**
 * Step 16 guards: what Commerce may never become.
 *
 * Static on purpose. Each of these is a line somebody could cross in one
 * import or one string, and a behavioural test would only notice afterwards.
 */
import * as fs from 'fs';
import * as path from 'path';

import { COMMERCE_PARTNER_KEYS, isCommerceDestination } from '../services/commerce';
import { COMMERCE, COMMERCE_COPY_VERSION } from '../strings/commerce';

const ROOT = path.resolve(__dirname, '../..');
const read = (relative: string) => fs.readFileSync(path.join(ROOT, relative), 'utf8');
const code = (relative: string) => read(relative).replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');

const STEP16_FILES = [
  'src/services/commerce.ts',
  'src/components/verdict/CommerceHandoff.tsx',
  'src/strings/commerce.ts',
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

const allCopy: string[] = [];
const collect = (value: unknown) => {
  if (typeof value === 'string') allCopy.push(value);
  else if (value && typeof value === 'object') Object.values(value).forEach(collect);
};
collect(COMMERCE);

describe('Step 16 — every word is keyed', () => {
  it.each([
    'src/services/commerce.ts',
    'src/components/verdict/CommerceHandoff.tsx',
    'src/components/verdict/BetterOption.tsx',
    'app/verdict.tsx',
  ])('%s carries no customer prose of its own', (relative) => {
    expect(strayProse(relative)).toEqual([]);
  });

  it('the Product Result reads its Step 16 words through the component only', () => {
    const verdict = read('app/verdict.tsx');
    for (const line of allCopy) expect(verdict).not.toContain(line);
    expect(verdict).not.toContain('strings/commerce');
  });

  it('the copy is versioned', () => {
    expect(COMMERCE_COPY_VERSION).toBe('commerce-copy.v2');
  });
});

describe('Step 16 — the disclosure and what the copy never says', () => {
  it('says all three facts, upfront, with Amazon\'s own Associate identification', () => {
    const disclosure = COMMERCE.disclosure.amazon_in;
    expect(disclosure).toBe('Affiliate · As an Amazon Associate I earn from qualifying purchases. This does not affect GlamGenius decisions.');
    expect(disclosure).toMatch(/^Affiliate\b/);
    // Amazon's required sentence, word for word and not reworded.
    expect(disclosure).toContain('As an Amazon Associate I earn from qualifying purchases.');
    expect(disclosure).toContain('This does not affect GlamGenius decisions.');
    expect(disclosure.indexOf('Affiliate')).toBeLessThan(disclosure.indexOf('As an Amazon Associate'));
    expect(disclosure.indexOf('As an Amazon Associate')).toBeLessThan(disclosure.indexOf('This does not affect GlamGenius decisions.'));
    expect(COMMERCE.a11y.action).toMatch(/Affiliate link\./);
  });

  it('has a disclosure for every partner the app will open, and no other', () => {
    expect(Object.keys(COMMERCE.disclosure)).toEqual(COMMERCE_PARTNER_KEYS);
  });

  it('renders the disclosure from the key file, never from the component', () => {
    const component = code('src/components/verdict/CommerceHandoff.tsx');
    expect(component).toContain('COMMERCE.disclosure[handoff.partner.key]');
    expect(component).not.toMatch(/Amazon Associate|qualifying purchases|earn a commission/);
  });

  it('keeps the pack notice verbatim', () => {
    expect(COMMERCE.packNotice).toBe('Re-scan the pack you receive before you use it.');
  });

  it.each([
    // Claims about the listing the app never checked.
    /same product|confirmed|verified|in stock|out of stock|available|availability|delivery|deliver/i,
    // Claims about price, deal or seller.
    /best|cheapest|lowest|price|₹|\brs\b|mrp|deal|offer|coupon|discount|cashback|sale\b|save\b|seller|bestseller/i,
    // Pressure.
    /hurry|now\b|today|limited|only \d|last chance|don'?t miss|while stocks/i,
    // A basket, a payment, an order.
    /cart|basket|checkout|pay\b|payment|order|wallet|upi/i,
    // A recommendation of the listing or the seller, a health claim, a score.
    /recommended|trusted seller|healthy|healthier|safe\b|safer|score|rating|stars?\b/i,
    // Growth.
    /invite|refer|reward|points|share/i,
  ])('never says %s', (pattern) => {
    expect(allCopy.filter((line) => pattern.test(line))).toEqual([]);
  });
});

describe('Step 16 — no SDK, no in-app browser, no stored address', () => {
  const manifest = JSON.parse(read('package.json')) as { dependencies?: Record<string, string>; devDependencies?: Record<string, string> };
  const packages = Object.keys({ ...manifest.dependencies, ...manifest.devDependencies });

  it('adds no merchant, advertising, attribution or analytics SDK', () => {
    const banned = /amazon|flipkart|affiliate|appsflyer|adjust|branch|mixpanel|segment|amplitude|facebook|fbsdk|google-mobile-ads|admob|expo-ads|tracking-transparency|advertising|kochava|singular|clevertap|moengage|webengage|firebase-analytics|razorpay|stripe|paytm|phonepe|juspay|cashfree/i;
    expect(packages.filter((name) => banned.test(name))).toEqual([]);
  });

  it.each(STEP16_FILES)('%s opens no WebView or in-app browser and stores nothing', (relative) => {
    const text = code(relative);
    expect(text).not.toMatch(/react-native-webview|WebView|expo-web-browser|WebBrowser|InAppBrowser|openBrowserAsync|openAuthSessionAsync/);
    expect(text).not.toMatch(/AsyncStorage|SecureStore|localStorage|sessionStorage|MMKV/);
    expect(text).not.toMatch(/console\.(log|info|warn|error|debug)/);
  });

  it('opens through the one external-link allowlist and nothing else', () => {
    const service = code('src/services/commerce.ts');
    const imports = [...service.matchAll(/from '([^']+)'/g)].map((match) => match[1]).sort();
    expect(imports).toEqual(['./api', './apiV2', './externalLinks', './productScan']);
    expect(service).toContain('openExternalUrl(handoff.partner.url)');
    expect(service).not.toMatch(/Linking\.openURL/);
    const component = code('src/components/verdict/CommerceHandoff.tsx');
    expect(component).not.toMatch(/Linking|openURL/);
  });

  it('pins the one partner address the app will open', () => {
    expect(COMMERCE_PARTNER_KEYS).toEqual(['amazon_in']);
    expect(Object.keys(COMMERCE.partners)).toEqual(COMMERCE_PARTNER_KEYS);
    expect(isCommerceDestination('https://www.amazon.in/s?k=8901058000191&tag=glamgenius-21', 'amazon_in', '8901058000191')).toBe(true);
    // The tag is required, in Amazon India's shape.
    expect(isCommerceDestination('https://www.amazon.in/s?k=8901058000191', 'amazon_in', '8901058000191')).toBe(false);
    expect(isCommerceDestination('https://www.amazon.in/s?k=8901058000191&tag=anything', 'amazon_in', '8901058000191')).toBe(false);
  });
});

describe('Step 16 — Commerce and growth stay apart', () => {
  it.each(STEP16_FILES)('%s carries no referral, share or growth', (relative) => {
    const text = code(relative);
    expect(text).not.toMatch(/growth|referral|invite|verdictShare|Share\.share|recordGrowthEvent|newClientEventId/);
  });

  it('growth, the share and the scanner session never mention Commerce', () => {
    for (const relative of ['src/services/growth.ts', 'src/services/verdictShare.ts', 'src/strings/growth.ts', 'src/services/scanSession.ts']) {
      expect(read(relative)).not.toMatch(/commerce|affiliate|amazon/i);
    }
  });

  it('the share is built exactly as before', () => {
    const verdict = read('app/verdict.tsx');
    const from = verdict.indexOf('const onShare = useCallback');
    const to = verdict.indexOf('const scanAnother', from);
    expect(verdict.slice(from, to)).not.toMatch(/commerce|affiliate/i);
  });
});

describe('Step 16 — telemetry carries nothing identifying', () => {
  it('types exactly one event with five closed properties', () => {
    const service = code('src/services/commerce.ts');
    const names = [...service.matchAll(/name: '(commerce\.[a-z_]+)'/g)].map((match) => match[1]);
    expect([...new Set(names)]).toEqual(['commerce.outbound_open']);
    const from = service.indexOf('export function commerceOpenEvent');
    const builder = service.slice(from, service.indexOf('\n}\n', from));
    const keys = [...builder.matchAll(/^\s{6}([a-z_]+):/gm)].map((match) => match[1]).sort();
    expect(keys).toEqual(['affiliate', 'decision', 'partner', 'surface', 'target']);
    expect(builder).not.toMatch(/barcode|url|productName|identity|device|account/i);
  });

  it('never reads a device or advertising identifier for telemetry', () => {
    const service = code('src/services/commerce.ts');
    expect(service).not.toMatch(/deviceKey|device_id|readStoredDevice|installation|advertising|idfa|gaid|X-Device-Token/i);
  });
});

describe('Step 16 — no screen, tab, notification or watch', () => {
  it('adds no route at all: no shop, catalogue or commerce screen', () => {
    const walk = (dir: string, prefix = ''): string[] => fs.readdirSync(dir, { withFileTypes: true })
      .flatMap((entry) => (entry.isDirectory()
        ? walk(path.join(dir, entry.name), `${prefix}${entry.name}/`)
        : [`${prefix}${entry.name}`]));
    // The routes as Step 16 found them. ``shopping-check`` is the existing
    // "Should I buy this?" check, not a shop.
    expect(walk(path.join(ROOT, 'app')).sort()).toEqual([
      '(auth)/_layout.tsx', '(auth)/callback.tsx', '(auth)/registration-incomplete.tsx', '(auth)/welcome.tsx',
      '(tabs)/_layout.tsx', '(tabs)/inventory.tsx', '(tabs)/profile.tsx', '(tabs)/scan.tsx', '(tabs)/you.tsx',
      '_layout.tsx', 'admin-knowledge.tsx', 'admin.tsx', 'event-add.tsx', 'event-ready.tsx', 'for-you-profile.tsx',
      'improve.tsx', 'index.tsx', 'intro.tsx', 'inventory-add.tsx', 'inventory-batch.tsx', 'inventory-insights.tsx',
      'inventory-item.tsx', 'memory.tsx', 'notifications.tsx', 'onboarding.tsx', 'progress.tsx',
      'purchase-candidate.tsx', 'scan-product.tsx', 'scan.tsx', 'shelf.tsx', 'shopping-check.tsx', 'support.tsx',
      'verdict.tsx',
    ].sort());
  });

  it.each(STEP16_FILES)('%s sends no notification and starts no watch', (relative) => {
    expect(code(relative)).not.toMatch(/Notification|notify|pushToken|sendPush|expoPush|scheduleNotification|watchProduct|ProductWatch/);
  });
});
