import React, { Component, type ErrorInfo, type ReactNode } from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { COLORS, FONTS, SPACING, RADIUS } from '../theme/colors';

type Props = { children: ReactNode; onReset?: () => void };
type State = { hasError: boolean; message: string };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: '' };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, message: error?.message || 'Something went wrong' };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('UI ErrorBoundary:', error, info?.componentStack);
  }

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <View style={styles.wrap}>
        <Text style={styles.title}>Something went wrong</Text>
        <Text style={styles.msg}>{this.state.message}</Text>
        <TouchableOpacity
          style={styles.btn}
          onPress={() => {
            this.setState({ hasError: false, message: '' });
            this.props.onReset?.();
          }}
        >
          <Text style={styles.btnText}>Try again</Text>
        </TouchableOpacity>
      </View>
    );
  }
}

const styles = StyleSheet.create({
  wrap: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: SPACING.xl,
    backgroundColor: COLORS.background,
  },
  title: { fontFamily: FONTS.family.heading, fontSize: 24, color: COLORS.textPrimary },
  msg: {
    fontFamily: FONTS.family.body,
    fontSize: 14,
    color: COLORS.textSecondary,
    textAlign: 'center',
    marginTop: 10,
    marginBottom: 20,
  },
  btn: {
    backgroundColor: COLORS.primary,
    paddingHorizontal: 18,
    paddingVertical: 12,
    borderRadius: RADIUS.md,
  },
  btnText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.white },
});
