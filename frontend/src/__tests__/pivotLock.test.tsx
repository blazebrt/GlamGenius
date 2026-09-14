import React from 'react';
import { render } from '@testing-library/react-native';
import ShoppingCheckScreen from '../../app/shopping-check';
import { Redirect } from 'expo-router';

jest.mock('expo-router', () => ({
  Redirect: () => { return null; },
}));

describe('Frontend Pivot Lock', () => {
  it('shopping-check is a deterministic Scan redirect only', () => {
    const { toJSON } = render(<ShoppingCheckScreen />);
    expect(toJSON()).toBeNull(); // Because Redirect is mocked to return null
  });
});
import * as fs from 'fs';
import * as path from 'path';

describe('Frontend Legacy Routing', () => {
  const appDir = path.resolve(__dirname, '../../app');
  
  it('no Style/Look/wardrobe customer screen exists', () => {
    expect(fs.existsSync(path.join(appDir, '(tabs)', 'style.tsx'))).toBe(false);
    expect(fs.existsSync(path.join(appDir, '(tabs)', 'plan.tsx'))).toBe(false);
    expect(fs.existsSync(path.join(appDir, 'look.tsx'))).toBe(false);
    expect(fs.existsSync(path.join(appDir, 'wardrobe.tsx'))).toBe(false);
  });

  it('Scan remains the primary product entry and You remains the other primary tab', () => {
    expect(fs.existsSync(path.join(appDir, '(tabs)', 'scan-product.tsx'))).toBe(true);
    expect(fs.existsSync(path.join(appDir, '(tabs)', 'you.tsx'))).toBe(true);
  });

  it('no Today/Style/Care/Plan primary tabs return', () => {
    expect(fs.existsSync(path.join(appDir, '(tabs)', 'today.tsx'))).toBe(false);
    expect(fs.existsSync(path.join(appDir, '(tabs)', 'style.tsx'))).toBe(false);
    expect(fs.existsSync(path.join(appDir, '(tabs)', 'care.tsx'))).toBe(false);
    expect(fs.existsSync(path.join(appDir, '(tabs)', 'plan.tsx'))).toBe(false);
  });
});
