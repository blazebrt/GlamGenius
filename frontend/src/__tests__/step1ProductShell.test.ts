import { existsSync, readFileSync } from 'fs';
import { join } from 'path';

const appRoot = join(__dirname, '..', '..', 'app');
const sourceRoot = join(__dirname, '..');

describe('Step 1 scan-first product shell', () => {
  it('sends both authenticated and unauthenticated launches to the scanner', () => {
    const launch = readFileSync(join(appRoot, 'index.tsx'), 'utf8');
    expect(launch).toContain("router.replace('/scan-product')");
    expect(launch).not.toContain("router.replace('/(tabs)/today')");
  });

  it('keeps the current scanner capabilities rather than replacing them', () => {
    const scanner = readFileSync(join(appRoot, 'scan-product.tsx'), 'utf8');
    const service = readFileSync(join(sourceRoot, 'services', 'productScan.ts'), 'utf8');
    expect(scanner).toContain('scanBarcode');
    expect(scanner).toContain('transcribeProductLabel');
    expect(scanner).toContain('confirmLabel');
    expect(scanner).toContain("pathname: '/verdict'");
    expect(service).toContain('syncQueue');
    expect(service).toContain('ensureDevice');
    expect(service).toContain('offline');
  });

  it('removes rejected Style entry routes while retaining the scanner', () => {
    const legacyScan = readFileSync(join(appRoot, 'scan.tsx'), 'utf8');
    expect(existsSync(join(appRoot, '(tabs)', 'style.tsx'))).toBe(false);
    expect(existsSync(join(appRoot, 'look.tsx'))).toBe(false);
    expect(legacyScan).toContain('<Redirect href="/scan-product"');
  });

  it('removes retired tab files and keeps the product shelf Care-only', () => {
    const inventory = readFileSync(join(appRoot, '(tabs)', 'inventory.tsx'), 'utf8');
    const add = readFileSync(join(appRoot, 'inventory-add.tsx'), 'utf8');
    const item = readFileSync(join(appRoot, 'inventory-item.tsx'), 'utf8');
    expect(existsSync(join(appRoot, '(tabs)', 'home.tsx'))).toBe(false);
    expect(existsSync(join(appRoot, '(tabs)', 'today.tsx'))).toBe(false);
    expect(inventory).toContain('PRODUCT SHELF');
    expect(add).toContain("params.domain !== 'care'");
    expect(item).toContain("['beauty', 'hair', 'perfumes', 'supplements']");
  });
});
