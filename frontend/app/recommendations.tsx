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

const ADDON_PRODUCTS = [
  { id: 'p1', name: 'Hair Serum (50ml)', price: 299, type: 'product' as const },
  { id: 'p2', name: 'Face Moisturizer', price: 399, type: 'product' as const },
  { id: 'p3', name: 'Sunscreen SPF 50', price: 349, type: 'product' as const },
];

const ADDON_SERVICES = [
  { id: 'a1', name: 'Head Massage (15 min)', price: 199, duration: 15, type: 'addon' as const },
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
          <Ionicons name="alert-circle" size={48} color="#E2E8F0" />
          <Text style={styles.emptyText}>No recommendations available</Text>
          <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}><Text style={styles.backBtnText}>Go Back</Text></TouchableOpacity>
        </View>
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={styles.headerBtn}><Ionicons name="arrow-back" size={24} color="#1E293B" /></TouchableOpacity>
        <Text style={styles.headerTitle}>Your Recommendations</Text>
        <TouchableOpacity onPress={() => router.push('/cart')} style={styles.headerBtn}>
          <Ionicons name="cart" size={22} color="#0EA5E9" />
          {cartCount > 0 && <View style={styles.cartBadge}><Text style={styles.cartBadgeText}>{cartCount}</Text></View>}
        </TouchableOpacity>
      </View>

      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.content}>
        {/* Services */}
        {recommendations.services?.length > 0 && (
          <Animated.View entering={FadeInDown.delay(100)} style={styles.section}>
            <View style={styles.sectionHeader}>
              <Ionicons name="star" size={18} color="#0EA5E9" />
              <Text style={styles.sectionTitle}>Recommended Services</Text>
            </View>
            {recommendations.services.map((service: any, index: number) => {
              const inCart = isServiceInCart(service.name);
              return (
                <View key={index} style={styles.serviceCard}>
                  <View style={styles.serviceInfo}>
                    <Text style={styles.serviceName}>{service.name}</Text>
                    {service.reason && <Text style={styles.serviceReason}>{service.reason}</Text>}
                  </View>
                  <TouchableOpacity style={[styles.addBtn, inCart && styles.addBtnActive]} onPress={() => handleToggleServiceCart(service)}>
                    <Ionicons name={inCart ? 'checkmark' : 'add'} size={20} color="#FFFFFF" />
                  </TouchableOpacity>
                </View>
              );
            })}
          </Animated.View>
        )}

        {/* Stylist Level */}
        {recommendations.stylist_level && (
          <Animated.View entering={FadeInDown.delay(150)} style={styles.infoCard}>
            <Ionicons name="person" size={20} color="#8B5CF6" />
            <View style={styles.infoContent}>
              <Text style={styles.infoLabel}>Stylist Level</Text>
              <Text style={styles.infoValue}>{recommendations.stylist_level}</Text>
            </View>
          </Animated.View>
        )}

        {/* Cost */}
        {recommendations.estimated_cost && (
          <Animated.View entering={FadeInDown.delay(200)} style={styles.costCard}>
            <Text style={styles.costLabel}>Estimated Cost</Text>
            <Text style={styles.costValue}>₹{recommendations.estimated_cost}</Text>
          </Animated.View>
        )}

        {/* Add-on Services */}
        <Animated.View entering={FadeInDown.delay(250)} style={styles.section}>
          <View style={styles.sectionHeader}>
            <Ionicons name="add-circle" size={18} color="#10B981" />
            <Text style={styles.sectionTitle}>Add Extra Services</Text>
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            {ADDON_SERVICES.map(addon => {
              const inCart = isInCart(addon.id);
              return (
                <TouchableOpacity key={addon.id} style={[styles.addonCard, inCart && styles.addonCardActive]} onPress={() => handleToggleCartItem(addon)}>
                  <Text style={styles.addonName}>{addon.name}</Text>
                  <Text style={[styles.addonPrice, inCart && styles.addonPriceActive]}>+₹{addon.price}</Text>
                  <View style={[styles.addonCheck, inCart && styles.addonCheckActive]}>
                    <Ionicons name={inCart ? 'checkmark' : 'add'} size={14} color={inCart ? '#FFFFFF' : '#10B981'} />
                  </View>
                </TouchableOpacity>
              );
            })}
          </ScrollView>
        </Animated.View>

        {/* Products */}
        <Animated.View entering={FadeInDown.delay(300)} style={styles.section}>
          <View style={styles.sectionHeader}>
            <Ionicons name="bag" size={18} color="#F59E0B" />
            <Text style={styles.sectionTitle}>Recommended Products</Text>
          </View>
          <View style={styles.productsGrid}>
            {ADDON_PRODUCTS.map(product => {
              const inCart = isInCart(product.id);
              return (
                <TouchableOpacity key={product.id} style={[styles.productCard, inCart && styles.productCardActive]} onPress={() => handleToggleCartItem(product)}>
                  <View style={styles.productIcon}><Ionicons name={inCart ? 'checkmark-circle' : 'bag-outline'} size={22} color={inCart ? '#10B981' : '#F59E0B'} /></View>
                  <Text style={styles.productName} numberOfLines={2}>{product.name}</Text>
                  <Text style={styles.productPrice}>₹{product.price}</Text>
                  <View style={[styles.productBtn, inCart && styles.productBtnActive]}>
                    <Text style={[styles.productBtnText, inCart && styles.productBtnTextActive]}>{inCart ? 'Added ✓' : 'Add'}</Text>
                  </View>
                </TouchableOpacity>
              );
            })}
          </View>
        </Animated.View>

        {/* Aftercare Tips */}
        {recommendations.aftercare_tips?.length > 0 && (
          <Animated.View entering={FadeInDown.delay(350)} style={styles.tipsCard}>
            <View style={styles.sectionHeader}>
              <Ionicons name="heart" size={18} color="#EC4899" />
              <Text style={styles.sectionTitle}>Aftercare Tips</Text>
            </View>
            {recommendations.aftercare_tips.map((tip: string, i: number) => (
              <View key={i} style={styles.tipItem}>
                <View style={styles.tipDot} />
                <Text style={styles.tipText}>{tip}</Text>
              </View>
            ))}
          </Animated.View>
        )}
      </ScrollView>

      {/* Bottom Bar */}
      {cartCount > 0 && (
        <Animated.View entering={FadeIn} style={[styles.bottomBar, { paddingBottom: insets.bottom + 16 }]}>
          <View style={styles.bottomInfo}>
            <Text style={styles.bottomItems}>{cartCount} items in cart</Text>
          </View>
          <TouchableOpacity style={styles.bottomBtn} onPress={() => router.push('/cart')}>
            <Text style={styles.bottomBtnText}>Go to Cart</Text>
            <Ionicons name="arrow-forward" size={18} color="#FFFFFF" />
          </TouchableOpacity>
        </Animated.View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F8FAFC' },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 16, paddingVertical: 12 },
  headerBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: '#FFFFFF', justifyContent: 'center', alignItems: 'center', borderWidth: 1, borderColor: '#E2E8F0' },
  headerTitle: { fontSize: 18, fontWeight: '600', color: '#1E293B' },
  cartBadge: { position: 'absolute', top: -2, right: -2, backgroundColor: '#EF4444', width: 16, height: 16, borderRadius: 8, justifyContent: 'center', alignItems: 'center' },
  cartBadgeText: { fontSize: 10, fontWeight: '700', color: '#FFFFFF' },
  content: { padding: 20, paddingBottom: 140 },
  emptyContainer: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: 12 },
  emptyText: { fontSize: 16, color: '#64748B' },
  backBtn: { backgroundColor: '#0EA5E9', paddingVertical: 12, paddingHorizontal: 24, borderRadius: 24, marginTop: 12 },
  backBtnText: { fontSize: 14, fontWeight: '600', color: '#FFFFFF' },
  section: { marginBottom: 24 },
  sectionHeader: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 14 },
  sectionTitle: { fontSize: 16, fontWeight: '600', color: '#1E293B' },
  serviceCard: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#FFFFFF', borderRadius: 14, padding: 16, marginBottom: 10, borderWidth: 1, borderColor: '#E2E8F0' },
  serviceInfo: { flex: 1, marginRight: 12 },
  serviceName: { fontSize: 15, fontWeight: '600', color: '#1E293B' },
  serviceReason: { fontSize: 13, color: '#64748B', marginTop: 4 },
  addBtn: { width: 36, height: 36, borderRadius: 18, backgroundColor: '#0EA5E9', justifyContent: 'center', alignItems: 'center' },
  addBtnActive: { backgroundColor: '#10B981' },
  infoCard: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#F5F3FF', borderRadius: 14, padding: 16, marginBottom: 12, gap: 12 },
  infoContent: { flex: 1 },
  infoLabel: { fontSize: 12, color: '#7C3AED' },
  infoValue: { fontSize: 15, fontWeight: '600', color: '#5B21B6' },
  costCard: { backgroundColor: '#E0F2FE', borderRadius: 14, padding: 18, alignItems: 'center', marginBottom: 24 },
  costLabel: { fontSize: 13, color: '#0284C7' },
  costValue: { fontSize: 26, fontWeight: '700', color: '#0EA5E9', marginTop: 4 },
  addonCard: { backgroundColor: '#FFFFFF', borderRadius: 12, padding: 14, marginRight: 10, borderWidth: 1, borderColor: '#E2E8F0', minWidth: 140 },
  addonCardActive: { borderColor: '#10B981', backgroundColor: '#D1FAE5' },
  addonName: { fontSize: 13, fontWeight: '500', color: '#1E293B' },
  addonPrice: { fontSize: 14, fontWeight: '600', color: '#10B981', marginTop: 6 },
  addonPriceActive: { color: '#059669' },
  addonCheck: { width: 24, height: 24, borderRadius: 12, backgroundColor: '#D1FAE5', justifyContent: 'center', alignItems: 'center', marginTop: 10 },
  addonCheckActive: { backgroundColor: '#10B981' },
  productsGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  productCard: { width: '48%', backgroundColor: '#FFFFFF', borderRadius: 14, padding: 14, borderWidth: 1, borderColor: '#E2E8F0' },
  productCardActive: { borderColor: '#10B981', backgroundColor: '#D1FAE5' },
  productIcon: { width: 40, height: 40, borderRadius: 20, backgroundColor: '#FEF3C7', justifyContent: 'center', alignItems: 'center', marginBottom: 10 },
  productName: { fontSize: 13, fontWeight: '500', color: '#1E293B', height: 36 },
  productPrice: { fontSize: 15, fontWeight: '600', color: '#F59E0B', marginTop: 6 },
  productBtn: { backgroundColor: '#FEF3C7', borderRadius: 16, paddingVertical: 8, marginTop: 10 },
  productBtnActive: { backgroundColor: '#10B981' },
  productBtnText: { fontSize: 12, fontWeight: '600', color: '#B45309', textAlign: 'center' },
  productBtnTextActive: { color: '#FFFFFF' },
  tipsCard: { backgroundColor: '#FDF2F8', borderRadius: 16, padding: 18 },
  tipItem: { flexDirection: 'row', alignItems: 'flex-start', marginTop: 10, gap: 10 },
  tipDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: '#EC4899', marginTop: 6 },
  tipText: { flex: 1, fontSize: 13, color: '#9D174D', lineHeight: 20 },
  bottomBar: { position: 'absolute', bottom: 0, left: 0, right: 0, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: '#FFFFFF', paddingHorizontal: 20, paddingTop: 16, borderTopWidth: 1, borderTopColor: '#E2E8F0' },
  bottomInfo: { flex: 1 },
  bottomItems: { fontSize: 14, color: '#64748B' },
  bottomBtn: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#0EA5E9', paddingVertical: 12, paddingHorizontal: 20, borderRadius: 24, gap: 8 },
  bottomBtnText: { fontSize: 14, fontWeight: '600', color: '#FFFFFF' },
});
