import React from 'react';
import { Linking, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { COLORS, FONTS, SPACING } from '../../theme/colors';

export const ODBL_ATTRIBUTION_TEXT = 'Contains information from Open Food Facts, made available under the Open Database License (ODbL)';
export const ODBL_LICENSE_URL = 'https://opendatacommons.org/licenses/odbl/1-0/';
export const OFF_SOURCE_URL = 'https://world.openfoodfacts.org/';

export function OpenFoodFactsAttribution() {
  return <View style={styles.container} accessibilityLabel="Open Food Facts attribution">
    <Text style={styles.text}>{ODBL_ATTRIBUTION_TEXT}</Text>
    <View style={styles.links}>
      <TouchableOpacity accessibilityRole="link" accessibilityLabel="Open Food Facts" onPress={() => void Linking.openURL(OFF_SOURCE_URL)}><Text style={styles.link}>Open Food Facts</Text></TouchableOpacity>
      <TouchableOpacity accessibilityRole="link" accessibilityLabel="Open Database License" onPress={() => void Linking.openURL(ODBL_LICENSE_URL)}><Text style={styles.link}>Licence</Text></TouchableOpacity>
    </View>
  </View>;
}
const styles = StyleSheet.create({ container: { marginTop: SPACING.md, paddingVertical: SPACING.sm }, text: { color: COLORS.textMuted, fontFamily: FONTS.family.body, fontSize: 11, lineHeight: 16 }, links: { flexDirection: 'row', gap: SPACING.md, marginTop: 4 }, link: { color: COLORS.primary, fontFamily: FONTS.family.bodySemibold, fontSize: 11 } });
