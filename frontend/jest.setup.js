/* eslint-env jest */
/**
 * Jest setup.
 *
 * Native modules that have no meaning in a test process are stubbed here rather
 * than in each test, so a test file only has to describe the behaviour it is
 * actually checking.
 */

// @expo/vector-icons reaches expo-asset through expo-font, which is not
// resolvable in a test process. Icons are decorative here — every assertion is
// on text or testID — so a stub is enough, and it avoids adding a runtime
// dependency purely to satisfy the test runner.
jest.mock('@expo/vector-icons', () => {
  const React = require('react');
  const { View } = require('react-native');
  const Icon = ({ name, ...rest }) =>
    React.createElement(View, { accessibilityLabel: `icon-${name}`, ...rest });
  return {
    Ionicons: Icon,
    MaterialIcons: Icon,
    FontAwesome: Icon,
    AntDesign: Icon,
    Feather: Icon,
  };
});

// expo-router: navigation is a side effect, not something these tests assert on.
jest.mock('expo-router', () => ({
  Redirect: ({ href }) => {
    const React = require('react');
    const { View } = require('react-native');
    return React.createElement(View, { testID: `redirect:${href}`, accessibilityLabel: `redirect:${href}` });
  },
  router: { push: jest.fn(), replace: jest.fn(), back: jest.fn(), canGoBack: () => true },
  useRouter: () => ({
    push: jest.fn(),
    replace: jest.fn(),
    back: jest.fn(),
    canGoBack: () => true,
  }),
  useLocalSearchParams: () => ({}),
  Stack: { Screen: () => null },
  Tabs: { Screen: () => null },
  Link: 'Link',
}));

jest.mock('react-native-safe-area-context', () => {
  const inset = { top: 0, right: 0, bottom: 0, left: 0 };
  return {
    SafeAreaProvider: ({ children }) => children,
    SafeAreaView: ({ children }) => children,
    useSafeAreaInsets: () => inset,
  };
});

jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(() => Promise.resolve(null)),
  setItem: jest.fn(() => Promise.resolve()),
  removeItem: jest.fn(() => Promise.resolve()),
  multiRemove: jest.fn(() => Promise.resolve()),
}));

// Silence the "not implemented" noise from the RN animation driver in tests.
jest.mock('react-native/Libraries/Animated/NativeAnimatedHelper', () => ({}), {
  virtual: true,
});
