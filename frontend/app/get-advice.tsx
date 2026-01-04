import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  TextInput,
} from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeIn, FadeInDown } from 'react-native-reanimated';
import { api } from '../src/services/api';
import { useUserStore } from '../src/store/userStore';

// Get Advice - Package recommendations based on mood/occasion
const MOOD_OPTIONS = [
  { id: 'fresh', label: 'Feel Fresh & Light', icon: 'leaf-outline', color: '#2ECC71' },
  { id: 'glam', label: 'Glamorous Look', icon: 'sparkles', color: '#E91E63' },
  { id: 'relax', label: 'Pamper & Relax', icon: 'flower-outline', color: '#9B59B6' },
  { id: 'quick', label: 'Quick Makeover', icon: 'flash-outline', color: '#F39C12' },
  { id: 'bridal', label: 'Bridal/Wedding', icon: 'heart', color: '#E74C3C' },
  { id: 'custom', label: 'Custom Request', icon: 'create-outline', color: '#3498DB' },
];

const OCCASION_OPTIONS = [
  { id: 'everyday', label: 'Daily/Casual' },
  { id: 'office', label: 'Office/Work' },
  { id: 'party', label: 'Party/Night Out' },
  { id: 'wedding', label: 'Shaadi/Wedding' },
  { id: 'festival', label: 'Festival/Pooja' },
  { id: 'date', label: 'Date/Anniversary' },
  { id: 'interview', label: 'Interview/Meeting' },
  { id: 'photoshoot', label: 'Photoshoot' },
];

// INR Budget options for Indian market
const BUDGET_OPTIONS = [
  { id: 'budget', label: '₹500 - ₹1,500', subtitle: 'Value Packages', icon: 'wallet-outline' },
  { id: 'standard', label: '₹1,500 - ₹3,000', subtitle: 'Popular Choice', icon: 'card-outline' },
  { id: 'premium', label: '₹3,000 - ₹6,000', subtitle: 'Premium Experience', icon: 'diamond-outline' },
  { id: 'luxury', label: '₹6,000+', subtitle: 'Luxury & Bridal', icon: 'sparkles' },
];

export default function GetAdviceScreen() {
  const router = useRouter();
  const params = useLocalSearchParams();
  const insets = useSafeAreaInsets();
  const { userId, user } = useUserStore();

  const [step, setStep] = useState<'mood' | 'occasion' | 'budget' | 'custom' | 'loading'>('mood');
  const [selectedMood, setSelectedMood] = useState('');
  const [selectedOccasion, setSelectedOccasion] = useState(params.occasion as string || '');
  const [selectedBudget, setSelectedBudget] = useState('');
  const [customRequest, setCustomRequest] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (params.occasion) {
      setStep('budget');
    }
  }, [params.occasion]);

  const handleMoodSelect = (moodId: string) => {
    setSelectedMood(moodId);
    if (moodId === 'custom') {
      setStep('custom');
    } else {
      setStep('occasion');
    }
  };

  const handleOccasionSelect = (occasionId: string) => {
    setSelectedOccasion(occasionId);
    setStep('budget');
  };

  const handleBudgetSelect = async (budgetId: string) => {
    setSelectedBudget(budgetId);
    setStep('loading');
    setLoading(true);

    try {
      const response = await api.post('/recommendations/generate', {
        user_id: userId,
        occasion: selectedOccasion || selectedMood,
        budget: budgetId,
        mood: selectedMood,
        custom_request: customRequest,
      });

      router.push({
        pathname: '/recommendations',
        params: {
          recommendationId: response.data.recommendation_id,
          recommendations: JSON.stringify(response.data.recommendations),
          occasion: selectedOccasion,
          budget: budgetId,
        },
      });
    } catch (error) {
      console.error('Error getting recommendations:', error);
      setStep('budget');
    } finally {
      setLoading(false);
    }
  };

  const handleCustomSubmit = () => {
    if (customRequest.trim()) {
      setStep('budget');
    }
  };

  const handleBack = () => {
    if (step === 'occasion') setStep('mood');
    else if (step === 'budget') setStep(selectedMood === 'custom' ? 'custom' : 'occasion');
    else if (step === 'custom') setStep('mood');
    else router.back();
  };

  // Loading state
  if (step === 'loading' || loading) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.loadingContainer}>
          <View style={styles.loadingIcon}>
            <Ionicons name="sparkles" size={48} color="#D4AF37" />
          </View>
          <Text style={styles.loadingTitle}>Finding Best Packages</Text>
          <Text style={styles.loadingText}>
            AI is creating personalized recommendations based on your preferences...
          </Text>
          <ActivityIndicator size="large" color="#D4AF37" style={{ marginTop: 24 }} />
        </View>
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity onPress={handleBack} style={styles.backButton}>
          <Ionicons name="arrow-back" size={24} color="#FFFFFF" />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Get Package Advice</Text>
        <View style={{ width: 40 }} />
      </View>

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.scrollContent}
      >
        {/* Mood Selection */}
        {step === 'mood' && (
          <Animated.View entering={FadeIn}>
            <Text style={styles.questionTitle}>Aaj ka mood kya hai?</Text>
            <Text style={styles.questionSubtitle}>What are you looking for today?</Text>
            
            <View style={styles.moodGrid}>
              {MOOD_OPTIONS.map((mood, index) => (
                <Animated.View
                  key={mood.id}
                  entering={FadeInDown.delay(index * 50)}
                  style={styles.moodCardWrapper}
                >
                  <TouchableOpacity
                    style={[styles.moodCard, { borderColor: mood.color }]}
                    onPress={() => handleMoodSelect(mood.id)}
                  >
                    <View style={[styles.moodIcon, { backgroundColor: `${mood.color}20` }]}>
                      <Ionicons name={mood.icon as any} size={28} color={mood.color} />
                    </View>
                    <Text style={styles.moodLabel}>{mood.label}</Text>
                  </TouchableOpacity>
                </Animated.View>
              ))}
            </View>
          </Animated.View>
        )}

        {/* Custom Request */}
        {step === 'custom' && (
          <Animated.View entering={FadeIn}>
            <Text style={styles.questionTitle}>Tell us what you need</Text>
            <Text style={styles.questionSubtitle}>Describe your requirements in detail</Text>
            
            <View style={styles.customInputContainer}>
              <TextInput
                style={styles.customInput}
                placeholder="E.g., I have a wedding next week, need hair styling + makeup that lasts 8 hours..."
                placeholderTextColor="rgba(255,255,255,0.4)"
                value={customRequest}
                onChangeText={setCustomRequest}
                multiline
                numberOfLines={5}
                textAlignVertical="top"
              />
            </View>
            
            <TouchableOpacity
              style={[styles.continueButton, !customRequest.trim() && styles.buttonDisabled]}
              onPress={handleCustomSubmit}
              disabled={!customRequest.trim()}
            >
              <Text style={styles.continueButtonText}>Continue</Text>
              <Ionicons name="arrow-forward" size={20} color="#0A0A0A" />
            </TouchableOpacity>
          </Animated.View>
        )}

        {/* Occasion Selection */}
        {step === 'occasion' && (
          <Animated.View entering={FadeIn}>
            <Text style={styles.questionTitle}>Kya hai occasion?</Text>
            <Text style={styles.questionSubtitle}>What's the event you're preparing for?</Text>
            
            <View style={styles.occasionGrid}>
              {OCCASION_OPTIONS.map((occasion, index) => (
                <Animated.View
                  key={occasion.id}
                  entering={FadeInDown.delay(index * 40)}
                >
                  <TouchableOpacity
                    style={[
                      styles.occasionCard,
                      selectedOccasion === occasion.id && styles.occasionCardSelected,
                    ]}
                    onPress={() => handleOccasionSelect(occasion.id)}
                  >
                    <Text style={[
                      styles.occasionLabel,
                      selectedOccasion === occasion.id && styles.occasionLabelSelected,
                    ]}>
                      {occasion.label}
                    </Text>
                    {selectedOccasion === occasion.id && (
                      <Ionicons name="checkmark-circle" size={20} color="#D4AF37" />
                    )}
                  </TouchableOpacity>
                </Animated.View>
              ))}
            </View>
          </Animated.View>
        )}

        {/* Budget Selection */}
        {step === 'budget' && (
          <Animated.View entering={FadeIn}>
            <Text style={styles.questionTitle}>Kitna budget hai?</Text>
            <Text style={styles.questionSubtitle}>Select your preferred spending range</Text>
            
            <View style={styles.budgetOptions}>
              {BUDGET_OPTIONS.map((budget, index) => (
                <Animated.View
                  key={budget.id}
                  entering={FadeInDown.delay(index * 60)}
                >
                  <TouchableOpacity
                    style={[
                      styles.budgetCard,
                      selectedBudget === budget.id && styles.budgetCardSelected,
                    ]}
                    onPress={() => handleBudgetSelect(budget.id)}
                  >
                    <View style={[
                      styles.budgetIcon,
                      selectedBudget === budget.id && styles.budgetIconSelected,
                    ]}>
                      <Ionicons
                        name={budget.icon as any}
                        size={24}
                        color={selectedBudget === budget.id ? '#0A0A0A' : '#D4AF37'}
                      />
                    </View>
                    <View style={styles.budgetInfo}>
                      <Text style={[
                        styles.budgetLabel,
                        selectedBudget === budget.id && styles.budgetLabelSelected,
                      ]}>
                        {budget.label}
                      </Text>
                      <Text style={styles.budgetSubtitle}>{budget.subtitle}</Text>
                    </View>
                    {budget.id === 'standard' && (
                      <View style={styles.popularBadge}>
                        <Text style={styles.popularBadgeText}>Popular</Text>
                      </View>
                    )}
                  </TouchableOpacity>
                </Animated.View>
              ))}
            </View>
            
            {/* Value for money tips */}
            <View style={styles.tipContainer}>
              <Ionicons name="bulb" size={18} color="#D4AF37" />
              <Text style={styles.tipText}>
                Pro tip: Book combo packages for better value! Our AI recommends best deals for you.
              </Text>
            </View>
          </Animated.View>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0A0A0A',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  backButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(255, 255, 255, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  headerTitle: {
    fontSize: 18,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  scrollContent: {
    paddingHorizontal: 20,
    paddingTop: 24,
    paddingBottom: 40,
  },
  questionTitle: {
    fontSize: 26,
    fontWeight: '700',
    color: '#FFFFFF',
    textAlign: 'center',
  },
  questionSubtitle: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.5)',
    textAlign: 'center',
    marginTop: 8,
    marginBottom: 32,
  },
  moodGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'space-between',
  },
  moodCardWrapper: {
    width: '48%',
    marginBottom: 12,
  },
  moodCard: {
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 20,
    alignItems: 'center',
    borderWidth: 1,
  },
  moodIcon: {
    width: 60,
    height: 60,
    borderRadius: 30,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 12,
  },
  moodLabel: {
    fontSize: 13,
    fontWeight: '600',
    color: '#FFFFFF',
    textAlign: 'center',
  },
  customInputContainer: {
    marginBottom: 24,
  },
  customInput: {
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 16,
    fontSize: 15,
    color: '#FFFFFF',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.1)',
    minHeight: 140,
  },
  continueButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#D4AF37',
    paddingVertical: 16,
    borderRadius: 28,
    gap: 8,
  },
  continueButtonText: {
    fontSize: 16,
    fontWeight: '600',
    color: '#0A0A0A',
  },
  buttonDisabled: {
    opacity: 0.5,
  },
  occasionGrid: {
    gap: 10,
  },
  occasionCard: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 14,
    padding: 16,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.1)',
  },
  occasionCardSelected: {
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    borderColor: '#D4AF37',
  },
  occasionLabel: {
    fontSize: 15,
    color: '#FFFFFF',
  },
  occasionLabelSelected: {
    fontWeight: '600',
  },
  budgetOptions: {
    gap: 12,
  },
  budgetCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 18,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.1)',
  },
  budgetCardSelected: {
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    borderColor: '#D4AF37',
  },
  budgetIcon: {
    width: 50,
    height: 50,
    borderRadius: 25,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  budgetIconSelected: {
    backgroundColor: '#D4AF37',
  },
  budgetInfo: {
    flex: 1,
    marginLeft: 14,
  },
  budgetLabel: {
    fontSize: 17,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  budgetLabelSelected: {
    color: '#D4AF37',
  },
  budgetSubtitle: {
    fontSize: 12,
    color: 'rgba(255,255,255,0.5)',
    marginTop: 2,
  },
  popularBadge: {
    backgroundColor: 'rgba(46, 204, 113, 0.2)',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 10,
  },
  popularBadgeText: {
    fontSize: 11,
    color: '#2ECC71',
    fontWeight: '600',
  },
  tipContainer: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    backgroundColor: 'rgba(212, 175, 55, 0.08)',
    borderRadius: 12,
    padding: 14,
    marginTop: 24,
    gap: 10,
  },
  tipText: {
    flex: 1,
    fontSize: 13,
    color: 'rgba(255,255,255,0.7)',
    lineHeight: 19,
  },
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 40,
  },
  loadingIcon: {
    width: 100,
    height: 100,
    borderRadius: 50,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 2,
    borderColor: 'rgba(212, 175, 55, 0.3)',
  },
  loadingTitle: {
    fontSize: 22,
    fontWeight: '600',
    color: '#FFFFFF',
    marginTop: 24,
  },
  loadingText: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.6)',
    textAlign: 'center',
    marginTop: 8,
    lineHeight: 22,
  },
});
