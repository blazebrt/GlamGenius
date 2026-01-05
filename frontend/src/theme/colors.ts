/**
 * MED-TECH BEAUTY DESIGN SYSTEM
 * =============================
 * 
 * FONT PAIRING RATIONALE:
 * 
 * 1. HEADINGS: Playfair Display (Serif)
 *    - WHY: Evokes luxury fashion magazines like Vogue & Harper's Bazaar
 *    - EFFECT: Creates immediate perception of premium, editorial quality
 *    - USE: Headlines, feature titles, price tags (for luxury positioning)
 *    - PSYCHOLOGY: Serif fonts signal tradition, trust, and expertise—essential 
 *      for medical credibility while maintaining salon elegance
 * 
 * 2. BODY TEXT: Inter (Geometric Sans-Serif)
 *    - WHY: Designed for screen legibility, used in medical/tech interfaces
 *    - EFFECT: Clean, precise, data-friendly—like clinical reports
 *    - USE: Descriptions, data, buttons, navigation
 *    - PSYCHOLOGY: Sans-serif signals modernity and precision—perfect for
 *      communicating medical accuracy and scientific credibility
 * 
 * WHY THIS PAIRING WORKS FOR MEDICAL-SALON:
 * - The contrast creates visual hierarchy: "luxury experience" (Playfair) 
 *   meets "scientific precision" (Inter)
 * - Playfair says "you're at a high-end salon" while Inter says "backed by science"
 * - This duality addresses the customer's dual desire: pampering + results
 * 
 * COLOR PALETTE RATIONALE:
 * - Soft medical blue: Clinical trust, calming, reduces anxiety
 * - Platinum/Silver: Premium, modern technology, metallic luxury
 * - White space: Clinical cleanliness, focus, breathing room
 * - Teal accents: Health, vitality, positive outcomes
 */

// ============================================
// COLOR PALETTE - CLINICAL LUXURY
// ============================================
export const COLORS = {
  // Backgrounds - Clean Clinical White
  background: '#FFFFFF',
  backgroundSecondary: '#F8FAFC',
  backgroundTertiary: '#F1F5F9',
  
  // Cards - Glassmorphism Ready
  card: '#FFFFFF',
  cardElevated: 'rgba(255, 255, 255, 0.85)',
  cardGlass: 'rgba(255, 255, 255, 0.7)',
  
  // Primary - Medical Blue (Trust & Calm)
  primary: '#3B82F6',
  primaryLight: '#EFF6FF',
  primaryMuted: '#BFDBFE',
  primaryDark: '#1D4ED8',
  
  // Accent - Platinum/Silver (Premium Tech)
  accent: '#64748B',
  accentLight: '#F1F5F9',
  accentMuted: '#CBD5E1',
  accentDark: '#475569',
  
  // Text - High Contrast for Medical Readability
  textPrimary: '#0F172A',
  textSecondary: '#475569',
  textMuted: '#94A3B8',
  textInverse: '#FFFFFF',
  
  // Status - Soft Medical Tones
  success: '#0D9488',
  successLight: '#CCFBF1',
  successMuted: '#5EEAD4',
  
  warning: '#D97706',
  warningLight: '#FEF3C7',
  warningMuted: '#FCD34D',
  
  error: '#DC2626',
  errorLight: '#FEE2E2',
  errorMuted: '#FCA5A5',
  
  info: '#0EA5E9',
  infoLight: '#E0F2FE',
  infoMuted: '#7DD3FC',
  
  // Health Score Gradient
  healthExcellent: '#10B981',
  healthGood: '#34D399',
  healthFair: '#FBBF24',
  healthPoor: '#F97316',
  healthCritical: '#EF4444',
  
  // Borders - Subtle Definition
  border: '#E2E8F0',
  borderLight: '#F1F5F9',
  borderFocus: '#3B82F6',
  
  // Utility
  white: '#FFFFFF',
  black: '#0F172A',
  overlay: 'rgba(15, 23, 42, 0.6)',
  overlayLight: 'rgba(15, 23, 42, 0.3)',
  
  // Scan Interface Colors
  scanRing: '#3B82F6',
  scanSuccess: '#10B981',
  scanProcessing: '#8B5CF6',
  scanBackground: 'rgba(0, 0, 0, 0.85)',
};

// ============================================
// TYPOGRAPHY SCALE - MEDICAL PRECISION
// ============================================
export const FONTS = {
  // Font Families
  family: {
    // Premium Editorial Serif - Headings
    heading: 'PlayfairDisplay_700Bold',
    headingMedium: 'PlayfairDisplay_500Medium',
    headingRegular: 'PlayfairDisplay_400Regular',
    
    // Geometric Sans - Body & Data
    body: 'Inter_400Regular',
    bodyMedium: 'Inter_500Medium',
    bodySemibold: 'Inter_600SemiBold',
    bodyBold: 'Inter_700Bold',
  },
  
  // Size Scale - Larger for Readability
  sizes: {
    // Display - Hero Headlines
    display: 42,
    displaySm: 36,
    
    // Headings
    h1: 32,
    h2: 26,
    h3: 22,
    h4: 18,
    
    // Body
    bodyLg: 17,
    body: 15,
    bodySm: 13,
    
    // Micro
    caption: 12,
    micro: 10,
  },
  
  // Line Heights
  lineHeights: {
    tight: 1.2,
    normal: 1.5,
    relaxed: 1.7,
  },
  
  // Letter Spacing
  letterSpacing: {
    tight: -0.5,
    normal: 0,
    wide: 0.5,
    wider: 1,
    widest: 2,
  },
};

// ============================================
// SPACING SYSTEM - 8pt Grid
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
// BORDER RADIUS - Soft Clinical
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
// SHADOWS - Subtle Elevation
// ============================================
export const SHADOWS = {
  sm: {
    shadowColor: '#0F172A',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05,
    shadowRadius: 2,
    elevation: 1,
  },
  md: {
    shadowColor: '#0F172A',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.08,
    shadowRadius: 8,
    elevation: 3,
  },
  lg: {
    shadowColor: '#0F172A',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.12,
    shadowRadius: 16,
    elevation: 6,
  },
};
