// Black & White Medical Beauty + Salon Theme
export const COLORS = {
  // Backgrounds
  background: '#FFFFFF',
  backgroundSecondary: '#FAFAFA',
  card: '#FFFFFF',
  
  // Text
  textPrimary: '#1A1A1A',
  textSecondary: '#4A4A4A',
  textMuted: '#8A8A8A',
  
  // Accent - Black/Charcoal for medical-beauty vibe
  primary: '#1A1A1A',
  primaryLight: '#F5F5F5',
  primaryDark: '#000000',
  
  // Status
  success: '#2D2D2D',
  successLight: '#E8E8E8',
  warning: '#4A4A4A',
  warningLight: '#F0F0F0',
  error: '#3D3D3D',
  errorLight: '#F5F5F5',
  
  // Borders
  border: '#E0E0E0',
  borderLight: '#F0F0F0',
  
  // Others
  white: '#FFFFFF',
  black: '#000000',
  overlay: 'rgba(0, 0, 0, 0.5)',
  
  // Medical accent - subtle gray tones
  accent: '#2C2C2C',
  accentLight: '#EBEBEB',
};

// Typography Scale - Larger sizes
export const FONTS = {
  sizes: {
    xs: 13,
    sm: 15,
    base: 17,
    lg: 19,
    xl: 22,
    '2xl': 26,
    '3xl': 32,
    '4xl': 40,
  },
  weights: {
    regular: '400' as const,
    medium: '500' as const,
    semibold: '600' as const,
    bold: '700' as const,
    extrabold: '800' as const,
  },
  family: {
    regular: 'NunitoSans_400Regular',
    medium: 'NunitoSans_500Medium',
    semibold: 'NunitoSans_600SemiBold',
    bold: 'NunitoSans_700Bold',
    extrabold: 'NunitoSans_800ExtraBold',
  },
};
