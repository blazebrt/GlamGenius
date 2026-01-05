/**
 * PREMIUM BLACK & WHITE DESIGN SYSTEM
 * ====================================
 * 
 * AESTHETIC: High-end luxury salon meets clinical precision
 * INSPIRATION: Chanel, Aesop, high-end dermatology clinics
 * 
 * FONT PAIRING:
 * - Playfair Display (Headings): Editorial elegance, fashion magazine feel
 * - Inter (Body): Clean, precise, medical-grade readability
 */

// ============================================
// COLOR PALETTE - PREMIUM BLACK & WHITE
// ============================================
export const COLORS = {
  // Backgrounds
  background: '#FFFFFF',
  backgroundSecondary: '#FAFAFA',
  backgroundTertiary: '#F5F5F5',
  
  // Cards
  card: '#FFFFFF',
  cardElevated: '#FFFFFF',
  cardDark: '#1A1A1A',
  
  // Primary - Pure Black (Premium)
  primary: '#000000',
  primaryLight: '#F5F5F5',
  primaryMuted: '#E5E5E5',
  primaryDark: '#000000',
  
  // Accent - Charcoal tones
  accent: '#2D2D2D',
  accentLight: '#F0F0F0',
  accentMuted: '#D4D4D4',
  accentDark: '#171717',
  
  // Text - High Contrast
  textPrimary: '#0A0A0A',
  textSecondary: '#525252',
  textMuted: '#A3A3A3',
  textInverse: '#FFFFFF',
  
  // Status - Monochrome versions
  success: '#171717',
  successLight: '#F5F5F5',
  successMuted: '#E5E5E5',
  
  warning: '#404040',
  warningLight: '#FAFAFA',
  warningMuted: '#E5E5E5',
  
  error: '#1A1A1A',
  errorLight: '#F5F5F5',
  errorMuted: '#E5E5E5',
  
  info: '#262626',
  infoLight: '#FAFAFA',
  infoMuted: '#E5E5E5',
  
  // Health Score - Grayscale gradient
  healthExcellent: '#000000',
  healthGood: '#262626',
  healthFair: '#525252',
  healthPoor: '#737373',
  healthCritical: '#A3A3A3',
  
  // Borders - Subtle definition
  border: '#E5E5E5',
  borderLight: '#F5F5F5',
  borderDark: '#D4D4D4',
  borderFocus: '#000000',
  
  // Utility
  white: '#FFFFFF',
  black: '#000000',
  overlay: 'rgba(0, 0, 0, 0.6)',
  overlayLight: 'rgba(0, 0, 0, 0.3)',
  
  // Scan Interface
  scanRing: '#FFFFFF',
  scanSuccess: '#FFFFFF',
  scanProcessing: '#FFFFFF',
  scanBackground: '#000000',
};

// ============================================
// TYPOGRAPHY - PREMIUM SCALE
// ============================================
export const FONTS = {
  family: {
    // Premium Editorial Serif
    heading: 'PlayfairDisplay_700Bold',
    headingMedium: 'PlayfairDisplay_500Medium',
    headingRegular: 'PlayfairDisplay_400Regular',
    
    // Clean Geometric Sans
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

// ============================================
// SPACING - 8pt Grid
// ============================================
export const SPACING = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
  '2xl': 48,
  '3xl': 64,
};

// ============================================
// BORDER RADIUS - Refined
// ============================================
export const RADIUS = {
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  '2xl': 24,
  full: 9999,
};

// ============================================
// SHADOWS - Subtle & Elegant
// ============================================
export const SHADOWS = {
  sm: {
    shadowColor: '#000000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.04,
    shadowRadius: 3,
    elevation: 1,
  },
  md: {
    shadowColor: '#000000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.06,
    shadowRadius: 8,
    elevation: 3,
  },
  lg: {
    shadowColor: '#000000',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.08,
    shadowRadius: 16,
    elevation: 6,
  },
};
