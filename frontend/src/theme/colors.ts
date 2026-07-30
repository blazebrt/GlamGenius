/**
 * GlamGenius Design System
 * Personal stylist + skin & hair wellness coach (India)
 * Warm earth + deep teal — calm, non-clinical, non-glam marketing
 */

export const COLORS = {
  background: '#F7F3EC',
  backgroundSecondary: '#EFE8DC',
  backgroundTertiary: '#E5DCCE',

  card: '#FFFCF7',
  cardElevated: '#FFFFFF',
  cardDark: '#1C1917',

  primary: '#0F5C4C',
  primaryLight: '#E4F0EC',
  primaryMuted: '#B7D4CB',
  primaryDark: '#0A3D34',

  accent: '#C45C26',
  accentLight: '#F8E8DE',
  accentMuted: '#E8C4AE',
  accentDark: '#8A3A12',

  textPrimary: '#1C1917',
  textSecondary: '#57534E',
  textMuted: '#A8A29E',
  textInverse: '#FFFCF7',

  success: '#0F5C4C',
  successLight: '#E4F0EC',
  successMuted: '#B7D4CB',

  warning: '#B45309',
  warningLight: '#FEF3C7',
  warningMuted: '#FDE68A',

  error: '#B91C1C',
  errorLight: '#FEE2E2',
  errorMuted: '#FECACA',

  info: '#1E3A5F',
  infoLight: '#E8EEF5',
  infoMuted: '#C5D0DE',

  healthExcellent: '#0F5C4C',
  healthGood: '#3D7A6C',
  healthFair: '#B45309',
  healthPoor: '#C45C26',
  healthCritical: '#B91C1C',

  border: '#E7DFD3',
  borderLight: '#F0EAE0',
  borderDark: '#D6CBB8',
  borderFocus: '#0F5C4C',

  white: '#FFFFFF',
  black: '#1C1917',
  overlay: 'rgba(28, 25, 23, 0.55)',
  overlayLight: 'rgba(28, 25, 23, 0.28)',

  scanRing: '#FFFFFF',
  scanSuccess: '#FFFFFF',
  scanProcessing: '#FFFFFF',
  scanBackground: '#1C1917',
};

export const FONTS = {
  family: {
    heading: 'PlayfairDisplay_700Bold',
    headingMedium: 'PlayfairDisplay_500Medium',
    headingRegular: 'PlayfairDisplay_400Regular',
    body: 'Inter_400Regular',
    bodyMedium: 'Inter_500Medium',
    bodySemibold: 'Inter_600SemiBold',
    bodyBold: 'Inter_700Bold',
  },
  sizes: {
    display: 42,
    displaySm: 36,
    h1: 32,
    h2: 26,
    h3: 22,
    h4: 18,
    bodyLg: 17,
    body: 15,
    bodySm: 13,
    caption: 12,
    micro: 10,
  },
  lineHeights: {
    tight: 1.2,
    normal: 1.5,
    relaxed: 1.7,
  },
  letterSpacing: {
    tight: -0.5,
    normal: 0,
    wide: 0.5,
    wider: 1,
    widest: 2,
  },
};

export const SPACING = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
  '2xl': 48,
  '3xl': 64,
};

export const RADIUS = {
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  '2xl': 24,
  full: 9999,
};

export const SHADOWS = {
  sm: {
    shadowColor: '#1C1917',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05,
    shadowRadius: 3,
    elevation: 1,
  },
  md: {
    shadowColor: '#1C1917',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.07,
    shadowRadius: 10,
    elevation: 3,
  },
  lg: {
    shadowColor: '#1C1917',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.09,
    shadowRadius: 18,
    elevation: 6,
  },
};
