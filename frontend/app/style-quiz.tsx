import React, { useState, useEffect } from 'react';
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
import Animated, { FadeIn, FadeInDown } from 'react-native-reanimated';
import { api } from '../src/services/api';
import { useUserStore } from '../src/store/userStore';

// Style Quiz for building beauty profile
const PROFILE_QUESTIONS = [
  {
    id: 'hair_type',
    question: 'What is your hair type?',
    options: [
      { id: 'straight', label: 'Straight', icon: 'remove-outline' },
      { id: 'wavy', label: 'Wavy', icon: 'water-outline' },
      { id: 'curly', label: 'Curly', icon: 'sync-outline' },
      { id: 'coily', label: 'Coily/Kinky', icon: 'infinite-outline' },
    ],
  },
  {
    id: 'skin_type',
    question: 'How would you describe your skin?',
    options: [
      { id: 'oily', label: 'Oily (Shiny, prone to acne)', icon: 'water' },
      { id: 'dry', label: 'Dry (Tight, flaky)', icon: 'sunny-outline' },
      { id: 'combination', label: 'Combination (Oily T-zone)', icon: 'contrast-outline' },
      { id: 'normal', label: 'Normal (Balanced)', icon: 'checkmark-circle-outline' },
      { id: 'sensitive', label: 'Sensitive (Easily irritated)', icon: 'alert-circle-outline' },
    ],
  },
  {
    id: 'hair_concern',
    question: "What's your main hair concern?",
    options: [
      { id: 'frizz', label: 'Frizz & Dryness', icon: 'cloudy-outline' },
      { id: 'hairfall', label: 'Hair Fall', icon: 'trending-down-outline' },
      { id: 'dandruff', label: 'Dandruff/Itchy Scalp', icon: 'snow-outline' },
      { id: 'damage', label: 'Damage & Breakage', icon: 'flash-outline' },
      { id: 'grey', label: 'Grey Hair', icon: 'contrast-outline' },
      { id: 'none', label: 'No major concerns', icon: 'checkmark-outline' },
    ],
  },
  {
    id: 'skin_concern',
    question: "What's your main skin concern?",
    options: [
      { id: 'acne', label: 'Acne & Pimples', icon: 'ellipse-outline' },
      { id: 'pigmentation', label: 'Pigmentation/Dark Spots', icon: 'contrast' },
      { id: 'aging', label: 'Aging & Wrinkles', icon: 'time-outline' },
      { id: 'dullness', label: 'Dullness/Tired Skin', icon: 'moon-outline' },
      { id: 'tan', label: 'Tan/Uneven Tone', icon: 'sunny' },
      { id: 'none', label: 'No major concerns', icon: 'checkmark-outline' },
    ],
  },
  {
    id: 'visit_frequency',
    question: 'How often do you visit a salon?',
    options: [
      { id: 'weekly', label: 'Weekly', icon: 'calendar-outline' },
      { id: 'biweekly', label: 'Every 2 weeks', icon: 'calendar-outline' },
      { id: 'monthly', label: 'Monthly', icon: 'calendar-outline' },
      { id: 'quarterly', label: 'Every 2-3 months', icon: 'calendar-outline' },
      { id: 'rarely', label: 'Rarely/Special occasions', icon: 'calendar-outline' },
    ],
  },
];

export default function StyleQuizScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { userId, updateUser } = useUserStore();

  const [currentStep, setCurrentStep] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);

  const currentQuestion = PROFILE_QUESTIONS[currentStep];
  const progress = ((currentStep + 1) / PROFILE_QUESTIONS.length) * 100;
  const isLastStep = currentStep === PROFILE_QUESTIONS.length - 1;

  const handleSelect = (optionId: string) => {
    setAnswers({ ...answers, [currentQuestion.id]: optionId });
  };

  const handleNext = () => {
    if (currentStep < PROFILE_QUESTIONS.length - 1) {
      setCurrentStep(currentStep + 1);
    }
  };

  const handleBack = () => {
    if (currentStep > 0) {
      setCurrentStep(currentStep - 1);
    } else {
      router.back();
    }
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      // Update user profile with answers
      await updateUser({
        hair_type: answers.hair_type,
        skin_type: answers.skin_type,
        hair_concerns: answers.hair_concern !== 'none' ? [answers.hair_concern] : [],
        skin_concerns: answers.skin_concern !== 'none' ? [answers.skin_concern] : [],
        preferences: { visit_frequency: answers.visit_frequency },
      });

      // Navigate to home with success
      router.replace('/(tabs)/home');
    } catch (error) {
      console.error('Error saving profile:', error);
    } finally {
      setSubmitting(false);
    }
  };

  const canProceed = answers[currentQuestion.id];

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity onPress={handleBack} style={styles.backButton}>
          <Ionicons name="arrow-back" size={24} color="#FFFFFF" />
        </TouchableOpacity>
        <View style={styles.headerCenter}>
          <Text style={styles.headerTitle}>Build Your Profile</Text>
          <Text style={styles.headerSubtitle}>For personalized packages</Text>
        </View>
        <View style={{ width: 40 }} />
      </View>

      {/* Progress Bar */}
      <View style={styles.progressContainer}>
        <View style={styles.progressBar}>
          <View style={[styles.progressFill, { width: `${progress}%` }]} />
        </View>
        <Text style={styles.progressText}>
          {currentStep + 1} of {PROFILE_QUESTIONS.length}
        </Text>
      </View>

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.scrollContent}
      >
        <Animated.View entering={FadeIn} key={currentStep}>
          <Text style={styles.questionText}>{currentQuestion.question}</Text>
          
          <View style={styles.optionsContainer}>
            {currentQuestion.options.map((option, index) => (
              <Animated.View
                key={option.id}
                entering={FadeInDown.delay(index * 50)}
              >
                <TouchableOpacity
                  style={[
                    styles.optionCard,
                    answers[currentQuestion.id] === option.id && styles.optionCardSelected,
                  ]}
                  onPress={() => handleSelect(option.id)}
                >
                  <View style={[
                    styles.optionIcon,
                    answers[currentQuestion.id] === option.id && styles.optionIconSelected,
                  ]}>
                    <Ionicons
                      name={option.icon as any}
                      size={24}
                      color={answers[currentQuestion.id] === option.id ? '#0A0A0A' : '#D4AF37'}
                    />
                  </View>
                  <Text style={[
                    styles.optionLabel,
                    answers[currentQuestion.id] === option.id && styles.optionLabelSelected,
                  ]}>
                    {option.label}
                  </Text>
                  {answers[currentQuestion.id] === option.id && (
                    <Ionicons name="checkmark-circle" size={24} color="#D4AF37" />
                  )}
                </TouchableOpacity>
              </Animated.View>
            ))}
          </View>
        </Animated.View>
      </ScrollView>

      {/* Footer */}
      <View style={[styles.footer, { paddingBottom: insets.bottom + 16 }]}>
        {isLastStep ? (
          <TouchableOpacity
            style={[styles.primaryButton, !canProceed && styles.buttonDisabled]}
            onPress={handleSubmit}
            disabled={!canProceed || submitting}
          >
            {submitting ? (
              <ActivityIndicator color="#0A0A0A" />
            ) : (
              <>
                <Ionicons name="checkmark-circle" size={20} color="#0A0A0A" />
                <Text style={styles.primaryButtonText}>Save Profile</Text>
              </>
            )}
          </TouchableOpacity>
        ) : (
          <TouchableOpacity
            style={[styles.primaryButton, !canProceed && styles.buttonDisabled]}
            onPress={handleNext}
            disabled={!canProceed}
          >
            <Text style={styles.primaryButtonText}>Continue</Text>
            <Ionicons name="arrow-forward" size={20} color="#0A0A0A" />
          </TouchableOpacity>
        )}
      </View>
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
  headerCenter: {
    alignItems: 'center',
  },
  headerTitle: {
    fontSize: 18,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  headerSubtitle: {
    fontSize: 12,
    color: 'rgba(255,255,255,0.5)',
    marginTop: 2,
  },
  progressContainer: {
    paddingHorizontal: 20,
    marginTop: 8,
  },
  progressBar: {
    height: 4,
    backgroundColor: 'rgba(255, 255, 255, 0.1)',
    borderRadius: 2,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    backgroundColor: '#D4AF37',
    borderRadius: 2,
  },
  progressText: {
    fontSize: 12,
    color: 'rgba(255, 255, 255, 0.5)',
    marginTop: 8,
    textAlign: 'center',
  },
  scrollContent: {
    paddingHorizontal: 20,
    paddingTop: 32,
    paddingBottom: 100,
  },
  questionText: {
    fontSize: 24,
    fontWeight: '700',
    color: '#FFFFFF',
    textAlign: 'center',
    marginBottom: 32,
  },
  optionsContainer: {
    gap: 12,
  },
  optionCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 16,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.1)',
  },
  optionCardSelected: {
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    borderColor: '#D4AF37',
  },
  optionIcon: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  optionIconSelected: {
    backgroundColor: '#D4AF37',
  },
  optionLabel: {
    flex: 1,
    fontSize: 15,
    color: '#FFFFFF',
    marginLeft: 14,
  },
  optionLabelSelected: {
    fontWeight: '600',
  },
  footer: {
    paddingHorizontal: 20,
    paddingTop: 16,
    borderTopWidth: 1,
    borderTopColor: 'rgba(255, 255, 255, 0.1)',
  },
  primaryButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#D4AF37',
    paddingVertical: 16,
    borderRadius: 28,
    gap: 8,
  },
  primaryButtonText: {
    fontSize: 16,
    fontWeight: '600',
    color: '#0A0A0A',
  },
  buttonDisabled: {
    opacity: 0.5,
  },
});
