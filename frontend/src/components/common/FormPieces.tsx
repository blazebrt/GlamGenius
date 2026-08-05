/**
 * Small shared UI primitives that used to live in a payment-specific folder.
 * Kept lean and framework-neutral — no payment concepts.
 */
import React from 'react';
import {
  ActivityIndicator,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

export function ErrorRecovery({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <View style={styles.errorCard}>
      <Ionicons name="alert-circle-outline" size={18} color={COLORS.warning} />
      <View style={{ flex: 1 }}>
        <Text style={styles.errorText}>{message}</Text>
      </View>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel="Try again"
        onPress={onRetry}
      >
        <Text style={styles.retry}>Try again</Text>
      </TouchableOpacity>
    </View>
  );
}

export function PrimaryButton({
  label,
  onPress,
  busy = false,
  disabled = false,
}: {
  label: string;
  onPress: () => void;
  busy?: boolean;
  disabled?: boolean;
}) {
  return (
    <TouchableOpacity
      testID="primary-button"
      accessibilityRole="button"
      accessibilityLabel={label}
      style={[styles.button, (busy || disabled) && styles.buttonDisabled]}
      onPress={onPress}
      disabled={busy || disabled}
    >
      {busy ? (
        <ActivityIndicator color={COLORS.white} />
      ) : (
        <Text style={styles.buttonText}>{label}</Text>
      )}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  errorCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: SPACING.sm,
    padding: 14,
    borderRadius: RADIUS.md,
    backgroundColor: COLORS.card,
    borderWidth: 1,
    borderColor: COLORS.border,
    marginVertical: 10,
  },
  errorText: {
    fontFamily: FONTS.family.body,
    fontSize: 13,
    color: COLORS.textPrimary,
    lineHeight: 19,
  },
  retry: {
    fontFamily: FONTS.family.bodySemibold,
    fontSize: 13,
    color: COLORS.primary,
  },
  button: {
    marginTop: SPACING.md,
    backgroundColor: COLORS.primary,
    borderRadius: RADIUS.lg,
    paddingVertical: 14,
    alignItems: 'center',
  },
  buttonDisabled: { opacity: 0.6 },
  buttonText: {
    fontFamily: FONTS.family.bodySemibold,
    fontSize: 15,
    color: COLORS.white,
  },
});
