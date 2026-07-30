import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { api } from '../src/services/api';
import { useUserStore } from '../src/store/userStore';
import { usePlanStore } from '../src/store/planStore';
import { COLORS, FONTS, SPACING, RADIUS } from '../src/theme/colors';

export default function StyleQuizScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { userId } = useUserStore();
  const setLatestPlan = usePlanStore((s) => s.setLatestPlan);
  const [questions, setQuestions] = useState<any[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [index, setIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const res = await api.get('/quiz/questions');
        setQuestions(res.data);
      } catch {
        setQuestions([]);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const current = questions[index];

  const select = (option: string) => {
    if (!current) return;
    const next = { ...answers, [current.id]: option };
    setAnswers(next);
    if (index < questions.length - 1) setIndex(index + 1);
  };

  const submit = async () => {
    if (!userId) {
      Alert.alert('Start from home', 'Create a profile first.');
      return;
    }
    setSubmitting(true);
    try {
      const payload = {
        user_id: userId,
        answers: Object.entries(answers).map(([question_id, answer]) => ({ question_id, answer })),
        occasion: 'everyday',
        budget: 'mid',
      };
      const res = await api.post('/quiz/submit', payload);
      setLatestPlan(res.data.plan);
      router.push('/recommendations');
    } catch (err) {
      console.error('quiz submit failed', err);
      Alert.alert('Quiz error', 'Could not save your quiz. Try again.');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <View style={[styles.container, styles.center, { paddingTop: insets.top }]}>
        <ActivityIndicator color={COLORS.primary} />
      </View>
    );
  }

  const done = Object.keys(answers).length >= questions.length;

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.top}>
        <TouchableOpacity onPress={() => router.back()}>
          <Ionicons name="close" size={22} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.topTitle}>Profile quiz</Text>
        <Text style={styles.progress}>{Math.min(index + 1, questions.length)}/{questions.length}</Text>
      </View>

      <ScrollView contentContainerStyle={{ padding: SPACING.lg, paddingBottom: 100 }}>
        {current && (
          <>
            <Text style={styles.question}>{current.question}</Text>
            {current.options.map((opt: string) => (
              <TouchableOpacity
                key={opt}
                style={[styles.option, answers[current.id] === opt && styles.optionActive]}
                onPress={() => select(opt)}
              >
                <Text style={[styles.optionText, answers[current.id] === opt && styles.optionTextActive]}>{opt}</Text>
              </TouchableOpacity>
            ))}
          </>
        )}

        {done && (
          <TouchableOpacity style={styles.cta} onPress={submit} disabled={submitting}>
            {submitting ? <ActivityIndicator color={COLORS.white} /> : (
              <Text style={styles.ctaText}>See my coach plan</Text>
            )}
          </TouchableOpacity>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  center: { alignItems: 'center', justifyContent: 'center' },
  top: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: SPACING.lg, paddingVertical: 12,
  },
  topTitle: { fontFamily: FONTS.family.bodySemibold, fontSize: 16, color: COLORS.textPrimary },
  progress: { fontFamily: FONTS.family.bodyMedium, fontSize: 13, color: COLORS.textMuted },
  question: { fontFamily: FONTS.family.heading, fontSize: 26, color: COLORS.textPrimary, marginBottom: 20, lineHeight: 32 },
  option: {
    padding: 16, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: COLORS.border,
    backgroundColor: COLORS.backgroundSecondary, marginBottom: 10,
  },
  optionActive: { borderColor: COLORS.primary, backgroundColor: COLORS.primaryLight },
  optionText: { fontFamily: FONTS.family.bodyMedium, fontSize: 15, color: COLORS.textPrimary },
  optionTextActive: { color: COLORS.primaryDark },
  cta: {
    marginTop: 24, backgroundColor: COLORS.primary, borderRadius: RADIUS.lg, paddingVertical: 16, alignItems: 'center',
  },
  ctaText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.white, fontSize: 16 },
});
