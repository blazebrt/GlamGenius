/**
 * Step 14's surface rules: scan stays primary, no new tab, every word keyed.
 */
import React from 'react';
import * as fs from 'fs';
import * as path from 'path';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import ScanProductScreen from '../../app/scan-product';
import { PURCHASE_OS, PURCHASE_OS_COPY_VERSION } from '../strings/purchaseOs';

const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush, replace: jest.fn(), back: jest.fn() }),
  useFocusEffect: jest.fn(),
}));
jest.mock('expo-camera', () => {
  const { View } = jest.requireActual('react-native');
  return {
    CameraView: View,
    useCameraPermissions: () => [{ granted: true, canAskAgain: true }, jest.fn()],
  };
});
jest.mock('expo-image-picker', () => ({
  requestCameraPermissionsAsync: jest.fn(), launchCameraAsync: jest.fn(),
}));
jest.mock('../services/productScan', () => ({
  confirmLabel: jest.fn(), confirmSkinCareLabel: jest.fn(), ensureDevice: jest.fn(async () => null),
  ensureDeviceClaimed: jest.fn(), fetchSkinCareForYou: jest.fn(), newScanId: () => 'scan-1',
  readQueue: jest.fn(async () => []), settleScanEvents: jest.fn(), scanBarcode: jest.fn(),
  syncQueue: jest.fn(async () => undefined),
}));
jest.mock('../services/apiV2', () => ({
  transcribeProductLabel: jest.fn(), transcribeSkinCareLabel: jest.fn(), uploadMedia: jest.fn(),
}));
let mockUserId: string | null = 'account-1';
jest.mock('../store/userStore', () => ({
  useUserStore: () => ({ userId: mockUserId }),
}));

const ROOT = path.resolve(__dirname, '..', '..');
const read = (relative: string) => fs.readFileSync(path.join(ROOT, relative), 'utf8');

beforeEach(() => {
  jest.clearAllMocks();
  mockUserId = 'account-1';
});

describe('Step 14 — scan stays primary', () => {
  it('offers the candidate check as a secondary entry below the scan instruction', async () => {
    render(<ScanProductScreen />);
    const entry = await waitFor(() => screen.getByTestId('purchase-candidate-entry'));
    expect(entry).toBeTruthy();
    const json = JSON.stringify(screen.toJSON());
    expect(json.indexOf('Point at a barcode')).toBeLessThan(json.indexOf(PURCHASE_OS.scanEntry.action));
    fireEvent.press(screen.getByRole('button', { name: PURCHASE_OS.scanEntry.action }));
    expect(mockPush).toHaveBeenCalledWith('/purchase-candidate');
  });

  it('does not offer the candidate check to an anonymous scanner', async () => {
    mockUserId = null;
    render(<ScanProductScreen />);
    await waitFor(() => expect(screen.getByText('Point at a barcode')).toBeTruthy());
    expect(screen.queryByTestId('purchase-candidate-entry')).toBeNull();
  });

  it('adds no top-level tab: Scan and You remain the only two', () => {
    const layout = read('app/(tabs)/_layout.tsx');
    const names = [...layout.matchAll(/<Tabs\.Screen\s+name="([^"]+)"/g)].map((match) => match[1]);
    expect(names).toEqual(['scan', 'you']);
    const tabFiles = fs.readdirSync(path.join(ROOT, 'app', '(tabs)'));
    expect(tabFiles.filter((file) => /purchase|shopping|market|shop/i.test(file))).toEqual([]);
    expect(fs.existsSync(path.join(ROOT, 'app', 'purchase-candidate.tsx'))).toBe(true);
    // The retired shopping-check route stays a redirect to Scan.
    expect(read('app/shopping-check.tsx')).toContain("<Redirect href='/scan' />");
  });

  it('builds no free-text product search, catalogue or feed', () => {
    // Code only: the file's own comments say what it refuses to be.
    const code = read('app/purchase-candidate.tsx').replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
    expect(code).not.toMatch(/search|catalogue|catalog|discover|feed|FlatList/i);
  });
});

describe('Step 14 — every customer word is keyed', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const ts = require('typescript') as typeof import('typescript');
  const PROSE_PROPS = new Set(['accessibilityLabel', 'accessibilityHint', 'title', 'placeholder', 'label']);
  const readsAsProse = (value: string) => {
    const text = value.trim();
    return text.split(/\s+/).length >= 2 && (/^[A-Z]/.test(text) || /[.!?]$/.test(text));
  };
  const strayIn = (relative: string, text: string = read(relative)): string[] => {
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
  };

  it.each([
    'app/purchase-candidate.tsx',
    'src/components/verdict/PurchaseContextSection.tsx',
    'src/services/purchaseOsModel.ts',
  ])('%s carries no customer prose of its own', (relative) => {
    expect(strayIn(relative)).toEqual([]);
  });

  it('the Scan entry and the Product Result section read their words from the key file', () => {
    const scan = read('app/scan-product.tsx');
    expect(scan).toContain('PURCHASE_OS.scanEntry.action');
    expect(scan).not.toContain(PURCHASE_OS.scanEntry.action);
    const verdict = read('app/verdict.tsx');
    expect(verdict).not.toContain(PURCHASE_OS.context.title);
  });

  it('the scanner catches a hard-coded sentence in each place one could hide', () => {
    const planted = [
      "const a = <Text>Buy it today</Text>;",
      "const b = <Button accessibilityLabel=\"Open the record\" />;",
      "const c = 'This pack is fine.';",
      "import { X } from 'Some Module Name';",
      "const d = 'context_only';",
    ].join('\n');
    expect([...new Set(strayIn('planted.tsx', planted))]).toEqual(['Buy it today', 'Open the record', 'This pack is fine.']);
  });

  it('the copy is versioned', () => {
    expect(PURCHASE_OS_COPY_VERSION).toBe('purchase-os-copy.v2');
  });
});

describe('Step 14 — what the copy never says', () => {
  const all: string[] = [];
  const collect = (value: unknown) => {
    if (typeof value === 'string') all.push(value);
    else if (value && typeof value === 'object') Object.values(value).forEach(collect);
  };
  collect(PURCHASE_OS);

  it.each([
    // Commerce and growth.
    /add to cart|buy now|shop now|checkout|coupon|discount|deal|offer|in stock|order|affiliate|invite|refer|share/i,
    // Diagnosis, safety, benefit and dosage claims.
    /\bsafe\b|unsafe|healthy|healthier|cure|treat|dose|dosage|diagnos|benefit|recommended for you/i,
    // A release rule for the official record that no reviewed authority provides.
    /cleared|lifted|no longer applies|resolved|until/i,
    // A score.
    /score|rating|points/i,
  ])('never says %s', (pattern) => {
    expect(all.filter((line) => pattern.test(line))).toEqual([]);
  });
});
