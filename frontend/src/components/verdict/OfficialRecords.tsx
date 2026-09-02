import React from 'react';
import { Linking, StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import type { VerdictSource } from '../../services/verdictModel';
import { S } from '../../strings/verdict';
import { COLORS, FONTS, RADIUS, SPACING } from '../../theme/colors';

export function OfficialRecords({ officialRecords }: { officialRecords: VerdictSource['officialRecords'] }) {
  const records = officialRecords?.records ?? [];
  if (records.length === 0) return null;
  return (
    <View style={styles.container}>
      {records.map((record) => {
        const status = typeof record.recall_status === 'string' ? record.recall_status : null;
        const reason = typeof record.reason === 'string' ? record.reason : null;
        const started = typeof record.recall_start_date === 'string' ? record.recall_start_date : null;
        const ended = typeof record.recall_termination_date === 'string' ? record.recall_termination_date : null;
        const nature = typeof record.nature_of_recall === 'string' ? record.nature_of_recall : null;
        return (
          <View key={record.recall_id} style={styles.card} accessible accessibilityLabel={`${S.officialRecords.title}. ${S.officialRecords.recallId} ${record.recall_id}. ${S.officialRecords.status} ${status ?? S.officialRecords.statusUnavailable}`}>
            <Text style={styles.title}>{S.officialRecords.title}</Text>
            <Text style={styles.detail}>{S.officialRecords.recallId}: {record.recall_id}</Text>
            {!!status && <Text style={styles.detail}>{S.officialRecords.status}: {status}</Text>}
            {!!started && <Text style={styles.detail}>{S.officialRecords.startDate}: {started}</Text>}
            {!!ended && <Text style={styles.detail}>{S.officialRecords.terminationDate}: {ended}</Text>}
            {!!reason && <Text style={styles.detail}>{S.officialRecords.reason}: {reason}</Text>}
            {!!nature && <Text style={styles.detail}>{S.officialRecords.nature}: {nature}</Text>}
            {!!officialRecords?.last_successful_check_at && <Text style={styles.context}>{S.officialRecords.checked}: {officialRecords.last_successful_check_at.slice(0, 10)}</Text>}
            <TouchableOpacity accessibilityRole="link" accessibilityLabel={S.officialRecords.openSource} onPress={() => void Linking.openURL(record.source_url)}>
              <Text style={styles.link}>{S.officialRecords.openSource}</Text>
            </TouchableOpacity>
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginTop: SPACING.md },
  card: { backgroundColor: COLORS.card, borderColor: COLORS.border, borderRadius: RADIUS.md, borderWidth: 1, padding: SPACING.md },
  title: { color: COLORS.textPrimary, fontFamily: FONTS.family.heading, fontSize: 17, marginBottom: SPACING.xs },
  detail: { color: COLORS.textSecondary, fontFamily: FONTS.family.body, fontSize: 14, lineHeight: 20 },
  context: { color: COLORS.textMuted, fontFamily: FONTS.family.body, fontSize: 12, marginTop: SPACING.sm },
  link: { color: COLORS.primary, fontFamily: FONTS.family.body, fontSize: 14, marginTop: SPACING.sm },
});
