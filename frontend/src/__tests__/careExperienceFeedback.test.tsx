import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import {
  CARE_EXPERIENCE_PRODUCT_CATEGORIES,
  CareExperienceFeedbackSheet,
  canRecordCareExperienceForCategory,
} from '../components/routines/CareExperienceFeedback';
import {
  api,
} from '../services/api';
import {
  deleteCareExperienceFeedback,
  listCareExperienceFeedback,
  recordCareExperienceFeedback,
} from '../services/apiV2';
import * as apiV2 from '../services/apiV2';

const entry = {
  id: 'feedback-1',
  feedback_version: 'v3-03.13' as const,
  subject_type: 'product' as const,
  subject_id: 'product-1',
  routine_kind: null,
  routine_slot: null,
  dimension: 'comfort' as const,
  sentiment: 'negative' as const,
  note: '  Felt heavier later  ',
  experienced_on: '2026-08-15',
  created_at: '2026-08-15T12:00:00Z',
};

describe('Care experience feedback API contract', () => {
  afterEach(() => jest.restoreAllMocks());

  it('posts only the explicit subject and selected experience fields', async () => {
    const post = jest.spyOn(api, 'post').mockResolvedValue({ data: { ...entry, affects_recommendations: false, creates_memory: false, changes_care_safety: false, message: 'Saved.' } } as any);
    await recordCareExperienceFeedback({ subject_type: 'product', subject_id: 'product-1', dimension: 'comfort', sentiment: 'negative', note: '  Felt heavier later  ' });
    expect(post).toHaveBeenCalledWith('/api/v2/routines/experience-feedback', {
      subject_type: 'product', subject_id: 'product-1', dimension: 'comfort', sentiment: 'negative', note: '  Felt heavier later  ',
    });
    expect(post.mock.calls[0][1]).not.toHaveProperty('account_id');
    expect(post.mock.calls[0][1]).not.toHaveProperty('experienced_on');
  });

  it('supports subject-scoped history and deletes only the selected feedback id', async () => {
    const get = jest.spyOn(api, 'get').mockResolvedValue({ data: { feedback: [entry] } } as any);
    const del = jest.spyOn(api, 'delete').mockResolvedValue({ data: { deleted: true, id: entry.id } } as any);
    await listCareExperienceFeedback('routine_step', 'step-1', 12);
    await deleteCareExperienceFeedback(entry.id);
    expect(get).toHaveBeenCalledWith('/api/v2/routines/experience-feedback', { params: { subject_type: 'routine_step', subject_id: 'step-1', limit: 12 } });
    expect(del).toHaveBeenCalledWith('/api/v2/routines/experience-feedback/feedback-1');
    expect(del.mock.calls[0]).toHaveLength(1);
  });
});

describe('CareExperienceFeedbackSheet', () => {
  const postResult = { ...entry, affects_recommendations: false as const, creates_memory: false as const, changes_care_safety: false as const, message: 'Backend response copy' };

  beforeEach(() => {
    jest.spyOn(api, 'get').mockResolvedValue({ data: { feedback: [] } } as any);
    jest.spyOn(api, 'post').mockResolvedValue({ data: postResult } as any);
    jest.spyOn(api, 'delete').mockResolvedValue({ data: { deleted: true, id: entry.id } } as any);
  });
  afterEach(() => jest.restoreAllMocks());

  it('keeps Save disabled until both a dimension and sentiment are selected', () => {
    render(<CareExperienceFeedbackSheet open subjectType="product" subjectId="product-1" subjectLabel="Cloud cleanser" onClose={jest.fn()} />);
    const save = screen.getByLabelText('Save experience');
    expect(save.props.accessibilityState.disabled).toBe(true);
    fireEvent.press(screen.getByLabelText('Comfort'));
    expect(save.props.accessibilityState.disabled).toBe(true);
    fireEvent.press(screen.getByLabelText('Negative'));
    expect(save.props.accessibilityState.disabled).toBe(false);
  });

  it('offers exactly the frozen four dimensions and three sentiments', () => {
    render(<CareExperienceFeedbackSheet open subjectType="product" subjectId="product-1" subjectLabel="Cloud cleanser" onClose={jest.fn()} />);
    expect(screen.getAllByRole('radio')).toHaveLength(7);
    expect(screen.getByLabelText('Overall experience')).toBeTruthy();
    expect(screen.getByLabelText('Comfort')).toBeTruthy();
    expect(screen.getByLabelText('Ease of use')).toBeTruthy();
    expect(screen.getByLabelText('Routine fit')).toBeTruthy();
    expect(screen.getByLabelText('Positive')).toBeTruthy();
    expect(screen.getByLabelText('Neutral')).toBeTruthy();
    expect(screen.getByLabelText('Negative')).toBeTruthy();
  });

  it('sends notes verbatim, omits a client date, and shows the non-adaptation confirmation and history', async () => {
    const post = jest.spyOn(api, 'post').mockResolvedValue({ data: postResult } as any);
    jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: { feedback: [] } } as any)
      .mockResolvedValueOnce({ data: { feedback: [entry] } } as any);
    render(<CareExperienceFeedbackSheet open subjectType="product" subjectId="product-1" subjectLabel="Cloud cleanser" onClose={jest.fn()} />);
    fireEvent.press(screen.getByLabelText('Comfort'));
    fireEvent.press(screen.getByLabelText('Negative'));
    fireEvent.changeText(screen.getByLabelText('Experience note'), '  Felt heavier later  ');
    fireEvent.press(screen.getByLabelText('Save experience'));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0][1]).toEqual({ subject_type: 'product', subject_id: 'product-1', dimension: 'comfort', sentiment: 'negative', note: '  Felt heavier later  ' });
    expect(screen.getByText('Saved. This does not change your routine automatically.')).toBeTruthy();
    expect(screen.queryByText('Backend response copy')).toBeNull();
    expect(await screen.findByText('Comfort · Negative')).toBeTruthy();
    expect(screen.getByText('  Felt heavier later  ')).toBeTruthy();
    expect(screen.getByText('2026-08-15')).toBeTruthy();
  });

  it('retains the selected values and note when saving fails', async () => {
    jest.spyOn(api, 'post').mockRejectedValue({ response: { data: { detail: 'Could not save' } } });
    render(<CareExperienceFeedbackSheet open subjectType="product" subjectId="product-1" subjectLabel="Cloud cleanser" onClose={jest.fn()} />);
    fireEvent.press(screen.getByLabelText('Comfort'));
    fireEvent.press(screen.getByLabelText('Negative'));
    fireEvent.changeText(screen.getByLabelText('Experience note'), 'Keep this exactly');
    fireEvent.press(screen.getByLabelText('Save experience'));
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
    expect(screen.getByLabelText('Experience note').props.value).toBe('Keep this exactly');
    expect(screen.getByLabelText('Comfort').props.accessibilityState.selected).toBe(true);
    expect(screen.getByLabelText('Negative').props.accessibilityState.selected).toBe(true);
  });

  it('distinguishes history retrieval failure from an empty history', async () => {
    jest.spyOn(api, 'get').mockRejectedValue(new Error('offline'));
    render(<CareExperienceFeedbackSheet open subjectType="routine_step" subjectId="step-1" subjectLabel="Morning · Cleanser" onClose={jest.fn()} />);
    expect(await screen.findByText('We could not retrieve previous entries.')).toBeTruthy();
    expect(screen.queryByText(/No previous entries/)).toBeNull();
  });

  it('deletes one entry and refreshes only that subject history', async () => {
    const del = jest.spyOn(api, 'delete').mockResolvedValue({ data: { deleted: true, id: entry.id } } as any);
    jest.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: { feedback: [entry] } } as any)
      .mockResolvedValueOnce({ data: { feedback: [] } } as any);
    render(<CareExperienceFeedbackSheet open subjectType="product" subjectId="product-1" subjectLabel="Cloud cleanser" onClose={jest.fn()} />);
    expect(await screen.findByText('Comfort · Negative')).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Delete experience entry from 2026-08-15'));
    await waitFor(() => expect(del).toHaveBeenCalledWith('/api/v2/routines/experience-feedback/feedback-1'));
    expect(screen.queryByText('Comfort · Negative')).toBeNull();
  });
  it('keeps experience capture, history, and deletion separate from existing adaptation and control actions', async () => {
    const generateRoutines = jest.spyOn(apiV2, 'generateRoutines').mockResolvedValue({} as any);
    const regenerateToday = jest.spyOn(apiV2, 'regenerateToday').mockResolvedValue({} as any);
    const completeRoutineStep = jest.spyOn(apiV2, 'completeRoutineStep').mockResolvedValue({} as any);
    const completePlanAction = jest.spyOn(apiV2, 'completePlanAction').mockResolvedValue({} as any);
    const sendMemoryFeedback = jest.spyOn(apiV2, 'sendMemoryFeedback').mockResolvedValue({} as any);
    const post = jest.spyOn(api, 'post').mockResolvedValue({ data: postResult } as any);
    jest.spyOn(api, 'get').mockResolvedValue({ data: { feedback: [entry] } } as any);
    const del = jest.spyOn(api, 'delete').mockResolvedValue({ data: { deleted: true, id: entry.id } } as any);

    render(<CareExperienceFeedbackSheet open subjectType="product" subjectId="product-1" subjectLabel="Cloud cleanser" onClose={jest.fn()} />);
    fireEvent.press(screen.getByLabelText('Comfort'));
    fireEvent.press(screen.getByLabelText('Negative'));
    fireEvent.press(screen.getByLabelText('Save experience'));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));

    fireEvent.press(await screen.findByLabelText('Delete experience entry from 2026-08-15'));
    await waitFor(() => expect(del).toHaveBeenCalledTimes(1));

    expect(post.mock.calls[0][0]).toBe('/api/v2/routines/experience-feedback');
    expect(generateRoutines).not.toHaveBeenCalled();
    expect(regenerateToday).not.toHaveBeenCalled();
    expect(completeRoutineStep).not.toHaveBeenCalled();
    expect(completePlanAction).not.toHaveBeenCalled();
    expect(sendMemoryFeedback).not.toHaveBeenCalled();
  });
});

describe('Care product gating', () => {
  it('allows Skin Care and Hair, and no other inventory category', () => {
    expect(CARE_EXPERIENCE_PRODUCT_CATEGORIES).toEqual(['beauty', 'hair']);
    expect(canRecordCareExperienceForCategory('beauty')).toBe(true);
    expect(canRecordCareExperienceForCategory('hair')).toBe(true);
    for (const category of ['wardrobe', 'shoes', 'accessories', 'perfume', 'perfumes', 'supplements']) {
      expect(canRecordCareExperienceForCategory(category)).toBe(false);
    }
  });
});
