/**
 * Step 13 — reading a supplement label from a photo.
 *
 * The action only ever produces unconfirmed drafts: it asks for the camera
 * only when the camera is chosen, never confirms anything, and a retry after a
 * failure replays the same photo and request instead of creating a second set.
 * A photo with nothing readable is not a success: nothing is added and the
 * same photo can be read again.
 */
import React from 'react';
import * as fs from 'fs';
import * as path from 'path';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import { SupplementPhotoReader } from '../components/inventory/SupplementPhotoReader';
import { SupplementDetailSection } from '../components/inventory/SupplementDetail';
import type { SupplementDetail } from '../services/apiV2';
import { S } from '../strings/supplements';

jest.mock('expo-image-picker', () => ({
  requestCameraPermissionsAsync: jest.fn(async () => ({ granted: true })),
  launchCameraAsync: jest.fn(async () => ({ canceled: false, assets: [{ uri: 'file:///label.jpg', mimeType: 'image/jpeg' }] })),
  launchImageLibraryAsync: jest.fn(async () => ({ canceled: false, assets: [{ uri: 'file:///label.png', mimeType: 'image/png' }] })),
}));

const mockUpload = jest.fn();
const mockTranscribe = jest.fn();
jest.mock('../services/apiV2', () => ({
  isNoLabelDetails: jest.requireActual('../services/apiV2').isNoLabelDetails,
  uploadMedia: (...args: unknown[]) => mockUpload(...args),
  transcribeSupplementLabelPhoto: (...args: unknown[]) => mockTranscribe(...args),
}));

const noLabelDetails = () => Object.assign(new Error('422'), {
  response: { status: 422, data: { detail: {
    code: 'VALIDATION_FAILED', message: 'server copy', retryable: true, reason: 'no_label_details', field: 'media_asset_id',
  } } },
});

const picker = jest.requireMock('expo-image-picker') as {
  requestCameraPermissionsAsync: jest.Mock; launchCameraAsync: jest.Mock; launchImageLibraryAsync: jest.Mock;
};

const draft = {
  id: 'fact-1', inventory_item_id: 'item-1', raw_name: 'Magnesium oxide', normalized_name: 'magnesium',
  canonical_component_key: 'magnesium', amount: '500', unit: 'mg', serving_text: 'Each tablet contains',
  source: 'photo_extracted', verification_state: 'draft', confidence: 0.8, schema_version: 'supplement-label.v1',
};

describe('Step 13 supplement photo reader', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockUpload.mockResolvedValue({ id: 'media-1' });
    mockTranscribe.mockResolvedValue({ status: 'created', label_facts: [draft] });
  });

  it('asks for no permission until the person chooses the camera', async () => {
    const onRead = jest.fn();
    render(<SupplementPhotoReader itemId="item-1" onRead={onRead} />);
    expect(picker.requestCameraPermissionsAsync).not.toHaveBeenCalled();
    fireEvent.press(screen.getByRole('button', { name: S.photo.action }));
    expect(picker.requestCameraPermissionsAsync).not.toHaveBeenCalled();
    await act(async () => { fireEvent.press(screen.getByRole('button', { name: S.photo.library })); });
    expect(picker.requestCameraPermissionsAsync).not.toHaveBeenCalled();
    expect(picker.launchImageLibraryAsync).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(onRead).toHaveBeenCalledTimes(1));
    expect(mockTranscribe).toHaveBeenCalledWith('item-1', 'media-1', expect.stringMatching(/^photo-[a-z0-9-]+$/));
    expect(screen.getByText(S.photo.done)).toBeTruthy();
  });

  it('asks for the camera only when the camera is chosen, and stops if it is refused', async () => {
    picker.requestCameraPermissionsAsync.mockResolvedValueOnce({ granted: false });
    render(<SupplementPhotoReader itemId="item-1" onRead={jest.fn()} />);
    fireEvent.press(screen.getByRole('button', { name: S.photo.action }));
    await act(async () => { fireEvent.press(screen.getByRole('button', { name: S.photo.camera })); });
    expect(picker.requestCameraPermissionsAsync).toHaveBeenCalledTimes(1);
    expect(picker.launchCameraAsync).not.toHaveBeenCalled();
    expect(mockUpload).not.toHaveBeenCalled();
    expect(screen.getByText(S.photo.cameraPermission)).toBeTruthy();
  });

  it('retries the same photo and request after a failure, instead of creating a second set', async () => {
    mockTranscribe.mockRejectedValueOnce(new Error('503'));
    const onRead = jest.fn();
    render(<SupplementPhotoReader itemId="item-1" onRead={onRead} />);
    fireEvent.press(screen.getByRole('button', { name: S.photo.action }));
    await act(async () => { fireEvent.press(screen.getByRole('button', { name: S.photo.library })); });
    await waitFor(() => expect(screen.getByText(S.photo.failed)).toBeTruthy());
    expect(onRead).not.toHaveBeenCalled();
    const firstCall = mockTranscribe.mock.calls[0];
    await act(async () => { fireEvent.press(screen.getByRole('button', { name: S.photo.retry })); });
    await waitFor(() => expect(onRead).toHaveBeenCalledTimes(1));
    expect(mockUpload).toHaveBeenCalledTimes(1);
    expect(mockTranscribe.mock.calls[1]).toEqual(firstCall);
  });

  it('treats a photo with nothing readable as not done, and reads the same photo again on retry', async () => {
    mockTranscribe.mockRejectedValueOnce(noLabelDetails());
    const onRead = jest.fn();
    render(<SupplementPhotoReader itemId="item-1" onRead={onRead} />);
    fireEvent.press(screen.getByRole('button', { name: S.photo.action }));
    await act(async () => { fireEvent.press(screen.getByRole('button', { name: S.photo.library })); });
    await waitFor(() => expect(screen.getByText(S.photo.empty)).toBeTruthy());
    expect(screen.queryByText('server copy')).toBeNull();
    expect(screen.queryByText(S.photo.done)).toBeNull();
    expect(onRead).not.toHaveBeenCalled();
    const firstCall = mockTranscribe.mock.calls[0];
    await act(async () => { fireEvent.press(screen.getByRole('button', { name: S.photo.retry })); });
    await waitFor(() => expect(onRead).toHaveBeenCalledTimes(1));
    expect(mockUpload).toHaveBeenCalledTimes(1);
    expect(mockTranscribe.mock.calls[1]).toEqual(firstCall);
  });

  it('keeps every word of the photo action and the detail in the keyed string file', () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const ts = require('typescript') as typeof import('typescript');
    const PROSE_PROPS = new Set(['accessibilityLabel', 'accessibilityHint', 'title', 'placeholder', 'label']);
    const readsAsProse = (value: string) => {
      const text = value.trim();
      return text.split(/\s+/).length >= 2 && (/^[A-Z]/.test(text) || /[.!?]$/.test(text));
    };
    for (const file of ['SupplementPhotoReader.tsx', 'SupplementDetail.tsx']) {
      const fullPath = path.join(__dirname, '..', 'components', 'inventory', file);
      const source = ts.createSourceFile(file, fs.readFileSync(fullPath, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
      const stray: string[] = [];
      const visit = (node: import('typescript').Node) => {
        // Words in JSX text; separators such as ":" or "·" carry none.
        if (ts.isJsxText(node) && /[A-Za-z]/.test(node.getText())) stray.push(node.getText().trim());
        if (ts.isJsxAttribute(node) && node.initializer && ts.isStringLiteral(node.initializer)
            && PROSE_PROPS.has(node.name.getText())) stray.push(node.initializer.text);
        if ((ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
            && !ts.isImportDeclaration(node.parent) && readsAsProse(node.text)) stray.push(node.text);
        ts.forEachChild(node, visit);
      };
      visit(source);
      expect({ file, stray }).toEqual({ file, stray: [] });
    }
  });

  it('shows read-from-photo details as not confirmed and driving nothing', () => {
    const detail: SupplementDetail = {
      contract_version: 'step-13-v1',
      item: { inventory_item_id: 'item-1', display_name: 'Night magnesium', brand: null, user_entered_purpose: null, provenance: 'you_entered', confirmed: true },
      expiry: { state: 'unknown', date: null, days_to_expiry: null },
      components: [{
        id: 'fact-1', printed: { name: 'Magnesium oxide', amount: '500', unit: 'mg', serving_text: 'Each tablet contains' },
        provenance: 'read_from_photo_not_confirmed', confirmed: false, counts_for_overlap: false, missing_information: [],
        nutrient: { status: 'awaiting_confirmation' }, form: { status: 'awaiting_confirmation' },
        package_chemistry: { status: 'awaiting_confirmation' }, published_knowledge: { status: 'awaiting_confirmation' },
      }],
      overlaps: [], missing_information: ['confirmation', 'expiry_date'],
      professional_boundary: { boundary: false, reason: null, message: null }, fingerprint: 'f',
    };
    render(<SupplementDetailSection detail={detail} onConfirm={jest.fn()} />);
    expect(screen.getByText(S.provenance.read_from_photo_not_confirmed)).toBeTruthy();
    expect(screen.getByText(S.awaitingConfirmation)).toBeTruthy();
    expect(screen.queryByTestId('supplement-chemistry')).toBeNull();
    expect(screen.queryByTestId('supplement-overlaps')).toBeNull();
  });

  it('never calls a photo reading scanned, verified, official or manufacturer data', () => {
    const words = JSON.stringify(S.photo) + JSON.stringify(S.provenance);
    for (const banned of [/scanned/i, /verified/i, /official/i, /manufacturer/i, /\bscan\b/i]) {
      expect(words).not.toMatch(banned);
    }
  });

  it('lives inside the existing item screen, not a new screen', () => {
    const screenSource = fs.readFileSync(path.join(__dirname, '..', '..', 'app', 'inventory-item.tsx'), 'utf8');
    expect(screenSource).toContain('<SupplementPhotoReader itemId={item.id}');
    expect(fs.existsSync(path.join(__dirname, '..', '..', 'app', 'supplement-photo.tsx'))).toBe(false);
  });
});
