import React from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  Image,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeIn, FadeInUp } from 'react-native-reanimated';

export default function WelcomeScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  return (
    <View style={[styles.container, { paddingTop: insets.top, paddingBottom: insets.bottom }]}>
      <View style={styles.content}>
        <Animated.View entering={FadeIn.delay(200)} style={styles.iconContainer}>
          <Ionicons name="sparkles" size={56} color="#0EA5E9" />
        </Animated.View>

        <Animated.Text entering={FadeIn.delay(400)} style={styles.title}>
          GlamGenius
        </Animated.Text>
        <Animated.Text entering={FadeIn.delay(500)} style={styles.subtitle}>
          Your Personal Beauty Advisor
        </Animated.Text>

        <Animated.View entering={FadeInUp.delay(600)} style={styles.features}>
          <View style={styles.featureItem}>
            <View style={styles.featureIcon}><Ionicons name="scan" size={20} color="#0EA5E9" /></View>
            <Text style={styles.featureText}>AI-Powered Analysis</Text>
          </View>
          <View style={styles.featureItem}>
            <View style={styles.featureIcon}><Ionicons name="star" size={20} color="#F59E0B" /></View>
            <Text style={styles.featureText}>Personalized Recommendations</Text>
          </View>
          <View style={styles.featureItem}>
            <View style={styles.featureIcon}><Ionicons name="heart" size={20} color="#EC4899" /></View>
            <Text style={styles.featureText}>Expert Beauty Tips</Text>
          </View>
        </Animated.View>
      </View>

      <Animated.View entering={FadeInUp.delay(800)} style={styles.bottomContainer}>
        <TouchableOpacity style={styles.primaryButton} onPress={() => router.replace('/(tabs)/home')}>
          <Text style={styles.primaryButtonText}>Get Started</Text>
          <Ionicons name="arrow-forward" size={20} color="#FFFFFF" />
        </TouchableOpacity>
        <Text style={styles.termsText}>
          By continuing, you agree to our Terms & Privacy Policy
        </Text>
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#FFFFFF' },
  content: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 40 },
  iconContainer: { width: 100, height: 100, borderRadius: 50, backgroundColor: '#E0F2FE', justifyContent: 'center', alignItems: 'center', marginBottom: 24 },
  title: { fontSize: 36, fontWeight: '700', color: '#1E293B', marginBottom: 8 },
  subtitle: { fontSize: 16, color: '#64748B', marginBottom: 40 },
  features: { width: '100%', gap: 14 },
  featureItem: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#F8FAFC', borderRadius: 14, padding: 16, gap: 14 },
  featureIcon: { width: 40, height: 40, borderRadius: 20, backgroundColor: '#FFFFFF', justifyContent: 'center', alignItems: 'center' },
  featureText: { fontSize: 15, color: '#1E293B', fontWeight: '500' },
  bottomContainer: { paddingHorizontal: 24, paddingBottom: 16 },
  primaryButton: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', backgroundColor: '#0EA5E9', paddingVertical: 18, borderRadius: 28, gap: 10 },
  primaryButtonText: { fontSize: 17, fontWeight: '600', color: '#FFFFFF' },
  termsText: { fontSize: 12, color: '#94A3B8', textAlign: 'center', marginTop: 16 },
});
