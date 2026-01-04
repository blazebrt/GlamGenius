import React, { useEffect, useState } from 'react';
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

export default function StyleQuizScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { userId, updateUserProfile } = useUserStore();

  const [questions, setQuestions] = useState<any[]>([]);
  const [currentQuestion, setCurrentQuestion] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

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
    setAnswers({ ...answers, [questionId]: answer });
    if (currentQuestion < questions.length - 1) {
      setTimeout(() => setCurrentQuestion(currentQuestion + 1), 300);
    }
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      await api.post('/quiz/submit', { user_id: userId, answers });
      await updateUserProfile();
      router.replace('/(tabs)/home');
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
          <ActivityIndicator size="large" color="#0EA5E9" />
          <Text style={styles.loadingText}>Loading quiz...</Text>
        </View>
      </View>
    );
  }

  const question = questions[currentQuestion];

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity onPress={() => currentQuestion > 0 ? setCurrentQuestion(currentQuestion - 1) : router.back()} style={styles.headerBtn}>
          <Ionicons name="arrow-back" size={24} color="#1E293B" />
        </TouchableOpacity>
        <View style={styles.progressContainer}>
          <View style={styles.progressBar}>
            <View style={[styles.progressFill, { width: `${progress}%` }]} />
          </View>
          <Text style={styles.progressText}>{currentQuestion + 1} of {questions.length}</Text>
        </View>
        <View style={{ width: 40 }} />
      </View>

      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.content}>
        {question && (
          <Animated.View key={question.id} entering={FadeIn}>
            <Text style={styles.questionText}>{question.question}</Text>
            <View style={styles.optionsContainer}>
              {question.options?.map((option: string, index: number) => {
                const isSelected = answers[question.id] === option;
                return (
                  <Animated.View key={index} entering={FadeInDown.delay(index * 50)}>
                    <TouchableOpacity
                      style={[styles.optionCard, isSelected && styles.optionCardActive]}
                      onPress={() => handleAnswer(question.id, option)}
                    >
                      <Text style={[styles.optionText, isSelected && styles.optionTextActive]}>{option}</Text>
                      <View style={[styles.radioOuter, isSelected && styles.radioOuterActive]}>
                        {isSelected && <View style={styles.radioInner} />}
                      </View>
                    </TouchableOpacity>
                  </Animated.View>
                );
              })}
            </View>
          </Animated.View>
        )}
      </ScrollView>

      {/* Bottom Action */}
      <View style={[styles.bottomContainer, { paddingBottom: insets.bottom + 16 }]}>
        {currentQuestion === questions.length - 1 && canSubmit ? (
          <TouchableOpacity style={styles.submitBtn} onPress={handleSubmit} disabled={submitting}>
            {submitting ? (
              <ActivityIndicator color="#FFFFFF" />
            ) : (
              <>
                <Text style={styles.submitBtnText}>Complete Profile</Text>
                <Ionicons name="checkmark-circle" size={20} color="#FFFFFF" />
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
  container: { flex: 1, backgroundColor: '#F8FAFC' },
  loadingContainer: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: 12 },
  loadingText: { fontSize: 14, color: '#64748B' },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 16, paddingVertical: 12 },
  headerBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: '#FFFFFF', justifyContent: 'center', alignItems: 'center', borderWidth: 1, borderColor: '#E2E8F0' },
  progressContainer: { flex: 1, marginHorizontal: 16 },
  progressBar: { height: 6, backgroundColor: '#E2E8F0', borderRadius: 3 },
  progressFill: { height: '100%', backgroundColor: '#0EA5E9', borderRadius: 3 },
  progressText: { fontSize: 12, color: '#64748B', textAlign: 'center', marginTop: 6 },
  content: { padding: 20, paddingBottom: 120 },
  questionText: { fontSize: 22, fontWeight: '600', color: '#1E293B', marginBottom: 24, lineHeight: 30 },
  optionsContainer: { gap: 12 },
  optionCard: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: '#FFFFFF', borderRadius: 14, padding: 18, borderWidth: 2, borderColor: '#E2E8F0' },
  optionCardActive: { borderColor: '#0EA5E9', backgroundColor: '#E0F2FE' },
  optionText: { flex: 1, fontSize: 16, color: '#1E293B' },
  optionTextActive: { fontWeight: '500', color: '#0284C7' },
  radioOuter: { width: 24, height: 24, borderRadius: 12, borderWidth: 2, borderColor: '#E2E8F0', justifyContent: 'center', alignItems: 'center' },
  radioOuterActive: { borderColor: '#0EA5E9' },
  radioInner: { width: 12, height: 12, borderRadius: 6, backgroundColor: '#0EA5E9' },
  bottomContainer: { paddingHorizontal: 20, paddingTop: 16, backgroundColor: '#FFFFFF', borderTopWidth: 1, borderTopColor: '#E2E8F0' },
  submitBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', backgroundColor: '#0EA5E9', paddingVertical: 16, borderRadius: 28, gap: 10 },
  submitBtnText: { fontSize: 16, fontWeight: '600', color: '#FFFFFF' },
  navigationDots: { flexDirection: 'row', justifyContent: 'center', gap: 8 },
  dot: { width: 10, height: 10, borderRadius: 5, backgroundColor: '#E2E8F0' },
  dotActive: { backgroundColor: '#0EA5E9', width: 24 },
  dotCompleted: { backgroundColor: '#10B981' },
});
