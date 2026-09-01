import { readFileSync } from 'fs';
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

  it('quarantines rejected Style entry routes at Scan', () => {
    const style = readFileSync(join(appRoot, '(tabs)', 'style.tsx'), 'utf8');
    const legacyScan = readFileSync(join(appRoot, 'scan.tsx'), 'utf8');
    expect(style).toContain('<Redirect href="/scan-product"');
    expect(legacyScan).toContain('<Redirect href="/scan-product"');
  });

  it('keeps only explicit Care inventory entry points and quarantines retired routes', () => {
    const home = readFileSync(join(appRoot, '(tabs)', 'home.tsx'), 'utf8');
    const today = readFileSync(join(appRoot, '(tabs)', 'today.tsx'), 'utf8');
    const inventory = readFileSync(join(appRoot, '(tabs)', 'inventory.tsx'), 'utf8');
    const add = readFileSync(join(appRoot, 'inventory-add.tsx'), 'utf8');
    const insights = readFileSync(join(appRoot, 'inventory-insights.tsx'), 'utf8');
    const item = readFileSync(join(appRoot, 'inventory-item.tsx'), 'utf8');
    const batch = readFileSync(join(appRoot, 'inventory-batch.tsx'), 'utf8');
    expect(home).toContain('<Redirect href="/scan-product" />');
    expect(today).toContain('<Redirect href="/scan-product" />');
    expect(inventory).toContain("params.domain !== 'care'");
    expect(add).toContain("params.domain !== 'care'");
    expect(insights).toContain("params.domain !== 'care'");
    expect(item).toContain("['beauty', 'hair', 'perfumes', 'supplements']");
    expect(batch).toContain('<Redirect href="/scan-product" />');
  });
});
