import React from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

export default function ScanTabScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>AI Beauty Scan</Text>
        <Text style={styles.headerSubtitle}>Get personalized analysis</Text>
      </View>

      <View style={styles.content}>
        <View style={styles.iconContainer}>
          <Ionicons name="scan" size={64} color="#0EA5E9" />
        </View>
        <Text style={styles.title}>Analyze Your Beauty</Text>
        <Text style={styles.description}>
          Our AI-powered scanner analyzes your skin and hair to provide personalized recommendations
        </Text>

        <View style={styles.features}>
          <View style={styles.featureItem}>
            <View style={styles.featureIcon}><Ionicons name="sparkles" size={20} color="#0EA5E9" /></View>
            <Text style={styles.featureText}>Skin health analysis</Text>
          </View>
          <View style={styles.featureItem}>
            <View style={styles.featureIcon}><Ionicons name="leaf" size={20} color="#10B981" /></View>
            <Text style={styles.featureText}>Hair condition check</Text>
          </View>
          <View style={styles.featureItem}>
            <View style={styles.featureIcon}><Ionicons name="heart" size={20} color="#EC4899" /></View>
            <Text style={styles.featureText}>Personalized tips</Text>
          </View>
        </View>

        <TouchableOpacity style={styles.scanButton} onPress={() => router.push('/scan')}>
          <Ionicons name="camera" size={22} color="#FFFFFF" />
          <Text style={styles.scanButtonText}>Start Scan</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F8FAFC' },
  header: { paddingHorizontal: 20, paddingVertical: 16 },
  headerTitle: { fontSize: 24, fontWeight: '700', color: '#1E293B' },
  headerSubtitle: { fontSize: 14, color: '#64748B', marginTop: 4 },
  content: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 40 },
  iconContainer: { width: 120, height: 120, borderRadius: 60, backgroundColor: '#E0F2FE', justifyContent: 'center', alignItems: 'center', marginBottom: 24 },
  title: { fontSize: 22, fontWeight: '700', color: '#1E293B', marginBottom: 12 },
  description: { fontSize: 15, color: '#64748B', textAlign: 'center', lineHeight: 22, marginBottom: 32 },
  features: { width: '100%', gap: 12, marginBottom: 32 },
  featureItem: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#FFFFFF', borderRadius: 12, padding: 14, gap: 12, borderWidth: 1, borderColor: '#E2E8F0' },
  featureIcon: { width: 36, height: 36, borderRadius: 18, backgroundColor: '#F1F5F9', justifyContent: 'center', alignItems: 'center' },
  featureText: { fontSize: 14, color: '#1E293B', fontWeight: '500' },
  scanButton: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#0EA5E9', paddingVertical: 16, paddingHorizontal: 32, borderRadius: 28, gap: 10 },
  scanButtonText: { fontSize: 16, fontWeight: '600', color: '#FFFFFF' },
});
