/**
 * Step 13 — read a supplement's label from a photo, into drafts.
 *
 * One small action inside the existing label editor. The person photographs
 * (or picks) the label, the photo goes through the ordinary media upload, and
 * the server transcribes what is printed onto this supplement as unconfirmed
 * details. Nothing here confirms anything: every row comes back "Read from your
 * photo · not confirmed yet" and drives nothing until the person confirms it.
 *
 * Three deliberate behaviours:
 * - The camera permission is asked for only when the person chooses the
 *   camera, never on mount, and no other permission is requested.
 * - A retry after a failure reuses the same uploaded photo and the same
 *   request id, so the server replays instead of creating a second set.
 * - A photo with no readable label detail is not a success: nothing was added,
 *   the photo and request id are kept, and "try again" reads it again.
 */
import React, { useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import * as ImagePicker from 'expo-image-picker';

import { isNoLabelDetails, transcribeSupplementLabelPhoto, uploadMedia } from '../../services/apiV2';
import { S } from '../../strings/supplements';
import { COLORS, FONTS, RADIUS } from '../../theme/colors';

const nextRequestId = () => `photo-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;

type Pending = { mediaId: string; requestId: string };

export function SupplementPhotoReader({ itemId, onRead }: {
  itemId: string;
  /** Called after drafts were written (or replayed), so the screen can refresh. */
  onRead: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [pending, setPending] = useState<Pending | null>(null);

  const transcribe = async (attempt: Pending) => {
    setBusy(true); setMessage('');
    try {
      await transcribeSupplementLabelPhoto(itemId, attempt.mediaId, attempt.requestId);
      setPending(null);
      setMessage(S.photo.done);
      onRead();
    } catch (err) {
      // Keep the photo and the request id: "try again" must replay, not duplicate.
      setPending(attempt);
      setMessage(isNoLabelDetails(err) ? S.photo.empty : S.photo.failed);
    } finally {
      setBusy(false);
    }
  };

  const capture = async (source: 'camera' | 'library') => {
    setMessage('');
    if (source === 'camera') {
      const permission = await ImagePicker.requestCameraPermissionsAsync();
      if (!permission.granted) { setMessage(S.photo.cameraPermission); return; }
    }
    const picked = source === 'camera'
      ? await ImagePicker.launchCameraAsync({ quality: 0.75, mediaTypes: ['images'] })
      : await ImagePicker.launchImageLibraryAsync({ quality: 0.75, mediaTypes: ['images'] });
    if (picked.canceled || !picked.assets?.length) return;
    const asset = picked.assets[0];
    setBusy(true);
    let uploaded: { id: string };
    try {
      uploaded = await uploadMedia({
        uri: asset.uri, name: asset.fileName || `supplement-label-${Date.now()}.jpg`, type: asset.mimeType || 'image/jpeg',
      });
    } catch {
      setBusy(false); setMessage(S.photo.failed); return;
    }
    setOpen(false);
    await transcribe({ mediaId: uploaded.id, requestId: nextRequestId() });
  };

  return (
    <View testID="supplement-photo-reader" style={styles.wrap}>
      {!open ? (
        <TouchableOpacity accessibilityRole="button" accessibilityLabel={S.photo.action} disabled={busy} onPress={() => setOpen(true)} style={styles.button}>
          <Text style={styles.buttonText}>{busy ? S.photo.busy : S.photo.action}</Text>
        </TouchableOpacity>
      ) : (
        <View style={styles.choices}>
          <Text style={styles.note}>{S.photo.explain}</Text>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel={S.photo.camera} disabled={busy} onPress={() => void capture('camera')} style={styles.button}>
            <Text style={styles.buttonText}>{S.photo.camera}</Text>
          </TouchableOpacity>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel={S.photo.library} disabled={busy} onPress={() => void capture('library')} style={styles.button}>
            <Text style={styles.buttonText}>{S.photo.library}</Text>
          </TouchableOpacity>
          <TouchableOpacity accessibilityRole="button" accessibilityLabel={S.photo.cancel} disabled={busy} onPress={() => setOpen(false)}>
            <Text style={styles.link}>{S.photo.cancel}</Text>
          </TouchableOpacity>
        </View>
      )}
      {busy && <ActivityIndicator color={COLORS.primary} accessibilityLabel={S.photo.busy} />}
      {!!message && <Text accessibilityRole="alert" style={styles.note}>{message}</Text>}
      {!!pending && !busy && (
        <TouchableOpacity accessibilityRole="button" accessibilityLabel={S.photo.retry} onPress={() => void transcribe(pending)}>
          <Text style={styles.link}>{S.photo.retry}</Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { marginTop: 12 },
  choices: { gap: 8 },
  button: { backgroundColor: COLORS.primaryLight, borderRadius: RADIUS.full, alignItems: 'center', padding: 12 },
  buttonText: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary },
  link: { fontFamily: FONTS.family.bodySemibold, color: COLORS.primary, fontSize: 12, marginTop: 8, textAlign: 'center' },
  note: { fontFamily: FONTS.family.body, color: COLORS.textMuted, fontSize: 11, lineHeight: 16, marginTop: 6 },
});

export default SupplementPhotoReader;
