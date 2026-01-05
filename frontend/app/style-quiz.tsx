import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  TextInput,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeIn, FadeInDown } from 'react-native-reanimated';
import { api } from '../src/services/api';
import { useUserStore } from '../src/store/userStore';
import { COLORS, FONTS, SPACING, RADIUS } from '../src/theme/colors';

export default function StyleQuizScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { userId, updateUserProfile, createUser, fetchUser } = useUserStore();

  const [questions, setQuestions] = useState<any[]>([]);
  const [currentQuestion, setCurrentQuestion] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [showCustomInput, setShowCustomInput] = useState(false);
  const [customValue, setCustomValue] = useState('');

  useEffect(() => {
    loadQuestions();
  }, []);

  const loadQuestions = async () => {
    try {
      const response = await api.get('/quiz/questions');
      setQuestions(response.data);
    } catch (error) {
      console.error('Error loading questions:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleAnswer = (questionId: string, answer: string) => {
    if (answer === 'Other (specify)') {
      setShowCustomInput(true);
      return;
    }
    setShowCustomInput(false);
    setCustomValue('');
    setAnswers({ ...answers, [questionId]: answer });
    if (currentQuestion < questions.length - 1) {
      setTimeout(() => setCurrentQuestion(currentQuestion + 1), 300);
    }
  };

  const handleCustomSubmit = (questionId: string) => {
    if (customValue.trim()) {
      setAnswers({ ...answers, [questionId]: customValue.trim() });
      setShowCustomInput(false);
      setCustomValue('');
      if (currentQuestion < questions.length - 1) {
        setTimeout(() => setCurrentQuestion(currentQuestion + 1), 300);
      }
    }
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      let currentUserId = userId;
      
      // If no user exists, create one first
      if (!currentUserId) {
        const newUser = await createUser('Beauty Enthusiast');
        if (newUser) {
          currentUserId = newUser.id;
          // Store in AsyncStorage for persistence
          const AsyncStorage = require('@react-native-async-storage/async-storage').default;
          await AsyncStorage.setItem('glamgenius_user_id', currentUserId);
        }
      }
      
      const quizData = {
        user_id: currentUserId,
        answers: Object.entries(answers).map(([questionId, answer]) => ({
          question_id: questionId,
          answer,
        })),
      };
      
      const response = await api.post('/quiz/submit', quizData);
      
      // Fetch updated user profile
      await fetchUser();
      
      router.replace('/(tabs)/profile');
    } catch (error) {
      console.error('Error submitting quiz:', error);
    } finally {
      setSubmitting(false);
    }
  };

  const progress = questions.length > 0 ? ((currentQuestion + 1) / questions.length) * 100 : 0;
  const canSubmit = Object.keys(answers).length === questions.length;

  if (loading) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color={COLORS.black} />
          <Text style={styles.loadingText}>Loading quiz...</Text>
        </View>
      </View>
    );
  }

  const question = questions[currentQuestion];

  return (
    <KeyboardAvoidingView 
      style={[styles.container, { paddingTop: insets.top }]}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
    >
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.headerBtn} onPress={() => { 
          if (showCustomInput) { setShowCustomInput(false); setCustomValue(''); }
          else if (currentQuestion > 0) { setCurrentQuestion(currentQuestion - 1); }
          else { router.back(); }
        }}>
          <Ionicons name="arrow-back" size={22} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <View style={styles.progressContainer}>
          <View style={styles.progressBar}>
            <View style={[styles.progressFill, { width: `${progress}%` }]} />
          </View>
          <Text style={styles.progressText}>{currentQuestion + 1} of {questions.length}</Text>
        </View>
        <View style={{ width: 40 }} />
      </View>

      {/* Question Content */}
      <ScrollView style={styles.content} showsVerticalScrollIndicator={false}>
        <Animated.View entering={FadeIn} key={question?.id}>
          <Text style={styles.questionText}>{question?.question}</Text>
          
          <View style={styles.optionsContainer}>
            {question?.options?.map((option: string, index: number) => (
              <Animated.View key={option} entering={FadeInDown.delay(index * 50)}>
                <TouchableOpacity
                  style={[styles.optionCard, answers[question.id] === option && styles.optionCardActive]}
                  onPress={() => handleAnswer(question.id, option)}
                >
                  <Text style={[styles.optionText, answers[question.id] === option && styles.optionTextActive]}>
                    {option}
                  </Text>
                  <View style={[styles.radioOuter, answers[question.id] === option && styles.radioOuterActive]}>
                    {answers[question.id] === option && <View style={styles.radioInner} />}
                  </View>
                </TouchableOpacity>
              </Animated.View>
            ))}
            
            {/* Custom "Other" Option */}
            <Animated.View entering={FadeInDown.delay((question?.options?.length || 0) * 50)}>
              <TouchableOpacity
                style={[styles.optionCard, showCustomInput && styles.optionCardActive]}
                onPress={() => handleAnswer(question?.id, 'Other (specify)')}
              >
                <View style={styles.otherOptionContent}>
                  <Ionicons name="create-outline" size={20} color={showCustomInput ? COLORS.black : COLORS.textMuted} />
                  <Text style={[styles.optionText, showCustomInput && styles.optionTextActive]}>
                    Other (specify)
                  </Text>
                </View>
                <View style={[styles.radioOuter, showCustomInput && styles.radioOuterActive]}>
                  {showCustomInput && <View style={styles.radioInner} />}
                </View>
              </TouchableOpacity>
            </Animated.View>
            
            {/* Custom Input Field */}
            {showCustomInput && (
              <Animated.View entering={FadeInDown} style={styles.customInputContainer}>
                <TextInput
                  style={styles.customInput}
                  placeholder="Type your answer..."
                  placeholderTextColor={COLORS.textMuted}
                  value={customValue}
                  onChangeText={setCustomValue}
                  autoFocus
                />
                <TouchableOpacity 
                  style={[styles.customSubmitBtn, !customValue.trim() && styles.customSubmitBtnDisabled]}
                  onPress={() => handleCustomSubmit(question?.id)}
                  disabled={!customValue.trim()}
                >
                  <Text style={styles.customSubmitBtnText}>Continue</Text>
                  <Ionicons name="arrow-forward" size={18} color={COLORS.white} />
                </TouchableOpacity>
              </Animated.View>
            )}
          </View>
        </Animated.View>
      </ScrollView>

      {/* Bottom Action */}
      <View style={[styles.bottomContainer, { paddingBottom: insets.bottom + 16 }]}>
        {currentQuestion === questions.length - 1 && canSubmit ? (
          <TouchableOpacity style={styles.submitBtn} onPress={handleSubmit} disabled={submitting}>
            {submitting ? (
              <ActivityIndicator color={COLORS.white} />
            ) : (
              <>
                <Text style={styles.submitBtnText}>Complete Profile</Text>
                <Ionicons name="checkmark-circle" size={20} color={COLORS.white} />
              </>
            )}
          </TouchableOpacity>
        ) : (
          <View style={styles.navigationDots}>
            {questions.map((_, index) => (
              <TouchableOpacity
                key={index}
                style={[styles.dot, index === currentQuestion && styles.dotActive, index < currentQuestion && styles.dotCompleted]}
                onPress={() => setCurrentQuestion(index)}
              />
            ))}
          </View>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  loadingContainer: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: SPACING.sm },
  loadingText: { fontSize: FONTS.sizes.body, fontFamily: FONTS.family.body, color: COLORS.textSecondary },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: SPACING.md, paddingVertical: SPACING.sm },
  headerBtn: { width: 44, height: 44, borderRadius: 22, backgroundColor: COLORS.backgroundSecondary, justifyContent: 'center', alignItems: 'center', borderWidth: 1, borderColor: COLORS.border },
  progressContainer: { flex: 1, marginHorizontal: SPACING.md },
  progressBar: { height: 6, backgroundColor: COLORS.border, borderRadius: 3 },
  progressFill: { height: '100%', backgroundColor: COLORS.black, borderRadius: 3 },
  progressText: { fontSize: FONTS.sizes.caption, fontFamily: FONTS.family.body, color: COLORS.textMuted, textAlign: 'center', marginTop: 6 },
  content: { padding: SPACING.lg, paddingBottom: 120 },
  questionText: { fontSize: FONTS.sizes.h2, fontFamily: FONTS.family.heading, color: COLORS.textPrimary, marginBottom: SPACING.lg, lineHeight: 32 },
  optionsContainer: { gap: SPACING.sm },
  optionCard: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: COLORS.card, borderRadius: RADIUS.md, padding: SPACING.md, borderWidth: 2, borderColor: COLORS.border },
  optionCardActive: { borderColor: COLORS.black, backgroundColor: COLORS.backgroundSecondary },
  optionText: { flex: 1, fontSize: FONTS.sizes.bodyLg, fontFamily: FONTS.family.body, color: COLORS.textPrimary },
  optionTextActive: { fontFamily: FONTS.family.bodySemibold, color: COLORS.black },
  radioOuter: { width: 24, height: 24, borderRadius: 12, borderWidth: 2, borderColor: COLORS.border, justifyContent: 'center', alignItems: 'center' },
  radioOuterActive: { borderColor: COLORS.black },
  radioInner: { width: 12, height: 12, borderRadius: 6, backgroundColor: COLORS.black },
  bottomContainer: { paddingHorizontal: SPACING.lg, paddingTop: SPACING.md, backgroundColor: COLORS.background, borderTopWidth: 1, borderTopColor: COLORS.border },
  submitBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.black, paddingVertical: 18, borderRadius: RADIUS.full, gap: SPACING.sm },
  submitBtnText: { fontSize: FONTS.sizes.bodyLg, fontFamily: FONTS.family.bodySemibold, color: COLORS.white },
  navigationDots: { flexDirection: 'row', justifyContent: 'center', gap: SPACING.sm },
  dot: { width: 10, height: 10, borderRadius: 5, backgroundColor: COLORS.border },
  dotActive: { backgroundColor: COLORS.black, width: 24 },
  dotCompleted: { backgroundColor: COLORS.accent },
});
