import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeIn } from 'react-native-reanimated';

export default function ScanTabScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const handleStartScan = () => {
    router.push('/scan');
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <Animated.View entering={FadeIn} style={styles.content}>
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.headerTitle}>AI Beauty Scan</Text>
          <Text style={styles.headerSubtitle}>
            Analyze your face, hair & skin for personalized recommendations
          </Text>
        </View>

        {/* Scan Options */}
        <View style={styles.scanOptions}>
          <TouchableOpacity style={styles.scanCard} onPress={handleStartScan}>
            <View style={styles.scanIconContainer}>
              <Ionicons name="happy-outline" size={40} color="#D4AF37" />
            </View>
            <Text style={styles.scanCardTitle}>Face Analysis</Text>
            <Text style={styles.scanCardDesc}>
              Detect face shape, skin type & concerns
            </Text>
          </TouchableOpacity>

          <TouchableOpacity style={styles.scanCard} onPress={handleStartScan}>
            <View style={styles.scanIconContainer}>
              <Ionicons name="leaf-outline" size={40} color="#D4AF37" />
            </View>
            <Text style={styles.scanCardTitle}>Hair Analysis</Text>
            <Text style={styles.scanCardDesc}>
              Analyze hair type, texture & condition
            </Text>
          </TouchableOpacity>

          <TouchableOpacity style={styles.scanCard} onPress={handleStartScan}>
            <View style={styles.scanIconContainer}>
              <Ionicons name="analytics-outline" size={40} color="#D4AF37" />
            </View>
            <Text style={styles.scanCardTitle}>Full Analysis</Text>
            <Text style={styles.scanCardDesc}>
              Complete beauty assessment
            </Text>
          </TouchableOpacity>
        </View>

        {/* Start Button */}
        <TouchableOpacity style={styles.startButton} onPress={handleStartScan}>
          <Ionicons name="scan" size={24} color="#0A0A0A" />
          <Text style={styles.startButtonText}>Start AI Scan</Text>
        </TouchableOpacity>

        {/* Info */}
        <View style={styles.infoContainer}>
          <Ionicons name="shield-checkmark" size={16} color="#D4AF37" />
          <Text style={styles.infoText}>
            Your photos are analyzed securely and never stored without permission
          </Text>
        </View>
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0A0A0A',
  },
  content: {
    flex: 1,
    paddingHorizontal: 20,
  },
  header: {
    paddingVertical: 24,
  },
  headerTitle: {
    fontSize: 28,
    fontWeight: '700',
    color: '#FFFFFF',
    textAlign: 'center',
  },
  headerSubtitle: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.5)',
    textAlign: 'center',
    marginTop: 8,
    lineHeight: 20,
  },
  scanOptions: {
    marginTop: 20,
    gap: 16,
  },
  scanCard: {
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 20,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.08)',
  },
  scanIconContainer: {
    width: 80,
    height: 80,
    borderRadius: 40,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 12,
    borderWidth: 1,
    borderColor: 'rgba(212, 175, 55, 0.3)',
  },
  scanCardTitle: {
    fontSize: 18,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  scanCardDesc: {
    fontSize: 13,
    color: 'rgba(255, 255, 255, 0.5)',
    marginTop: 4,
    textAlign: 'center',
  },
  startButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#D4AF37',
    paddingVertical: 16,
    borderRadius: 30,
    marginTop: 32,
    gap: 10,
  },
  startButtonText: {
    fontSize: 18,
    fontWeight: '600',
    color: '#0A0A0A',
  },
  infoContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 20,
    gap: 8,
    paddingHorizontal: 20,
  },
  infoText: {
    fontSize: 12,
    color: 'rgba(255, 255, 255, 0.4)',
    textAlign: 'center',
    flex: 1,
  },
});
