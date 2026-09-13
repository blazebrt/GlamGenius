/**
 * Which links the app is willing to follow.
 *
 * Every URL it opens arrives as data - an evidence source, an official record,
 * a citation, a brand's reply page, an Open Food Facts entry. ``openURL`` hands
 * whatever it receives to the operating system, which acts on far more than web
 * addresses, so a row in a database could otherwise make a phone read local
 * storage, reach another installed app, or run script in an in-app browser.
 */
import { Linking } from 'react-native';

import { isSafeExternalUrl, openExternalUrl } from '../services/externalLinks';

jest.mock('react-native', () => ({
  Linking: { openURL: jest.fn(async () => undefined) },
}));

const openURL = Linking.openURL as jest.Mock;

beforeEach(() => jest.clearAllMocks());

describe('links the app follows', () => {
  it.each([
    'https://openfoodfacts.org/product/8901058000191',
    'http://fssai.gov.in/notification',
    'https://example.org/a?b=c#d',
    'HTTPS://EXAMPLE.ORG/shouting',
  ])('opens %s', async (url) => {
    expect(isSafeExternalUrl(url)).toBe(true);
    await expect(openExternalUrl(url)).resolves.toBe(true);
    expect(openURL).toHaveBeenCalledWith(url.trim());
  });

  it('trims surrounding whitespace rather than refusing a usable link', async () => {
    await expect(openExternalUrl('  https://example.org/x  ')).resolves.toBe(true);
    expect(openURL).toHaveBeenCalledWith('https://example.org/x');
  });
});

describe('links the app refuses', () => {
  it.each([
    ['javascript:alert(1)', 'runs script in some in-app browsers'],
    ['file:///etc/passwd', 'reads local storage'],
    ['intent://scan/#Intent;scheme=zxing;end', 'reaches another installed app on Android'],
    ['content://com.android.contacts/contacts', 'reads a content provider'],
    ['glamgenius://settings', 'a custom scheme is still not a web link'],
    ['data:text/html;base64,PHNjcmlwdD4=', 'inline document'],
    ['tel:+910000000000', 'places a call'],
    ['sms:+910000000000', 'sends a message'],
    ['//example.org/x', 'scheme-relative, resolves unpredictably'],
    ['example.org', 'no scheme at all'],
    ['', 'empty'],
    ['https://example.org/' + String.fromCharCode(10) + 'javascript:alert(1)', 'embedded newline'],
    ['https://exa mple.org', 'embedded space'],
    ['https://example.org/' + String.fromCharCode(0), 'embedded null'],
  ])('refuses %s (%s)', async (url) => {
    expect(isSafeExternalUrl(url)).toBe(false);
    await expect(openExternalUrl(url)).resolves.toBe(false);
    expect(openURL).not.toHaveBeenCalled();
  });

  it.each([null, undefined, 42, {}, [], true])('refuses the non-string %p', async (value) => {
    expect(isSafeExternalUrl(value)).toBe(false);
    await expect(openExternalUrl(value)).resolves.toBe(false);
    expect(openURL).not.toHaveBeenCalled();
  });

  it('never rewrites a refused link into an allowed one', async () => {
    await openExternalUrl('javascript:alert(1)');
    expect(openURL).not.toHaveBeenCalled();
  });
});

describe('when the device cannot open it', () => {
  it('reports failure rather than throwing into a screen', async () => {
    openURL.mockRejectedValueOnce(new Error('no handler'));
    await expect(openExternalUrl('https://example.org/x')).resolves.toBe(false);
  });
});
