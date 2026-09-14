import React from 'react';
import { render } from '@testing-library/react-native';
import ShoppingCheckScreen from '../../app/shopping-check';
import * as fs from 'fs';
import * as path from 'path';

let redirectHref = '';
jest.mock('expo-router', () => ({
  Redirect: (props: any) => { redirectHref = props.href; return null; },
}));

describe('Frontend Pivot Lock', () => {
  it('shopping-check is a deterministic Scan redirect only', () => {
    redirectHref = '';
    const { toJSON } = render(<ShoppingCheckScreen />);
    expect(toJSON()).toBeNull();
    expect(redirectHref).toBe('/scan');
  });
});

describe('Frontend Legacy Routing', () => {
  const appDir = path.resolve(__dirname, '../../app');

  it('no Style/Look/wardrobe customer screen exists', () => {
    expect(fs.existsSync(path.join(appDir, '(tabs)', 'style.tsx'))).toBe(false);
    expect(fs.existsSync(path.join(appDir, '(tabs)', 'plan.tsx'))).toBe(false);
    expect(fs.existsSync(path.join(appDir, 'look.tsx'))).toBe(false);
    expect(fs.existsSync(path.join(appDir, 'wardrobe.tsx'))).toBe(false);
    expect(fs.existsSync(path.join(appDir, '(tabs)', 'today.tsx'))).toBe(false);
    expect(fs.existsSync(path.join(appDir, '(tabs)', 'care.tsx'))).toBe(false);
  });

  it('Scan remains the primary product entry and You remains the other primary tab', () => {
    expect(fs.existsSync(path.join(appDir, '(tabs)', 'scan.tsx'))).toBe(true);
    expect(fs.existsSync(path.join(appDir, '(tabs)', 'you.tsx'))).toBe(true);
  });

  it('proves legacy routes are safe redirects', () => {
    const memory = fs.readFileSync(path.join(appDir, 'memory.tsx'), 'utf-8');
    expect(memory).toContain("<Redirect href='/(tabs)/you' />");

    const progress = fs.readFileSync(path.join(appDir, 'progress.tsx'), 'utf-8');
    expect(progress).toContain("<Redirect href='/(tabs)/you' />");

    const eventReady = fs.readFileSync(path.join(appDir, 'event-ready.tsx'), 'utf-8');
    expect(eventReady).toContain("<Redirect href='/scan' />");

    const eventAdd = fs.readFileSync(path.join(appDir, 'event-add.tsx'), 'utf-8');
    expect(eventAdd).toContain("<Redirect href='/scan' />");
  });

  it('proves zero free-text for retired product inputs by checking for TextInput', () => {
    const checkNoTextInput = (file: string) => {
      const content = fs.readFileSync(path.join(appDir, file), 'utf-8');
      expect(content).not.toMatch(/<TextInput/);
    };
    checkNoTextInput('memory.tsx');
    checkNoTextInput('event-add.tsx');
    checkNoTextInput('event-ready.tsx');
    checkNoTextInput('shopping-check.tsx');
    checkNoTextInput('for-you-profile.tsx');
  });
});
