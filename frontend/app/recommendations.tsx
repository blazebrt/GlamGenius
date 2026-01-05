import React from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
} from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeIn, FadeInDown } from 'react-native-reanimated';
import { useCartStore } from '../src/store/cartStore';
import { COLORS, FONTS, SPACING, RADIUS } from '../src/theme/colors';

const ADDON_PRODUCTS = [
  { id: 'p1', name: 'Hair Serum (50ml)', price: 299, type: 'product' as const },
  { id: 'p2', name: 'Face Moisturizer', price: 399, type: 'product' as const },
  { id: 'p3', name: 'Sunscreen SPF 50', price: 349, type: 'product' as const },
];

const ADDON_SERVICES = [
  { id: 'a1', name: 'Head Massage', price: 199, duration: 15, type: 'addon' as const },
  { id: 'a2', name: 'Hand Massage', price: 149, duration: 10, type: 'addon' as const },
  { id: 'a3', name: 'Deep Conditioning', price: 299, duration: 20, type: 'addon' as const },
];

export default function RecommendationsScreen() {
  const router = useRouter();
  const params = useLocalSearchParams();
  const insets = useSafeAreaInsets();
  const { addItem, getItemCount, items, removeItem } = useCartStore();

  const recommendations = params.recommendations ? JSON.parse(params.recommendations as string) : null;

  const isInCart = (itemId: string) => items.some(item => item.id === itemId);
  const isServiceInCart = (serviceName: string) => items.some(item => item.name === serviceName);

  const handleToggleCartItem = (item: any) => {
    if (isInCart(item.id)) removeItem(item.id);
    else addItem(item);
  };

  const handleToggleServiceCart = (service: any) => {
    const existingItem = items.find(item => item.name === service.name);
    if (existingItem) removeItem(existingItem.id);
    else {
      const priceMatch = service.name?.match(/\d+/) || ['999'];
      addItem({ id: `service-${Date.now()}`, type: 'service', name: service.name, price: parseInt(priceMatch[0]) || 999, duration: 45 });
    }
  };

  const cartCount = getItemCount();

  if (!recommendations) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.emptyContainer}>
          <Ionicons name="alert-circle-outline" size={48} color={COLORS.border} />
          <Text style={styles.emptyText}>No recommendations available</Text>
          <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
            <Text style={styles.backBtnText}>Go Back</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  const services = recommendations.services || recommendations.top_recommendations || [];
  const aftercare = recommendations.aftercare_tips || [];
  const totalCost = recommendations.estimated_total_cost || services.reduce((acc: number, s: any) => acc + (s.price || 0), 0);

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.headerBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={22} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Your Package</Text>
        <TouchableOpacity style={styles.headerBtn} onPress={() => router.push('/cart')}>
          <Ionicons name="cart-outline" size={22} color={COLORS.textPrimary} />
          {cartCount > 0 && (
            <View style={styles.cartBadge}>
              <Text style={styles.cartBadgeText}>{cartCount}</Text>
            </View>
          )}
        </TouchableOpacity>
      </View>

      <ScrollView style={styles.content} showsVerticalScrollIndicator={false}>
        {/* Summary Card */}
        <Animated.View entering={FadeIn} style={styles.summaryCard}>
          <Text style={styles.summaryLabel}>ESTIMATED COST</Text>
          <Text style={styles.summaryValue}>₹{totalCost.toLocaleString('en-IN')}</Text>
          <View style={styles.summaryMeta}>
            <View style={styles.summaryMetaItem}>
              <Ionicons name="time-outline" size={16} color={COLORS.textMuted} />
              <Text style={styles.summaryMetaText}>~{recommendations.estimated_duration || 90} min</Text>
            </View>
            <View style={styles.summaryMetaItem}>
              <Ionicons name="sparkles-outline" size={16} color={COLORS.textMuted} />
              <Text style={styles.summaryMetaText}>{services.length} services</Text>
            </View>
          </View>
        </Animated.View>

        {/* Recommended Services */}
        <Animated.View entering={FadeInDown.delay(100)} style={styles.section}>
          <View style={styles.sectionHeader}>
            <Ionicons name="star-outline" size={20} color={COLORS.black} />
            <Text style={styles.sectionTitle}>Recommended Services</Text>
          </View>
          {services.map((service: any, index: number) => {
            const inCart = isServiceInCart(service.name || service.service);
            return (
              <TouchableOpacity
                key={index}
                style={[styles.serviceCard, inCart && styles.serviceCardActive]}
                onPress={() => handleToggleServiceCart({ name: service.name || service.service })}
              >
                <View style={styles.serviceInfo}>
                  <Text style={styles.serviceName}>{service.name || service.service}</Text>
                  <Text style={styles.serviceReason}>{service.reason || service.why_recommended}</Text>
                </View>
                <View style={[styles.addBtn, inCart && styles.addBtnActive]}>
                  <Ionicons name={inCart ? 'checkmark' : 'add'} size={20} color={inCart ? COLORS.white : COLORS.black} />
                </View>
              </TouchableOpacity>
            );
          })}
        </Animated.View>

        {/* Add-on Services */}
        <Animated.View entering={FadeInDown.delay(200)} style={styles.section}>
          <View style={styles.sectionHeader}>
            <Ionicons name="add-circle-outline" size={20} color={COLORS.black} />
            <Text style={styles.sectionTitle}>Enhance Your Experience</Text>
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            {ADDON_SERVICES.map((addon) => {
              const inCart = isInCart(addon.id);
              return (
                <TouchableOpacity
                  key={addon.id}
                  style={[styles.addonCard, inCart && styles.addonCardActive]}
                  onPress={() => handleToggleCartItem({ ...addon, quantity: 1 })}
                >
                  <Text style={styles.addonName}>{addon.name}</Text>
                  <Text style={styles.addonDuration}>{addon.duration} min</Text>
                  <Text style={styles.addonPrice}>₹{addon.price}</Text>
                  <View style={[styles.addonCheck, inCart && styles.addonCheckActive]}>
                    <Ionicons name={inCart ? 'checkmark' : 'add'} size={14} color={inCart ? COLORS.white : COLORS.black} />
                  </View>
                </TouchableOpacity>
              );
            })}
          </ScrollView>
        </Animated.View>

        {/* Recommended Products */}
        <Animated.View entering={FadeInDown.delay(300)} style={styles.section}>
          <View style={styles.sectionHeader}>
            <Ionicons name="bag-outline" size={20} color={COLORS.black} />
            <Text style={styles.sectionTitle}>Recommended Products</Text>
          </View>
          <View style={styles.productsGrid}>
            {ADDON_PRODUCTS.map((product) => {
              const inCart = isInCart(product.id);
              return (
                <TouchableOpacity
                  key={product.id}
                  style={[styles.productCard, inCart && styles.productCardActive]}
                  onPress={() => handleToggleCartItem({ ...product, quantity: 1 })}
                >
                  <View style={styles.productIcon}>
                    <Ionicons name="gift-outline" size={20} color={COLORS.black} />
                  </View>
                  <Text style={styles.productName}>{product.name}</Text>
                  <Text style={styles.productPrice}>₹{product.price}</Text>
                  <View style={[styles.productBtn, inCart && styles.productBtnActive]}>
                    <Text style={[styles.productBtnText, inCart && styles.productBtnTextActive]}>
                      {inCart ? 'Added ✓' : 'Add'}
                    </Text>
                  </View>
                </TouchableOpacity>
              );
            })}
          </View>
        </Animated.View>

        {/* Aftercare Tips */}
        {aftercare.length > 0 && (
          <Animated.View entering={FadeInDown.delay(400)} style={styles.section}>
            <View style={styles.sectionHeader}>
              <Ionicons name="heart-outline" size={20} color={COLORS.black} />
              <Text style={styles.sectionTitle}>Aftercare Tips</Text>
            </View>
            <View style={styles.tipsCard}>
              {aftercare.slice(0, 3).map((tip: string, index: number) => (
                <View key={index} style={styles.tipItem}>
                  <View style={styles.tipDot} />
                  <Text style={styles.tipText}>{tip}</Text>
                </View>
              ))}
            </View>
          </Animated.View>
        )}

        <View style={{ height: 140 }} />
      </ScrollView>

      {/* Bottom Bar */}
      <View style={[styles.bottomBar, { paddingBottom: insets.bottom + 20 }]}>
        <View style={styles.bottomInfo}>
          <Text style={styles.bottomItems}>{cartCount} items in cart</Text>
        </View>
        <TouchableOpacity style={styles.bottomBtn} onPress={() => router.push('/cart')}>
          <Text style={styles.bottomBtnText}>View Cart</Text>
          <Ionicons name="arrow-forward" size={18} color={COLORS.white} />
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: SPACING.md, paddingVertical: SPACING.sm, borderBottomWidth: 1, borderBottomColor: COLORS.border },
  headerBtn: { width: 44, height: 44, borderRadius: 22, backgroundColor: COLORS.backgroundSecondary, justifyContent: 'center', alignItems: 'center' },
  headerTitle: { fontSize: FONTS.sizes.h3, fontFamily: FONTS.family.heading, color: COLORS.textPrimary },
  cartBadge: { position: 'absolute', top: -2, right: -2, backgroundColor: COLORS.black, width: 18, height: 18, borderRadius: 9, justifyContent: 'center', alignItems: 'center' },
  cartBadgeText: { fontSize: 10, fontFamily: FONTS.family.bodyBold, color: COLORS.white },
  content: { padding: SPACING.lg, paddingBottom: 140 },
  emptyContainer: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: SPACING.sm },
  emptyText: { fontSize: FONTS.sizes.body, fontFamily: FONTS.family.body, color: COLORS.textSecondary },
  backBtn: { backgroundColor: COLORS.black, paddingVertical: 12, paddingHorizontal: 24, borderRadius: RADIUS.full, marginTop: SPACING.sm },
  backBtnText: { fontSize: FONTS.sizes.body, fontFamily: FONTS.family.bodySemibold, color: COLORS.white },
  summaryCard: { backgroundColor: COLORS.backgroundSecondary, borderRadius: RADIUS.lg, padding: SPACING.xl, alignItems: 'center', marginBottom: SPACING.lg, borderWidth: 1, borderColor: COLORS.border },
  summaryLabel: { fontSize: FONTS.sizes.caption, fontFamily: FONTS.family.bodySemibold, color: COLORS.textMuted, letterSpacing: 1 },
  summaryValue: { fontSize: FONTS.sizes.display, fontFamily: FONTS.family.heading, color: COLORS.black, marginTop: 4 },
  summaryMeta: { flexDirection: 'row', gap: SPACING.lg, marginTop: SPACING.md },
  summaryMetaItem: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  summaryMetaText: { fontSize: FONTS.sizes.bodySm, fontFamily: FONTS.family.body, color: COLORS.textMuted },
  section: { marginBottom: SPACING.lg },
  sectionHeader: { flexDirection: 'row', alignItems: 'center', gap: SPACING.sm, marginBottom: SPACING.md },
  sectionTitle: { fontSize: FONTS.sizes.h4, fontFamily: FONTS.family.heading, color: COLORS.textPrimary },
  serviceCard: { flexDirection: 'row', alignItems: 'center', backgroundColor: COLORS.card, borderRadius: RADIUS.md, padding: SPACING.md, marginBottom: SPACING.sm, borderWidth: 1, borderColor: COLORS.border },
  serviceCardActive: { borderColor: COLORS.black, backgroundColor: COLORS.backgroundSecondary },
  serviceInfo: { flex: 1, marginRight: SPACING.sm },
  serviceName: { fontSize: FONTS.sizes.body, fontFamily: FONTS.family.bodySemibold, color: COLORS.textPrimary },
  serviceReason: { fontSize: FONTS.sizes.bodySm, fontFamily: FONTS.family.body, color: COLORS.textSecondary, marginTop: 4 },
  addBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: COLORS.black, justifyContent: 'center', alignItems: 'center' },
  addBtnActive: { backgroundColor: COLORS.accent },
  addonCard: { backgroundColor: COLORS.card, borderRadius: RADIUS.md, padding: SPACING.md, marginRight: SPACING.sm, borderWidth: 1, borderColor: COLORS.border, minWidth: 140 },
  addonCardActive: { borderColor: COLORS.black, backgroundColor: COLORS.backgroundSecondary },
  addonName: { fontSize: FONTS.sizes.bodySm, fontFamily: FONTS.family.bodyMedium, color: COLORS.textPrimary },
  addonDuration: { fontSize: FONTS.sizes.caption, fontFamily: FONTS.family.body, color: COLORS.textMuted, marginTop: 4 },
  addonPrice: { fontSize: FONTS.sizes.body, fontFamily: FONTS.family.bodySemibold, color: COLORS.black, marginTop: 4 },
  addonCheck: { width: 28, height: 28, borderRadius: 14, backgroundColor: COLORS.backgroundSecondary, justifyContent: 'center', alignItems: 'center', marginTop: SPACING.sm, borderWidth: 1, borderColor: COLORS.border },
  addonCheckActive: { backgroundColor: COLORS.black, borderColor: COLORS.black },
  productsGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: SPACING.sm },
  productCard: { width: '48%', backgroundColor: COLORS.card, borderRadius: RADIUS.md, padding: SPACING.md, borderWidth: 1, borderColor: COLORS.border },
  productCardActive: { borderColor: COLORS.black, backgroundColor: COLORS.backgroundSecondary },
  productIcon: { width: 44, height: 44, borderRadius: 22, backgroundColor: COLORS.backgroundSecondary, justifyContent: 'center', alignItems: 'center', marginBottom: SPACING.sm },
  productName: { fontSize: FONTS.sizes.bodySm, fontFamily: FONTS.family.bodyMedium, color: COLORS.textPrimary, height: 36 },
  productPrice: { fontSize: FONTS.sizes.body, fontFamily: FONTS.family.bodySemibold, color: COLORS.black, marginTop: 6 },
  productBtn: { backgroundColor: COLORS.backgroundSecondary, borderRadius: RADIUS.full, paddingVertical: 8, marginTop: SPACING.sm, borderWidth: 1, borderColor: COLORS.border },
  productBtnActive: { backgroundColor: COLORS.black, borderColor: COLORS.black },
  productBtnText: { fontSize: FONTS.sizes.bodySm, fontFamily: FONTS.family.bodySemibold, color: COLORS.textSecondary, textAlign: 'center' },
  productBtnTextActive: { color: COLORS.white },
  tipsCard: { backgroundColor: COLORS.backgroundSecondary, borderRadius: RADIUS.lg, padding: SPACING.md, borderWidth: 1, borderColor: COLORS.border },
  tipItem: { flexDirection: 'row', alignItems: 'flex-start', marginTop: SPACING.sm, gap: SPACING.sm },
  tipDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: COLORS.black, marginTop: 6 },
  tipText: { flex: 1, fontSize: FONTS.sizes.bodySm, fontFamily: FONTS.family.body, color: COLORS.textSecondary, lineHeight: 20 },
  bottomBar: { position: 'absolute', bottom: 0, left: 0, right: 0, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: COLORS.background, paddingHorizontal: SPACING.lg, paddingTop: SPACING.md, borderTopWidth: 1, borderTopColor: COLORS.border },
  bottomInfo: { flex: 1 },
  bottomItems: { fontSize: FONTS.sizes.body, fontFamily: FONTS.family.body, color: COLORS.textSecondary },
  bottomBtn: { flexDirection: 'row', alignItems: 'center', backgroundColor: COLORS.black, paddingVertical: 14, paddingHorizontal: 24, borderRadius: RADIUS.full, gap: SPACING.sm },
  bottomBtnText: { fontSize: FONTS.sizes.body, fontFamily: FONTS.family.bodySemibold, color: COLORS.white },
});
