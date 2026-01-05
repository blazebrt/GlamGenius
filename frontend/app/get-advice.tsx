import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeIn, FadeInDown, FadeInUp } from 'react-native-reanimated';
import { api } from '../src/services/api';
import { useUserStore } from '../src/store/userStore';
import { COLORS, FONTS, SPACING, RADIUS } from '../src/theme/colors';

const MOODS = [
  { id: 'fresh', label: 'Feel Fresh & Light', icon: 'sunny-outline', emoji: '✨' },
  { id: 'glam', label: 'Glamorous Look', icon: 'sparkles-outline', emoji: '💅' },
  { id: 'relax', label: 'Pamper & Relax', icon: 'happy-outline', emoji: '🧖' },
  { id: 'quick', label: 'Quick Makeover', icon: 'flash-outline', emoji: '⚡' },
  { id: 'special', label: 'Special Occasion', icon: 'heart-outline', emoji: '🎉' },
  { id: 'custom', label: 'Something Else...', icon: 'create-outline', emoji: '✏️' },
];

const OCCASIONS = [
  { id: 'daily', label: 'Daily/Casual', icon: 'cafe-outline' },
  { id: 'work', label: 'Office/Work', icon: 'briefcase-outline' },
  { id: 'party', label: 'Party/Night Out', icon: 'musical-notes-outline' },
  { id: 'wedding', label: 'Shaadi/Wedding', icon: 'heart-outline' },
  { id: 'festival', label: 'Festival/Pooja', icon: 'star-outline' },
  { id: 'date', label: 'Date Night', icon: 'moon-outline' },
  { id: 'selfcare', label: 'Self-Care Sunday', icon: 'sparkles-outline' },
  { id: 'custom', label: 'Other Occasion...', icon: 'create-outline' },
];

const BUDGETS = [
  { id: 'budget', label: '₹500 - ₹1,500', range: 'Budget Friendly', value: '500-1500' },
  { id: 'standard', label: '₹1,500 - ₹3,000', range: 'Standard', value: '1500-3000' },
  { id: 'premium', label: '₹3,000 - ₹5,000', range: 'Premium', value: '3000-5000' },
  { id: 'luxury', label: '₹5,000+', range: 'Luxury', value: '5000+' },
  { id: 'custom', label: 'Custom Budget', range: 'Enter your range', value: 'custom' },
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

  const handleMoodSelect = (mood: typeof MOODS[0]) => {
    setSelectedMood(mood.id);
    setTimeout(() => setStep(2), 300);
  };

  const handleOccasionSelect = (occasion: typeof OCCASIONS[0]) => {
    setSelectedOccasion(occasion.id);
    setTimeout(() => setStep(3), 300);
  };

  const handleBudgetSelect = async (budget: typeof BUDGETS[0]) => {
    setSelectedBudget(budget.value);
    setLoading(true);
    
    try {
      const response = await api.post('/recommendations/advice', {
        user_id: userId,
        mood: selectedMood,
        occasion: selectedOccasion,
        budget: budget.value,
      });
      
      router.push({
        pathname: '/recommendations',
        params: {
          recommendations: JSON.stringify(response.data.recommendations || response.data),
          budget: budget.value,
          occasion: selectedOccasion,
        },
      });
    } catch (error) {
      console.error('Error getting advice:', error);
      Alert.alert('Error', 'Could not get recommendations. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backButton} onPress={() => step > 1 ? setStep(step - 1) : router.back()}>
          <Ionicons name="arrow-back" size={22} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <View style={styles.progressContainer}>
          <View style={styles.progressBar}>
            <View style={[styles.progressFill, { width: `${(step / 3) * 100}%` }]} />
          </View>
          <Text style={styles.progressText}>Step {step} of 3</Text>
        </View>
        <View style={{ width: 40 }} />
      </View>

      {loading ? (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color={COLORS.black} />
          <Text style={styles.loadingText}>Creating your perfect package...</Text>
        </View>
      ) : (
        <ScrollView style={styles.content} showsVerticalScrollIndicator={false}>
          {/* Step 1: Mood */}
          {step === 1 && (
            <Animated.View entering={FadeIn}>
              <Text style={styles.stepTitle}>How do you want to feel?</Text>
              <Text style={styles.stepSubtitle}>Aaj kaisa feel karna hai?</Text>
              <View style={styles.optionsGrid}>
                {MOODS.map((mood, index) => (
                  <Animated.View key={mood.id} entering={FadeInDown.delay(index * 50)}>
                    <TouchableOpacity
                      style={[styles.moodCard, selectedMood === mood.id && styles.moodCardActive]}
                      onPress={() => handleMoodSelect(mood)}
                    >
                      <Text style={styles.moodEmoji}>{mood.emoji}</Text>
                      <Text style={styles.moodLabel}>{mood.label}</Text>
                      <Ionicons name="chevron-forward" size={20} color={COLORS.textMuted} />
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
              <Text style={styles.stepSubtitle}>Kaunsa mauka hai?</Text>
              <View style={styles.occasionList}>
                {OCCASIONS.map((occasion, index) => (
                  <Animated.View key={occasion.id} entering={FadeInUp.delay(index * 40)}>
                    <TouchableOpacity
                      style={[styles.occasionCard, selectedOccasion === occasion.id && styles.occasionCardActive]}
                      onPress={() => handleOccasionSelect(occasion)}
                    >
                      <View style={[styles.occasionIcon, selectedOccasion === occasion.id && styles.occasionIconActive]}>
                        <Ionicons 
                          name={occasion.icon as any} 
                          size={22} 
                          color={selectedOccasion === occasion.id ? COLORS.white : COLORS.black} 
                        />
                      </View>
                      <Text style={[styles.occasionLabel, selectedOccasion === occasion.id && styles.occasionLabelActive]}>
                        {occasion.label}
                      </Text>
                      <Ionicons name="chevron-forward" size={20} color={COLORS.textMuted} />
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
                        {selectedBudget === budget.value && <Ionicons name="checkmark" size={18} color={COLORS.white} />}
                      </View>
                    </TouchableOpacity>
                  </Animated.View>
                ))}
              </View>
            </Animated.View>
          )}
          
          <View style={{ height: 40 }} />
        </ScrollView>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: SPACING.md, paddingVertical: SPACING.sm },
  backButton: { width: 44, height: 44, borderRadius: 22, backgroundColor: COLORS.backgroundSecondary, justifyContent: 'center', alignItems: 'center', borderWidth: 1, borderColor: COLORS.border },
  progressContainer: { flex: 1, marginHorizontal: SPACING.md },
  progressBar: { height: 6, backgroundColor: COLORS.border, borderRadius: 3 },
  progressFill: { height: '100%', backgroundColor: COLORS.black, borderRadius: 3 },
  progressText: { fontSize: FONTS.sizes.caption, fontFamily: FONTS.family.body, color: COLORS.textMuted, textAlign: 'center', marginTop: 6 },
  content: { paddingHorizontal: SPACING.lg, paddingBottom: 40 },
  loadingContainer: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: SPACING.md },
  loadingText: { fontSize: FONTS.sizes.body, fontFamily: FONTS.family.body, color: COLORS.textSecondary, textAlign: 'center' },
  stepTitle: { fontSize: FONTS.sizes.h2, fontFamily: FONTS.family.heading, color: COLORS.textPrimary, marginTop: SPACING.lg },
  stepSubtitle: { fontSize: FONTS.sizes.body, fontFamily: FONTS.family.body, color: COLORS.textSecondary, marginTop: 4, marginBottom: SPACING.lg },
  optionsGrid: { gap: SPACING.sm },
  moodCard: { flexDirection: 'row', alignItems: 'center', backgroundColor: COLORS.card, borderRadius: RADIUS.lg, padding: SPACING.md, borderWidth: 1, borderColor: COLORS.border, gap: SPACING.md },
  moodCardActive: { borderColor: COLORS.black, backgroundColor: COLORS.backgroundSecondary },
  moodEmoji: { fontSize: 28 },
  moodLabel: { flex: 1, fontSize: FONTS.sizes.bodyLg, fontFamily: FONTS.family.bodyMedium, color: COLORS.textPrimary },
  occasionList: { gap: SPACING.sm },
  occasionCard: { flexDirection: 'row', alignItems: 'center', backgroundColor: COLORS.card, borderRadius: RADIUS.md, padding: SPACING.md, borderWidth: 1, borderColor: COLORS.border },
  occasionCardActive: { borderColor: COLORS.black, backgroundColor: COLORS.backgroundSecondary },
  occasionIcon: { width: 48, height: 48, borderRadius: 24, backgroundColor: COLORS.backgroundSecondary, justifyContent: 'center', alignItems: 'center', marginRight: SPACING.md },
  occasionIconActive: { backgroundColor: COLORS.black },
  occasionLabel: { flex: 1, fontSize: FONTS.sizes.bodyLg, fontFamily: FONTS.family.bodyMedium, color: COLORS.textPrimary },
  occasionLabelActive: { color: COLORS.black, fontFamily: FONTS.family.bodySemibold },
  budgetList: { gap: SPACING.sm },
  budgetCard: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: COLORS.card, borderRadius: RADIUS.lg, padding: SPACING.lg, borderWidth: 2, borderColor: COLORS.border },
  budgetCardActive: { borderColor: COLORS.black, backgroundColor: COLORS.backgroundSecondary },
  budgetLabel: { fontSize: FONTS.sizes.h4, fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary },
  budgetRange: { fontSize: FONTS.sizes.bodySm, fontFamily: FONTS.family.body, color: COLORS.textSecondary, marginTop: 2 },
  budgetCheck: { width: 28, height: 28, borderRadius: 14, backgroundColor: COLORS.border, justifyContent: 'center', alignItems: 'center' },
  budgetCheckActive: { backgroundColor: COLORS.black },
});
