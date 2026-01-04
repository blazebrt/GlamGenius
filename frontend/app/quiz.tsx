import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
} from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeIn, FadeInDown } from 'react-native-reanimated';
import { api } from '../src/services/api';
import { useUserStore } from '../src/store/userStore';

interface Question {
  id: string;
  question: string;
  options: string[];
}

const BUDGET_OPTIONS = [
  { id: 'low', label: '$50-100', icon: 'wallet-outline' },
  { id: 'medium', label: '$100-200', icon: 'card-outline' },
  { id: 'high', label: '$200-400', icon: 'diamond-outline' },
  { id: 'premium', label: '$400+', icon: 'sparkles' },
];

const OCCASION_OPTIONS = [
  { id: 'everyday', label: 'Everyday Look' },
  { id: 'office', label: 'Office/Professional' },
  { id: 'party', label: 'Party/Night Out' },
  { id: 'wedding', label: 'Wedding/Special Event' },
  { id: 'date', label: 'Date Night' },
];

export default function QuizScreen() {
  const router = useRouter();
  const params = useLocalSearchParams();
  const insets = useSafeAreaInsets();
  const { userId, updateUser } = useUserStore();

  const [questions, setQuestions] = useState<Question[]>([]);
  const [currentStep, setCurrentStep] = useState(0); // 0: occasion, 1: budget, 2+: quiz questions
  const [selectedOccasion, setSelectedOccasion] = useState(params.occasion as string || '');
  const [selectedBudget, setSelectedBudget] = useState('');
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    loadQuestions();
    if (params.occasion) {
      setCurrentStep(1); // Skip to budget if occasion pre-selected
    }
  }, []);

  const loadQuestions = async () => {
    setLoading(true);
    try {
      const response = await api.get('/quiz/questions');
      setQuestions(response.data);
    } catch (error) {
      console.error('Error loading questions:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleNext = () => {
    if (currentStep === 0 && selectedOccasion) {
      setCurrentStep(1);
    } else if (currentStep === 1 && selectedBudget) {
      setCurrentStep(2);
    } else if (currentStep > 1 && currentStep < questions.length + 1) {
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

  const handleAnswerSelect = (questionId: string, answer: string) => {
    setAnswers({ ...answers, [questionId]: answer });
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      const quizAnswers = Object.entries(answers).map(([question_id, answer]) => ({
        question_id,
        answer,
      }));

      const response = await api.post('/quiz/submit', {
        user_id: userId,
        answers: quizAnswers,
        occasion: selectedOccasion,
        budget: selectedBudget,
      });

      // Update user budget preference
      await updateUser({ budget_range: selectedBudget });

      // Navigate to recommendations
      router.push({
        pathname: '/recommendations',
        params: { 
          recommendationId: response.data.recommendation_id,
          recommendations: JSON.stringify(response.data.recommendations),
        },
      });
    } catch (error) {
      console.error('Error submitting quiz:', error);
    } finally {
      setSubmitting(false);
    }
  };

  const totalSteps = questions.length + 2; // occasion + budget + questions
  const progress = ((currentStep + 1) / totalSteps) * 100;
  const currentQuestion = currentStep > 1 ? questions[currentStep - 2] : null;
  const canProceed =
    (currentStep === 0 && selectedOccasion) ||
    (currentStep === 1 && selectedBudget) ||
    (currentStep > 1 && currentQuestion && answers[currentQuestion.id]);
  const isLastStep = currentStep === totalSteps - 1;

  if (loading) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <ActivityIndicator size="large" color="#D4AF37" />
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
        <Text style={styles.headerTitle}>Style Quiz</Text>
        <View style={{ width: 40 }} />
      </View>

      {/* Progress Bar */}
      <View style={styles.progressContainer}>
        <View style={styles.progressBar}>
          <Animated.View
            style={[styles.progressFill, { width: `${progress}%` }]}
          />
        </View>
        <Text style={styles.progressText}>
          Step {currentStep + 1} of {totalSteps}
        </Text>
      </View>

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.scrollContent}
      >
        {/* Occasion Selection */}
        {currentStep === 0 && (
          <Animated.View entering={FadeIn}>
            <Text style={styles.questionTitle}>What's the occasion?</Text>
            <Text style={styles.questionSubtitle}>
              Select the event you're preparing for
            </Text>
            <View style={styles.optionsGrid}>
              {OCCASION_OPTIONS.map((option, index) => (
                <Animated.View
                  key={option.id}
                  entering={FadeInDown.delay(index * 50)}
                >
                  <TouchableOpacity
                    style={[
                      styles.optionCard,
                      selectedOccasion === option.id && styles.optionCardSelected,
                    ]}
                    onPress={() => setSelectedOccasion(option.id)}
                  >
                    <Text
                      style={[
                        styles.optionText,
                        selectedOccasion === option.id && styles.optionTextSelected,
                      ]}
                    >
                      {option.label}
                    </Text>
                    {selectedOccasion === option.id && (
                      <Ionicons name="checkmark-circle" size={24} color="#D4AF37" />
                    )}
                  </TouchableOpacity>
                </Animated.View>
              ))}
            </View>
          </Animated.View>
        )}

        {/* Budget Selection */}
        {currentStep === 1 && (
          <Animated.View entering={FadeIn}>
            <Text style={styles.questionTitle}>What's your budget?</Text>
            <Text style={styles.questionSubtitle}>
              Select your preferred spending range
            </Text>
            <View style={styles.budgetOptions}>
              {BUDGET_OPTIONS.map((option, index) => (
                <Animated.View
                  key={option.id}
                  entering={FadeInDown.delay(index * 50)}
                >
                  <TouchableOpacity
                    style={[
                      styles.budgetCard,
                      selectedBudget === option.id && styles.budgetCardSelected,
                    ]}
                    onPress={() => setSelectedBudget(option.id)}
                  >
                    <View
                      style={[
                        styles.budgetIcon,
                        selectedBudget === option.id && styles.budgetIconSelected,
                      ]}
                    >
                      <Ionicons
                        name={option.icon as any}
                        size={24}
                        color={selectedBudget === option.id ? '#0A0A0A' : '#D4AF37'}
                      />
                    </View>
                    <Text
                      style={[
                        styles.budgetLabel,
                        selectedBudget === option.id && styles.budgetLabelSelected,
                      ]}
                    >
                      {option.label}
                    </Text>
                  </TouchableOpacity>
                </Animated.View>
              ))}
            </View>
          </Animated.View>
        )}

        {/* Quiz Questions */}
        {currentStep > 1 && currentQuestion && (
          <Animated.View entering={FadeIn}>
            <Text style={styles.questionTitle}>{currentQuestion.question}</Text>
            <View style={styles.quizOptions}>
              {currentQuestion.options.map((option, index) => (
                <Animated.View
                  key={index}
                  entering={FadeInDown.delay(index * 50)}
                >
                  <TouchableOpacity
                    style={[
                      styles.quizOption,
                      answers[currentQuestion.id] === option && styles.quizOptionSelected,
                    ]}
                    onPress={() => handleAnswerSelect(currentQuestion.id, option)}
                  >
                    <Text
                      style={[
                        styles.quizOptionText,
                        answers[currentQuestion.id] === option && styles.quizOptionTextSelected,
                      ]}
                    >
                      {option}
                    </Text>
                    {answers[currentQuestion.id] === option && (
                      <Ionicons name="checkmark-circle" size={22} color="#D4AF37" />
                    )}
                  </TouchableOpacity>
                </Animated.View>
              ))}
            </View>
          </Animated.View>
        )}
      </ScrollView>

      {/* Navigation Buttons */}
      <View style={[styles.footer, { paddingBottom: insets.bottom + 16 }]}>
        {isLastStep ? (
          <TouchableOpacity
            style={[
              styles.submitButton,
              !canProceed && styles.buttonDisabled,
            ]}
            onPress={handleSubmit}
            disabled={!canProceed || submitting}
          >
            {submitting ? (
              <ActivityIndicator color="#0A0A0A" />
            ) : (
              <>
                <Ionicons name="sparkles" size={20} color="#0A0A0A" />
                <Text style={styles.submitButtonText}>Get Recommendations</Text>
              </>
            )}
          </TouchableOpacity>
        ) : (
          <TouchableOpacity
            style={[
              styles.nextButton,
              !canProceed && styles.buttonDisabled,
            ]}
            onPress={handleNext}
            disabled={!canProceed}
          >
            <Text style={styles.nextButtonText}>Continue</Text>
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
  headerTitle: {
    fontSize: 18,
    fontWeight: '600',
    color: '#FFFFFF',
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
  questionTitle: {
    fontSize: 24,
    fontWeight: '700',
    color: '#FFFFFF',
    textAlign: 'center',
  },
  questionSubtitle: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.5)',
    textAlign: 'center',
    marginTop: 8,
  },
  optionsGrid: {
    marginTop: 32,
    gap: 12,
  },
  optionCard: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 18,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.1)',
  },
  optionCardSelected: {
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    borderColor: '#D4AF37',
  },
  optionText: {
    fontSize: 16,
    color: '#FFFFFF',
  },
  optionTextSelected: {
    fontWeight: '600',
  },
  budgetOptions: {
    marginTop: 32,
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'space-between',
    gap: 12,
  },
  budgetCard: {
    width: '47%',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 20,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.1)',
    minWidth: 150,
  },
  budgetCardSelected: {
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    borderColor: '#D4AF37',
  },
  budgetIcon: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 12,
  },
  budgetIconSelected: {
    backgroundColor: '#D4AF37',
  },
  budgetLabel: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  budgetLabelSelected: {
    color: '#D4AF37',
  },
  quizOptions: {
    marginTop: 32,
    gap: 12,
  },
  quizOption: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 18,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.1)',
  },
  quizOptionSelected: {
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    borderColor: '#D4AF37',
  },
  quizOptionText: {
    fontSize: 15,
    color: '#FFFFFF',
    flex: 1,
  },
  quizOptionTextSelected: {
    fontWeight: '600',
  },
  footer: {
    paddingHorizontal: 20,
    paddingTop: 16,
    borderTopWidth: 1,
    borderTopColor: 'rgba(255, 255, 255, 0.1)',
  },
  nextButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#D4AF37',
    paddingVertical: 16,
    borderRadius: 28,
    gap: 8,
  },
  nextButtonText: {
    fontSize: 16,
    fontWeight: '600',
    color: '#0A0A0A',
  },
  submitButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#D4AF37',
    paddingVertical: 16,
    borderRadius: 28,
    gap: 8,
  },
  submitButtonText: {
    fontSize: 16,
    fontWeight: '600',
    color: '#0A0A0A',
  },
  buttonDisabled: {
    opacity: 0.5,
  },
});
