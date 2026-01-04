import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeIn, FadeInDown, FadeInUp } from 'react-native-reanimated';
import { api } from '../src/services/api';
import { useUserStore } from '../src/store/userStore';

const MOODS = [
  { id: 'fresh', label: 'Feel Fresh & Light', icon: 'sunny', emoji: '✨' },
  { id: 'glam', label: 'Glamorous Look', icon: 'sparkles', emoji: '💅' },
  { id: 'relax', label: 'Pamper & Relax', icon: 'leaf', emoji: '🧖' },
  { id: 'quick', label: 'Quick Makeover', icon: 'flash', emoji: '⚡' },
  { id: 'special', label: 'Special Occasion', icon: 'heart', emoji: '🎉' },
];

const OCCASIONS = [
  { id: 'daily', label: 'Daily/Casual', icon: 'cafe' },
  { id: 'work', label: 'Office/Work', icon: 'briefcase' },
  { id: 'party', label: 'Party/Night Out', icon: 'musical-notes' },
  { id: 'wedding', label: 'Shaadi/Wedding', icon: 'heart' },
  { id: 'festival', label: 'Festival/Pooja', icon: 'flower' },
  { id: 'date', label: 'Date Night', icon: 'moon' },
  { id: 'selfcare', label: 'Self-Care Sunday', icon: 'sparkles' },
];

const BUDGETS = [
  { id: 'budget', label: '₹500 - ₹1,500', range: 'Budget Friendly', value: '500-1500' },
  { id: 'standard', label: '₹1,500 - ₹3,000', range: 'Standard', value: '1500-3000' },
  { id: 'premium', label: '₹3,000 - ₹5,000', range: 'Premium', value: '3000-5000' },
  { id: 'luxury', label: '₹5,000+', range: 'Luxury', value: '5000+' },
];

export default function GetAdviceScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { userId } = useUserStore();

  const [step, setStep] = useState(1);
  const [selectedMood, setSelectedMood] = useState('');
  const [selectedOccasion, setSelectedOccasion] = useState('');
  const [selectedBudget, setSelectedBudget] = useState('');
  const [loading, setLoading] = useState(false);

  const handleMoodSelect = (moodId: string) => {
    setSelectedMood(moodId);
    setStep(2);
  };

  const handleOccasionSelect = (occasionId: string) => {
    setSelectedOccasion(occasionId);
    setStep(3);
  };

  const handleBudgetSelect = async (budget: typeof BUDGETS[0]) => {
    setSelectedBudget(budget.value);
    setLoading(true);

    try {
      const response = await api.post('/recommendations/generate', {
        user_id: userId,
        mood: selectedMood,
        occasion: selectedOccasion,
        budget: budget.value,
      });

      router.push({
        pathname: '/recommendations',
        params: { recommendations: JSON.stringify(response.data) },
      });
    } catch (error) {
      console.error('Error generating recommendations:', error);
    } finally {
      setLoading(false);
    }
  };

  const progress = (step / 3) * 100;

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity onPress={() => (step > 1 ? setStep(step - 1) : router.back())} style={styles.backButton}>
          <Ionicons name="arrow-back" size={24} color="#1E293B" />
        </TouchableOpacity>
        <View style={styles.progressContainer}>
          <View style={styles.progressBar}>
            <View style={[styles.progressFill, { width: `${progress}%` }]} />
          </View>
          <Text style={styles.progressText}>Step {step} of 3</Text>
        </View>
        <View style={{ width: 40 }} />
      </View>

      {loading ? (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color="#0EA5E9" />
          <Text style={styles.loadingText}>Creating your personalized recommendations...</Text>
        </View>
      ) : (
        <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.content}>
          {/* Step 1: Mood */}
          {step === 1 && (
            <Animated.View entering={FadeIn}>
              <Text style={styles.stepTitle}>What's your mood today?</Text>
              <Text style={styles.stepSubtitle}>Aaj ka mood kya hai?</Text>
              <View style={styles.optionsGrid}>
                {MOODS.map((mood, index) => (
                  <Animated.View key={mood.id} entering={FadeInDown.delay(index * 50)}>
                    <TouchableOpacity
                      style={[styles.moodCard, selectedMood === mood.id && styles.moodCardActive]}
                      onPress={() => handleMoodSelect(mood.id)}
                    >
                      <Text style={styles.moodEmoji}>{mood.emoji}</Text>
                      <Text style={styles.moodLabel}>{mood.label}</Text>
                    </TouchableOpacity>
                  </Animated.View>
                ))}
              </View>
            </Animated.View>
          )}

          {/* Step 2: Occasion */}
          {step === 2 && (
            <Animated.View entering={FadeIn}>
              <Text style={styles.stepTitle}>What's the occasion?</Text>
              <Text style={styles.stepSubtitle}>Kya hai occasion?</Text>
              <View style={styles.occasionList}>
                {OCCASIONS.map((occasion, index) => (
                  <Animated.View key={occasion.id} entering={FadeInDown.delay(index * 50)}>
                    <TouchableOpacity
                      style={[styles.occasionCard, selectedOccasion === occasion.id && styles.occasionCardActive]}
                      onPress={() => handleOccasionSelect(occasion.id)}
                    >
                      <View style={[styles.occasionIcon, selectedOccasion === occasion.id && styles.occasionIconActive]}>
                        <Ionicons name={occasion.icon as any} size={22} color={selectedOccasion === occasion.id ? '#FFFFFF' : '#0EA5E9'} />
                      </View>
                      <Text style={[styles.occasionLabel, selectedOccasion === occasion.id && styles.occasionLabelActive]}>{occasion.label}</Text>
                      <Ionicons name="chevron-forward" size={20} color="#94A3B8" />
                    </TouchableOpacity>
                  </Animated.View>
                ))}
              </View>
            </Animated.View>
          )}

          {/* Step 3: Budget */}
          {step === 3 && (
            <Animated.View entering={FadeIn}>
              <Text style={styles.stepTitle}>What's your budget?</Text>
              <Text style={styles.stepSubtitle}>Kitna budget hai?</Text>
              <View style={styles.budgetList}>
                {BUDGETS.map((budget, index) => (
                  <Animated.View key={budget.id} entering={FadeInUp.delay(index * 50)}>
                    <TouchableOpacity
                      style={[styles.budgetCard, selectedBudget === budget.value && styles.budgetCardActive]}
                      onPress={() => handleBudgetSelect(budget)}
                    >
                      <View>
                        <Text style={styles.budgetLabel}>{budget.label}</Text>
                        <Text style={styles.budgetRange}>{budget.range}</Text>
                      </View>
                      <View style={[styles.budgetCheck, selectedBudget === budget.value && styles.budgetCheckActive]}>
                        {selectedBudget === budget.value && <Ionicons name="checkmark" size={18} color="#FFFFFF" />}
                      </View>
                    </TouchableOpacity>
                  </Animated.View>
                ))}
              </View>
            </Animated.View>
          )}
        </ScrollView>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F8FAFC' },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 16, paddingVertical: 12 },
  backButton: { width: 40, height: 40, borderRadius: 20, backgroundColor: '#FFFFFF', justifyContent: 'center', alignItems: 'center', borderWidth: 1, borderColor: '#E2E8F0' },
  progressContainer: { flex: 1, marginHorizontal: 16 },
  progressBar: { height: 6, backgroundColor: '#E2E8F0', borderRadius: 3 },
  progressFill: { height: '100%', backgroundColor: '#0EA5E9', borderRadius: 3 },
  progressText: { fontSize: 12, color: '#64748B', textAlign: 'center', marginTop: 6 },
  content: { paddingHorizontal: 20, paddingBottom: 40 },
  loadingContainer: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: 16 },
  loadingText: { fontSize: 16, color: '#64748B', textAlign: 'center' },
  stepTitle: { fontSize: 26, fontWeight: '700', color: '#1E293B', marginTop: 20 },
  stepSubtitle: { fontSize: 16, color: '#64748B', marginTop: 4, marginBottom: 24 },
  optionsGrid: { gap: 12 },
  moodCard: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#FFFFFF', borderRadius: 16, padding: 18, borderWidth: 1, borderColor: '#E2E8F0', gap: 14 },
  moodCardActive: { borderColor: '#0EA5E9', backgroundColor: '#E0F2FE' },
  moodEmoji: { fontSize: 28 },
  moodLabel: { fontSize: 16, fontWeight: '500', color: '#1E293B' },
  occasionList: { gap: 10 },
  occasionCard: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#FFFFFF', borderRadius: 14, padding: 16, borderWidth: 1, borderColor: '#E2E8F0' },
  occasionCardActive: { borderColor: '#0EA5E9', backgroundColor: '#E0F2FE' },
  occasionIcon: { width: 44, height: 44, borderRadius: 22, backgroundColor: '#E0F2FE', justifyContent: 'center', alignItems: 'center', marginRight: 14 },
  occasionIconActive: { backgroundColor: '#0EA5E9' },
  occasionLabel: { flex: 1, fontSize: 16, fontWeight: '500', color: '#1E293B' },
  occasionLabelActive: { color: '#0284C7' },
  budgetList: { gap: 12 },
  budgetCard: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: '#FFFFFF', borderRadius: 16, padding: 20, borderWidth: 2, borderColor: '#E2E8F0' },
  budgetCardActive: { borderColor: '#0EA5E9', backgroundColor: '#E0F2FE' },
  budgetLabel: { fontSize: 18, fontWeight: '600', color: '#1E293B' },
  budgetRange: { fontSize: 13, color: '#64748B', marginTop: 2 },
  budgetCheck: { width: 28, height: 28, borderRadius: 14, backgroundColor: '#E2E8F0', justifyContent: 'center', alignItems: 'center' },
  budgetCheckActive: { backgroundColor: '#0EA5E9' },
});
