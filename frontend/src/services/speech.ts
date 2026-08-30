/**
 * Reading the verdict aloud.
 *
 * India is voice-first, and a large number of people who will use this can
 * scan a barcode more easily than they can read a nutrition panel. The speaker
 * button is on every verdict, for everybody, in the same place and the same
 * size. It is never presented as an accessibility feature and nothing in the
 * interface labels the person using it.
 *
 * The module degrades quietly: if speech is unavailable the button reports it
 * once rather than pretending to work.
 */
import * as Speech from 'expo-speech';
import { Platform } from 'react-native';

/** Hindi-accented English reads Indian brand names better than en-US. */
export const SPEECH_LANGUAGE = 'en-IN';
/** Slightly under natural pace: a verdict is read once and must land. */
export const SPEECH_RATE = 0.95;

export const isSpeechAvailable = (): boolean =>
  Platform.OS !== 'web' && typeof Speech?.speak === 'function';

export interface SpeakHandlers {
  onStart?: () => void;
  onDone?: () => void;
  onError?: () => void;
}

export async function speak(text: string, handlers: SpeakHandlers = {}): Promise<boolean> {
  if (!isSpeechAvailable() || !text.trim()) {
    handlers.onError?.();
    return false;
  }
  try {
    // Speaking twice over itself is worse than not speaking.
    await Speech.stop();
    Speech.speak(text, {
      language: SPEECH_LANGUAGE,
      rate: SPEECH_RATE,
      onStart: handlers.onStart,
      onDone: handlers.onDone,
      onStopped: handlers.onDone,
      onError: handlers.onError,
    });
    return true;
  } catch {
    handlers.onError?.();
    return false;
  }
}

export async function stopSpeaking(): Promise<void> {
  if (!isSpeechAvailable()) return;
  try {
    await Speech.stop();
  } catch {
    // Nothing was speaking.
  }
}

/**
 * A single letter is read as a word otherwise — "grade bee" for B.
 * Spelling it out keeps the letter audible, which is the whole verdict.
 */
export const spokenGrade = (letter: string): string => letter.split('').join(' ');
