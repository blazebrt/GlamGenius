import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Alert,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useUserStore } from '../../src/store/userStore';
import { COLORS, FONTS, SPACING, RADIUS } from '../../src/theme/colors';

export default function AuthWelcome() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { login, createUser, loading } = useUserStore();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  const handleSubmit = async () => {
    if (!email.trim() || !password.trim()) {
      Alert.alert('Missing details', 'Email and password are required.');
      return;
    }
    if (mode === 'login') {
      const user = await login(email.trim(), password);
      if (user) router.replace('/(tabs)/home');
      else Alert.alert('Sign in failed', 'Check your email and password.');
    } else {
      if (!name.trim()) {
        Alert.alert('Missing name', 'Please enter your name.');
        return;
      }
      const user = await createUser(name.trim(), email.trim(), { password } as any);
      if (user) router.replace('/(tabs)/home');
      else Alert.alert('Could not register', 'Try a different email or try again.');
    }
  };

  return (
    <KeyboardAvoidingView
      style={[styles.container, { paddingTop: insets.top + 12, paddingBottom: insets.bottom + 16 }]}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <TouchableOpacity onPress={() => router.back()} style={styles.back}>
        <Ionicons name="arrow-back" size={22} color={COLORS.textPrimary} />
      </TouchableOpacity>

      <Text style={styles.title}>{mode === 'login' ? 'Welcome back' : 'Create account'}</Text>
      <Text style={styles.subtitle}>Save your checks, colours, and progress across devices.</Text>

      {mode === 'register' && (
        <TextInput
          style={styles.input}
          placeholder="Your name"
          placeholderTextColor={COLORS.textMuted}
          value={name}
          onChangeText={setName}
        />
      )}
      <TextInput
        style={styles.input}
        placeholder="Email"
        placeholderTextColor={COLORS.textMuted}
        autoCapitalize="none"
        keyboardType="email-address"
        value={email}
        onChangeText={setEmail}
      />
      <TextInput
        style={styles.input}
        placeholder="Password"
        placeholderTextColor={COLORS.textMuted}
        secureTextEntry
        value={password}
        onChangeText={setPassword}
      />

      <TouchableOpacity style={styles.button} onPress={handleSubmit} disabled={loading}>
        {loading ? <ActivityIndicator color={COLORS.white} /> : (
          <Text style={styles.buttonText}>{mode === 'login' ? 'Sign in' : 'Create account'}</Text>
        )}
      </TouchableOpacity>

      <TouchableOpacity onPress={() => setMode(mode === 'login' ? 'register' : 'login')} style={{ marginTop: 18 }}>
        <Text style={styles.switchText}>
          {mode === 'login' ? 'New here? Create an account' : 'Have an account? Sign in'}
        </Text>
      </TouchableOpacity>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background, paddingHorizontal: SPACING.lg },
  back: { marginBottom: SPACING.lg },
  title: { fontFamily: FONTS.family.heading, fontSize: 32, color: COLORS.textPrimary },
  subtitle: { fontFamily: FONTS.family.body, fontSize: 15, color: COLORS.textSecondary, marginTop: 8, marginBottom: SPACING.xl },
  input: {
    backgroundColor: COLORS.card, borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.md,
    paddingHorizontal: 16, paddingVertical: 14, marginBottom: 12,
    fontFamily: FONTS.family.body, fontSize: 15, color: COLORS.textPrimary,
  },
  button: {
    backgroundColor: COLORS.primary, borderRadius: RADIUS.lg, paddingVertical: 16, alignItems: 'center', marginTop: 8,
  },
  buttonText: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.white },
  switchText: { fontFamily: FONTS.family.bodyMedium, fontSize: 14, color: COLORS.primary, textAlign: 'center' },
});
