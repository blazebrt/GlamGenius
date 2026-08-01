/**
 * Reusable states for everything that can go wrong, and everything that takes
 * time.
 *
 * The audit found one generic toast — "Could not complete analysis. Please try
 * again." — covering a timeout, a bad photo and the provider being down. Those
 * need different words, because they need different actions from the user.
 *
 * The rule these components exist to enforce: **never show a personalised
 * result after a failure.** A failure renders one of these instead.
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
import { COLORS, FONTS, RADIUS, SPACING } from '../theme/colors';

// --- Shared shell -----------------------------------------------------------

interface ShellProps {
  icon: keyof typeof Ionicons.glyphMap;
  tone: 'neutral' | 'working' | 'warning' | 'error';
  title: string;
  body?: string;
  children?: React.ReactNode;
  testID?: string;
}

const TONE_COLOR: Record<ShellProps['tone'], string> = {
  neutral: COLORS.textSecondary,
  working: COLORS.primary,
  warning: COLORS.warning,
  error: COLORS.error,
};

const TONE_BACKGROUND: Record<ShellProps['tone'], string> = {
  neutral: COLORS.backgroundSecondary,
  working: COLORS.primaryLight,
  warning: COLORS.warningLight,
  error: COLORS.errorLight,
};

function StateShell({ icon, tone, title, body, children, testID }: ShellProps) {
  return (
    <View style={styles.container} testID={testID} accessibilityRole="summary">
      <View style={[styles.iconWrap, { backgroundColor: TONE_BACKGROUND[tone] }]}>
        <Ionicons name={icon} size={26} color={TONE_COLOR[tone]} />
      </View>
      <Text style={styles.title} accessibilityRole="header">
        {title}
      </Text>
      {body ? <Text style={styles.body}>{body}</Text> : null}
      {children}
    </View>
  );
}

// --- Buttons ----------------------------------------------------------------

interface ActionProps {
  label: string;
  onPress: () => void;
  variant?: 'primary' | 'secondary' | 'danger';
  testID?: string;
  disabled?: boolean;
}

export function StateAction({
  label,
  onPress,
  variant = 'primary',
  testID,
  disabled,
}: ActionProps) {
  return (
    <TouchableOpacity
      onPress={onPress}
      disabled={disabled}
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled: !!disabled }}
      style={[
        styles.action,
        variant === 'secondary' && styles.actionSecondary,
        variant === 'danger' && styles.actionDanger,
        disabled && styles.actionDisabled,
      ]}
    >
      <Text
        style={[
          styles.actionText,
          variant === 'secondary' && styles.actionTextSecondary,
        ]}
      >
        {label}
      </Text>
    </TouchableOpacity>
  );
}

// --- Progress states --------------------------------------------------------

export function UploadingState({ progress }: { progress?: number }) {
  const pct =
    typeof progress === 'number' ? ` ${Math.round(progress * 100)}%` : '';
  return (
    <StateShell
      testID="state-uploading"
      icon="cloud-upload-outline"
      tone="working"
      title={`Uploading your photo${pct}`}
      body="Keep the app open — this only takes a moment."
    >
      <ActivityIndicator color={COLORS.primary} style={styles.spinner} />
    </StateShell>
  );
}

export function ProcessingState({ step }: { step?: string }) {
  return (
    <StateShell
      testID="state-processing"
      icon="sparkles-outline"
      tone="working"
      title="Reading your photo"
      body={step ?? 'This usually takes about fifteen seconds.'}
    >
      <ActivityIndicator color={COLORS.primary} style={styles.spinner} />
    </StateShell>
  );
}

export function RetryingState({
  attempt,
  maxAttempts,
}: {
  attempt: number;
  maxAttempts: number;
}) {
  return (
    <StateShell
      testID="state-retrying"
      icon="refresh-outline"
      tone="working"
      title={`Trying again (${attempt} of ${maxAttempts})`}
      body="The first attempt did not come back cleanly. Your check has not been used."
    >
      <ActivityIndicator color={COLORS.primary} style={styles.spinner} />
    </StateShell>
  );
}

// --- Failure states ---------------------------------------------------------

/**
 * A photo we could not read. The guidance comes from the server so the advice
 * and the reason can never drift apart.
 */
export function LowQualityImageState({
  guidance,
  onRetake,
  onChooseAnother,
}: {
  guidance: string[];
  onRetake: () => void;
  onChooseAnother?: () => void;
}) {
  return (
    <StateShell
      testID="state-low-quality"
      icon="camera-reverse-outline"
      tone="warning"
      title="We could not read that photo clearly"
      body="Your check has not been used. A clearer photo should do it."
    >
      {guidance.length > 0 && (
        <View style={styles.guidance}>
          {guidance.map((tip) => (
            <View key={tip} style={styles.guidanceRow}>
              <Ionicons
                name="ellipse"
                size={5}
                color={COLORS.textMuted}
                style={styles.bullet}
              />
              <Text style={styles.guidanceText}>{tip}</Text>
            </View>
          ))}
        </View>
      )}
      <StateAction label="Take another photo" onPress={onRetake} testID="retake" />
      {onChooseAnother && (
        <StateAction
          label="Choose from gallery"
          onPress={onChooseAnother}
          variant="secondary"
          testID="choose-another"
        />
      )}
    </StateShell>
  );
}

/** Our problem, not the user's photo. Say so plainly. */
export function ProviderUnavailableState({
  onRetry,
  onDismiss,
  retryable = true,
}: {
  onRetry: () => void;
  onDismiss?: () => void;
  retryable?: boolean;
}) {
  return (
    <StateShell
      testID="state-provider-unavailable"
      icon="cloud-offline-outline"
      tone="error"
      title="Photo checks are unavailable right now"
      body={
        'This is a problem on our side, not with your photo. ' +
        'Your check has not been used.'
      }
    >
      {retryable && (
        <StateAction label="Try again" onPress={onRetry} testID="retry" />
      )}
      {onDismiss && (
        <StateAction
          label="Back"
          onPress={onDismiss}
          variant="secondary"
          testID="dismiss"
        />
      )}
    </StateShell>
  );
}

/**
 * The general failure state.
 *
 * `allowancePreserved` is only claimed when the server explicitly said so —
 * telling someone their check was refunded when it was not would be worse than
 * saying nothing.
 */
export function AnalysisFailedState({
  message,
  guidance = [],
  allowancePreserved,
  retryable = true,
  onRetry,
  onDismiss,
}: {
  message: string;
  guidance?: string[];
  allowancePreserved: boolean;
  retryable?: boolean;
  onRetry: () => void;
  onDismiss?: () => void;
}) {
  return (
    <StateShell
      testID="state-analysis-failed"
      icon="alert-circle-outline"
      tone="error"
      title="We could not finish that check"
      body={message}
    >
      {allowancePreserved && (
        <View style={styles.reassurance} testID="allowance-preserved">
          <Ionicons name="shield-checkmark-outline" size={15} color={COLORS.primary} />
          <Text style={styles.reassuranceText}>
            This did not use one of your checks.
          </Text>
        </View>
      )}
      {guidance.length > 0 && (
        <View style={styles.guidance}>
          {guidance.map((tip) => (
            <View key={tip} style={styles.guidanceRow}>
              <Ionicons
                name="ellipse"
                size={5}
                color={COLORS.textMuted}
                style={styles.bullet}
              />
              <Text style={styles.guidanceText}>{tip}</Text>
            </View>
          ))}
        </View>
      )}
      {retryable && (
        <StateAction label="Try again" onPress={onRetry} testID="retry" />
      )}
      {onDismiss && (
        <StateAction
          label="Not now"
          onPress={onDismiss}
          variant="secondary"
          testID="dismiss"
        />
      )}
    </StateShell>
  );
}

// --- Product state ----------------------------------------------------------

/** Shown where a paid or unfinished feature would otherwise be offered. */
export function BetaFeatureUnavailableState({
  message,
  onDismiss,
}: {
  message: string;
  onDismiss?: () => void;
}) {
  return (
    <StateShell
      testID="state-beta-unavailable"
      icon="lock-closed-outline"
      tone="neutral"
      title="Not available in the private beta"
      body={message}
    >
      {onDismiss && (
        <StateAction
          label="Got it"
          onPress={onDismiss}
          variant="secondary"
          testID="dismiss"
        />
      )}
    </StateShell>
  );
}

/** Deleting is irreversible, so it is stated in those words before it happens. */
export function DeletionConfirmState({
  title = 'Delete this photo?',
  body = 'This cannot be undone. The photo is removed from our storage straight away.',
  confirmLabel = 'Delete',
  busy = false,
  onConfirm,
  onCancel,
}: {
  title?: string;
  body?: string;
  confirmLabel?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <StateShell
      testID="state-deletion-confirm"
      icon="trash-outline"
      tone="warning"
      title={title}
      body={body}
    >
      {busy ? (
        <ActivityIndicator color={COLORS.error} style={styles.spinner} />
      ) : (
        <>
          <StateAction
            label={confirmLabel}
            onPress={onConfirm}
            variant="danger"
            testID="confirm-delete"
          />
          <StateAction
            label="Keep it"
            onPress={onCancel}
            variant="secondary"
            testID="cancel-delete"
          />
        </>
      )}
    </StateShell>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    paddingHorizontal: SPACING.lg,
    paddingVertical: SPACING.xl,
    gap: SPACING.sm,
  },
  iconWrap: {
    width: 56,
    height: 56,
    borderRadius: RADIUS.full,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: SPACING.xs,
  },
  title: {
    fontFamily: FONTS.family.headingMedium,
    fontSize: FONTS.sizes.h3,
    color: COLORS.textPrimary,
    textAlign: 'center',
  },
  body: {
    fontFamily: FONTS.family.body,
    fontSize: FONTS.sizes.body,
    color: COLORS.textSecondary,
    textAlign: 'center',
    lineHeight: FONTS.sizes.body * FONTS.lineHeights.normal,
    maxWidth: 340,
  },
  spinner: { marginTop: SPACING.sm },
  guidance: {
    alignSelf: 'stretch',
    backgroundColor: COLORS.backgroundSecondary,
    borderRadius: RADIUS.lg,
    padding: SPACING.md,
    marginTop: SPACING.sm,
    gap: SPACING.sm,
  },
  guidanceRow: { flexDirection: 'row', alignItems: 'flex-start', gap: SPACING.sm },
  bullet: { marginTop: 7 },
  guidanceText: {
    flex: 1,
    fontFamily: FONTS.family.body,
    fontSize: FONTS.sizes.bodySm,
    color: COLORS.textSecondary,
    lineHeight: FONTS.sizes.bodySm * FONTS.lineHeights.normal,
  },
  reassurance: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: SPACING.sm,
    backgroundColor: COLORS.primaryLight,
    borderRadius: RADIUS.md,
    paddingVertical: SPACING.sm,
    paddingHorizontal: SPACING.md,
    marginTop: SPACING.xs,
  },
  reassuranceText: {
    fontFamily: FONTS.family.bodyMedium,
    fontSize: FONTS.sizes.bodySm,
    color: COLORS.primaryDark,
  },
  action: {
    alignSelf: 'stretch',
    backgroundColor: COLORS.primary,
    borderRadius: RADIUS.lg,
    paddingVertical: 14,
    alignItems: 'center',
    marginTop: SPACING.sm,
  },
  actionSecondary: {
    backgroundColor: 'transparent',
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  actionDanger: { backgroundColor: COLORS.error },
  actionDisabled: { opacity: 0.5 },
  actionText: {
    fontFamily: FONTS.family.bodySemibold,
    fontSize: FONTS.sizes.body,
    color: COLORS.white,
  },
  actionTextSecondary: { color: COLORS.textPrimary },
});
