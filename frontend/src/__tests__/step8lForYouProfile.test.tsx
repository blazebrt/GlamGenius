/**
 * Step 8L — the two skin facts FOR YOU reads.
 *
 * The screen exists to make exactly two controlled answers editable. Every
 * test here is about the boundary: only the approved values, no free text, and
 * no trust metadata asserted by the phone.
 */
import React from 'react';
import Screen, * as ForYouProfileScreen from '../../app/for-you-profile';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react-native';
import { TextInput } from 'react-native';
import fs from 'fs';
import path from 'path';

const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: jest.fn() }),
}));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0 }),
}));

const mockGetProfile = jest.fn();
const mockPatchProfile = jest.fn();
jest.mock('../services/apiV2', () => ({
  getAppearanceProfile: (...args: unknown[]) => mockGetProfile(...args),
  patchAppearanceProfile: (...args: unknown[]) => mockPatchProfile(...args),
}));

const { SKIN_USUAL_FEEL_OPTIONS, SKIN_SENSITIVITY_OPTIONS } = ForYouProfileScreen;

const emptyProfile = { id: 'p', version: 1, baseline_status: 'x', attributes: [], readiness: [] };

beforeEach(() => {
  jest.clearAllMocks();
  mockGetProfile.mockResolvedValue(emptyProfile);
  mockPatchProfile.mockResolvedValue(emptyProfile);
});

async function open(profile = emptyProfile) {
  mockGetProfile.mockResolvedValue(profile);
  render(<Screen />);
  await screen.findByText('Skin details used by FOR YOU');
}

describe('the controlled vocabulary', () => {
  it('offers exactly the approved usual-feel values', async () => {
    await open();
    expect(SKIN_USUAL_FEEL_OPTIONS.map((o: { value: string }) => o.value)).toEqual([
      'comfortable', 'often_dry_or_tight', 'often_oily', 'mixed', 'not_sure',
    ]);
    SKIN_USUAL_FEEL_OPTIONS.forEach((option: { value: string }) => {
      expect(screen.getByTestId(`usual-feel-${option.value}`)).toBeTruthy();
    });
  });

  it('offers exactly the approved sensitivity values', async () => {
    await open();
    expect(SKIN_SENSITIVITY_OPTIONS.map((o: { value: string }) => o.value)).toEqual([
      'rarely_reactive', 'sometimes_reactive', 'often_reactive', 'not_sure',
    ]);
    SKIN_SENSITIVITY_OPTIONS.forEach((option: { value: string }) => {
      expect(screen.getByTestId(`sensitivity-${option.value}`)).toBeTruthy();
    });
  });

  it('has no way to type an arbitrary value', async () => {
    const { UNSAFE_queryAllByType } = render(<Screen />);
    await screen.findByText('Skin details used by FOR YOU');
    expect(UNSAFE_queryAllByType(TextInput)).toHaveLength(0);
  });
});

describe('saving', () => {
  it('sends only the two keys and their exact values', async () => {
    await open();
    fireEvent.press(screen.getByTestId('usual-feel-often_dry_or_tight'));
    fireEvent.press(screen.getByTestId('sensitivity-rarely_reactive'));
    await act(async () => { fireEvent.press(screen.getByTestId('for-you-profile-save')); });

    await waitFor(() => expect(mockPatchProfile).toHaveBeenCalledTimes(1));
    expect(mockPatchProfile).toHaveBeenCalledWith([
      { key: 'care_skin_usual_feel', value: 'often_dry_or_tight' },
      { key: 'care_skin_sensitivity', value: 'rarely_reactive' },
    ]);
  });

  it('never asserts source, confidence or verification state from the phone', async () => {
    await open();
    fireEvent.press(screen.getByTestId('usual-feel-mixed'));
    fireEvent.press(screen.getByTestId('sensitivity-not_sure'));
    await act(async () => { fireEvent.press(screen.getByTestId('for-you-profile-save')); });
    await waitFor(() => expect(mockPatchProfile).toHaveBeenCalled());
    const payload = JSON.stringify(mockPatchProfile.mock.calls[0][0]);
    for (const banned of ['source', 'confidence', 'verification_state', 'ai_run', 'user_declared']) {
      expect(payload).not.toContain(banned);
    }
    expect(JSON.parse(payload).every((row: object) => Object.keys(row).sort().join() === 'key,value')).toBe(true);
  });

  it('will not save until both questions are answered', async () => {
    await open();
    await act(async () => { fireEvent.press(screen.getByTestId('for-you-profile-save')); });
    expect(mockPatchProfile).not.toHaveBeenCalled();
    fireEvent.press(screen.getByTestId('usual-feel-comfortable'));
    await act(async () => { fireEvent.press(screen.getByTestId('for-you-profile-save')); });
    expect(mockPatchProfile).not.toHaveBeenCalled();
  });

  it('treats "not sure" as a real answer', async () => {
    await open();
    fireEvent.press(screen.getByTestId('usual-feel-not_sure'));
    fireEvent.press(screen.getByTestId('sensitivity-not_sure'));
    await act(async () => { fireEvent.press(screen.getByTestId('for-you-profile-save')); });
    await waitFor(() => expect(mockPatchProfile).toHaveBeenCalledWith([
      { key: 'care_skin_usual_feel', value: 'not_sure' },
      { key: 'care_skin_sensitivity', value: 'not_sure' },
    ]));
  });

  it('returns to the scanner so the decision can refresh', async () => {
    await open();
    fireEvent.press(screen.getByTestId('usual-feel-often_oily'));
    fireEvent.press(screen.getByTestId('sensitivity-often_reactive'));
    await act(async () => { fireEvent.press(screen.getByTestId('for-you-profile-save')); });
    await waitFor(() => expect(mockBack).toHaveBeenCalled());
  });
});

describe('loading existing answers', () => {
  it('preselects a stored value', async () => {
    await open({
      ...emptyProfile,
      attributes: [
        { key: 'care_skin_usual_feel', value: 'often_dry_or_tight' },
        { key: 'care_skin_sensitivity', value: 'rarely_reactive' },
      ] as never,
    });
    expect(screen.getByTestId('usual-feel-often_dry_or_tight').props.accessibilityState.selected).toBe(true);
    expect(screen.getByTestId('sensitivity-rarely_reactive').props.accessibilityState.selected).toBe(true);
  });

  it('ignores a stored value outside the vocabulary', async () => {
    await open({
      ...emptyProfile,
      attributes: [{ key: 'care_skin_usual_feel', value: 'something_invented' }] as never,
    });
    SKIN_USUAL_FEEL_OPTIONS.forEach((option: { value: string }) => {
      expect(screen.getByTestId(`usual-feel-${option.value}`).props.accessibilityState.selected).toBe(false);
    });
  });
});

describe('the screen collects no health free text', () => {
  it('mentions no medication, diagnosis or symptom field', () => {
    const source = fs.readFileSync(
      path.join(path.resolve(__dirname, '..', '..'), 'app/for-you-profile.tsx'), 'utf8',
    );
    for (const banned of ['medication', 'diagnosis', 'symptom', 'TextInput']) {
      expect(source).not.toContain(banned);
    }
  });
});
