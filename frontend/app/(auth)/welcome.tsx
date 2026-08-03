import React, { useState } from 'react';
import {
  Text,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Alert,
  ScrollView,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useUserStore } from '../../src/store/userStore';
import { supabase } from '../../src/services/supabase';
import { COLORS, FONTS, SPACING, RADIUS } from '../../src/theme/colors';

function notify(title: string, message: string) {
  if (Platform.OS === 'web') window.alert(`${title}\n\n${message}`);
  else Alert.alert(title, message);
}

export default function AuthWelcome() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { login, createUser, loading } = useUserStore();
  const [mode, setMode] = useState<'login' | 'register' | 'reset'>('login');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [inviteCode, setInviteCode] = useState('');
  const [resetSent, setResetSent] = useState(false);

  const goHome = () => {
    try {
      router.replace('/(tabs)/today');
    } catch {
      router.replace('/');
    }
  };

  const handlePasswordReset = async () => {
    if (!email.trim()) {
      notify('Missing email', 'Enter the email on your account.');
      return;
    }
    try {
      const { error } = await supabase.auth.resetPasswordForEmail(email.trim());
      if (error) {
        notify('Could not send reset', error.message);
        return;
      }
      setResetSent(true);
    } catch (err) {
       
      console.error('reset error', err);
      notify('Reset error', 'Something went wrong. Please try again.');
    }
  };

  const handleSubmit = async () => {
    if (mode === 'reset') {
      await handlePasswordReset();
      return;
    }
    if (!email.trim() || !password.trim()) {
      notify('Missing details', 'Email and password are required.');
      return;
    }
    try {
      if (mode === 'login') {
        const result = await login(email.trim(), password);
        if (result.ok) goHome();
        else notify('Sign in failed', result.message ?? 'Check your email and password.');
      } else {
        if (!name.trim()) {
          notify('Missing name', 'Please enter your name.');
          return;
        }
        if (!inviteCode.trim()) {
          notify(
            'Invite required',
            'GlamGenius is invite-only. Enter the code you were given.'
          );
          return;
        }
        const result = await createUser(name.trim(), email.trim(), password, inviteCode.trim());
        if (result.ok) router.replace('/onboarding');
        else {
          notify(
            'Could not register',
            result.message ?? 'Check your invite code and email, then try again.'
          );
        }
      }
    } catch (err) {
       
      console.error('auth failed', err);
      notify('Auth error', 'Something went wrong. Please try again.');
    }
  };

  const title =
    mode === 'login' ? 'Welcome back' : mode === 'register' ? 'Create account' : 'Reset password';

  const subtitle =
    mode === 'login'
      ? 'Save your checks, colours, and progress across devices.'
      : mode === 'register'
        ? 'GlamGenius is currently invite-only for a private test group. Enter the invite code you were given to create your account.'
        : 'Enter your email and we will send you a reset link.';

  return (
    <KeyboardAvoidingView
      style={[
        styles.container,
        { paddingTop: insets.top + 12, paddingBottom: insets.bottom + 16 },
      ]}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ flexGrow: 1 }}>
        <TouchableOpacity
          testID="auth-back"
          onPress={() => {
            try {
              if (router.canGoBack()) router.back();
              else goHome();
            } catch {
              goHome();
            }
          }}
          style={styles.back}
        >
          <Ionicons name="arrow-back" size={22} color={COLORS.textPrimary} />
        </TouchableOpacity>

        <Text style={styles.title} testID="auth-title">
          {title}
        </Text>
        <Text style={styles.subtitle}>{subtitle}</Text>

        {mode === 'register' && (
          <>
            <TextInput
              testID="auth-name-input"
              style={styles.input}
              placeholder="Your name"
              placeholderTextColor={COLORS.textMuted}
              value={name}
              onChangeText={setName}
            />
            <TextInput
              testID="auth-invite-input"
              style={styles.input}
              placeholder="Invite code"
              placeholderTextColor={COLORS.textMuted}
              autoCapitalize="characters"
              autoCorrect={false}
              value={inviteCode}
              onChangeText={setInviteCode}
            />
          </>
        )}
        <TextInput
          testID="auth-email-input"
          style={styles.input}
          placeholder="Email"
          placeholderTextColor={COLORS.textMuted}
          autoCapitalize="none"
          keyboardType="email-address"
          value={email}
          onChangeText={setEmail}
        />
        {mode !== 'reset' && (
          <TextInput
            testID="auth-password-input"
            style={styles.input}
            placeholder="Password"
            placeholderTextColor={COLORS.textMuted}
            secureTextEntry
            value={password}
            onChangeText={setPassword}
          />
        )}

        {mode === 'reset' && resetSent && (
          <Text style={styles.info} testID="auth-reset-sent">
            If that email is on file, a reset link is on its way.
          </Text>
        )}

        <TouchableOpacity
          testID="auth-submit"
          style={styles.button}
          onPress={handleSubmit}
          disabled={loading}
        >
          {loading ? (
            <ActivityIndicator color={COLORS.white} />
          ) : (
            <Text style={styles.buttonText}>
              {mode === 'login'
                ? 'Sign in'
                : mode === 'register'
                  ? 'Create account'
                  : 'Send reset email'}
            </Text>
          )}
        </TouchableOpacity>

        <TouchableOpacity
          testID="auth-switch"
          onPress={() => {
            setResetSent(false);
            setMode(mode === 'login' ? 'register' : 'login');
          }}
          style={{ marginTop: 18 }}
        >
          <Text style={styles.switchText}>
            {mode === 'login'
              ? 'New here? Create an account'
              : mode === 'register'
                ? 'Have an account? Sign in'
                : 'Back to sign in'}
          </Text>
        </TouchableOpacity>

        {mode === 'login' && (
          <TouchableOpacity
            testID="auth-forgot"
            onPress={() => {
              setResetSent(false);
              setMode('reset');
            }}
            style={{ marginTop: 10 }}
          >
            <Text style={styles.switchText}>Forgot password?</Text>
          </TouchableOpacity>
        )}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background, paddingHorizontal: SPACING.lg },
  back: { marginBottom: SPACING.lg, alignSelf: 'flex-start', padding: 4 },
  title: { fontFamily: FONTS.family.heading, fontSize: 32, color: COLORS.textPrimary },
  subtitle: {
    fontFamily: FONTS.family.body,
    fontSize: 15,
    color: COLORS.textSecondary,
    marginTop: 8,
    marginBottom: SPACING.xl,
  },
  input: {
    backgroundColor: COLORS.card,
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: RADIUS.md,
    paddingHorizontal: 16,
    paddingVertical: 14,
    marginBottom: 12,
    fontFamily: FONTS.family.body,
    fontSize: 15,
    color: COLORS.textPrimary,
  },
  button: {
    backgroundColor: COLORS.primary,
    borderRadius: RADIUS.lg,
    paddingVertical: 16,
    alignItems: 'center',
    marginTop: 8,
  },
  buttonText: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.white },
  switchText: {
    fontFamily: FONTS.family.bodyMedium,
    fontSize: 14,
    color: COLORS.primary,
    textAlign: 'center',
  },
  info: {
    fontFamily: FONTS.family.body,
    fontSize: 13,
    color: COLORS.textSecondary,
    marginBottom: 8,
    textAlign: 'center',
  },
});
