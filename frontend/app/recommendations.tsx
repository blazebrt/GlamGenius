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

// Add-on products with INR pricing
const ADDON_PRODUCTS = [
  { id: 'p1', name: 'Hair Serum (50ml)', price: 299, type: 'product' as const },
  { id: 'p2', name: 'Face Moisturizer', price: 399, type: 'product' as const },
  { id: 'p3', name: 'Sunscreen SPF 50', price: 349, type: 'product' as const },
  { id: 'p4', name: 'Hair Oil (100ml)', price: 249, type: 'product' as const },
  { id: 'p5', name: 'Lip Balm', price: 149, type: 'product' as const },
  { id: 'p6', name: 'Face Wash', price: 199, type: 'product' as const },
];

const ADDON_SERVICES = [
  { id: 'a1', name: 'Head Massage (15 min)', price: 199, duration: 15, type: 'addon' as const },
  { id: 'a2', name: 'Hand Massage', price: 149, duration: 10, type: 'addon' as const },
  { id: 'a3', name: 'Deep Conditioning', price: 299, duration: 20, type: 'addon' as const },
  { id: 'a4', name: 'Under-eye Treatment', price: 249, duration: 15, type: 'addon' as const },
  { id: 'a5', name: 'Lip Scrub & Care', price: 99, duration: 10, type: 'addon' as const },
];

export default function RecommendationsScreen() {
  const router = useRouter();
  const params = useLocalSearchParams();
  const insets = useSafeAreaInsets();
  const { addItem, getItemCount, items, removeItem } = useCartStore();

  const recommendations = params.recommendations
    ? JSON.parse(params.recommendations as string)
    : null;

  // Check if item is in cart
  const isInCart = (itemId: string) => {
    return items.some(item => item.id === itemId);
  };

  // Check if service is in cart by name
  const isServiceInCart = (serviceName: string) => {
    return items.some(item => item.name === serviceName);
  };

  const handleToggleCartItem = (item: any) => {
    if (isInCart(item.id)) {
      removeItem(item.id);
    } else {
      addItem(item);
    }
  };

  const handleToggleServiceCart = (service: any) => {
    const existingItem = items.find(item => item.name === service.name);
    if (existingItem) {
      removeItem(existingItem.id);
    } else {
      // Extract price from service (take minimum price)
      const priceMatch = service.name?.match(/\d+/) || ['999'];
      const price = parseInt(priceMatch[0]) || 999;
      
      addItem({
        id: `service-${Date.now()}`,
        type: 'service',
        name: service.name,
        price: price,
        duration: 45,
      });
    }
  };

  const goToCart = () => {
    router.push('/cart');
  };

  if (!recommendations) {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.emptyContainer}>
          <Ionicons name="alert-circle" size={48} color="#D4AF37" />
          <Text style={styles.emptyText}>No recommendations available</Text>
          <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
            <Text style={styles.backButtonText}>Go Back</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  const cartCount = getItemCount();

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={styles.headerBackButton}>
          <Ionicons name="arrow-back" size={24} color="#FFFFFF" />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Your Recommendations</Text>
        <TouchableOpacity onPress={goToCart} style={styles.cartButton}>
          <Ionicons name="cart" size={24} color="#FFFFFF" />
          {cartCount > 0 && (
            <View style={styles.cartBadge}>
              <Text style={styles.cartBadgeText}>{cartCount}</Text>
            </View>
          )}
        </TouchableOpacity>
      </View>

      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.scrollContent}
      >
        {/* Hero Section */}
        <Animated.View entering={FadeIn} style={styles.heroSection}>
          <View style={styles.heroIcon}>
            <Ionicons name="sparkles" size={32} color="#D4AF37" />
          </View>
          <Text style={styles.heroTitle}>Personalized For You</Text>
          <Text style={styles.heroSubtitle}>
            Based on your profile, preferences, and occasion
          </Text>
        </Animated.View>

        {/* Services */}
        {recommendations.services?.length > 0 && (
          <Animated.View entering={FadeInDown.delay(100)} style={styles.section}>
            <View style={styles.sectionHeader}>
              <View style={styles.sectionTitleRow}>
                <Ionicons name="star" size={20} color="#D4AF37" />
                <Text style={styles.sectionTitle}>Recommended Services</Text>
              </View>
            </View>
            {recommendations.services.map((service: any, index: number) => {
              const inCart = isServiceInCart(service.name);
              return (
                <View key={index} style={styles.serviceCard}>
                  <View style={styles.serviceInfo}>
                    <Text style={styles.serviceName}>{service.name}</Text>
                    {service.reason && (
                      <Text style={styles.serviceReason}>{service.reason}</Text>
                    )}
                    {service.expected_result && (
                      <Text style={styles.serviceResult}>
                        Expected: {service.expected_result}
                      </Text>
                    )}
                  </View>
                  <TouchableOpacity
                    style={[styles.addButton, inCart && styles.addButtonActive]}
                    onPress={() => handleToggleServiceCart(service)}
                  >
                    <Ionicons 
                      name={inCart ? "checkmark" : "add"} 
                      size={20} 
                      color={inCart ? "#FFFFFF" : "#0A0A0A"} 
                    />
                  </TouchableOpacity>
                </View>
              );
            })}
          </Animated.View>
        )}

        {/* Stylist Level & Cost */}
        <Animated.View entering={FadeInDown.delay(200)} style={styles.metricsRow}>
          {recommendations.stylist_level && (
            <View style={styles.metricCard}>
              <Ionicons name="person" size={24} color="#D4AF37" />
              <Text style={styles.metricLabel}>Stylist</Text>
              <Text style={styles.metricValue}>{recommendations.stylist_level}</Text>
            </View>
          )}
          {recommendations.total_estimated_cost && (
            <View style={styles.metricCard}>
              <Ionicons name="wallet-outline" size={24} color="#D4AF37" />
              <Text style={styles.metricLabel}>Estimated</Text>
              <Text style={styles.metricValue}>{recommendations.total_estimated_cost}</Text>
            </View>
          )}
        </Animated.View>

        {/* Add-on Services */}
        <Animated.View entering={FadeInDown.delay(250)} style={styles.section}>
          <View style={styles.sectionHeader}>
            <View style={styles.sectionTitleRow}>
              <Ionicons name="add-circle" size={20} color="#D4AF37" />
              <Text style={styles.sectionTitle}>Add Extra Services</Text>
            </View>
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            {ADDON_SERVICES.map((addon) => {
              const inCart = isInCart(addon.id);
              return (
                <TouchableOpacity
                  key={addon.id}
                  style={[styles.addonCard, inCart && styles.addonCardActive]}
                  onPress={() => handleToggleCartItem(addon)}
                >
                  <Text style={[styles.addonName, inCart && styles.addonNameActive]}>{addon.name}</Text>
                  <Text style={[styles.addonPrice, inCart && styles.addonPriceActive]}>+₹{addon.price}</Text>
                  <View style={[styles.addonAddBtn, inCart && styles.addonAddBtnActive]}>
                    <Ionicons name={inCart ? "checkmark" : "add"} size={16} color={inCart ? "#FFFFFF" : "#D4AF37"} />
                  </View>
                </TouchableOpacity>
              );
            })}
          </ScrollView>
        </Animated.View>

        {/* Expected Outcome */}
        {recommendations.expected_outcome && (
          <Animated.View entering={FadeInDown.delay(300)} style={styles.section}>
            <View style={styles.sectionTitleRow}>
              <Ionicons name="eye" size={20} color="#D4AF37" />
              <Text style={styles.sectionTitle}>Expected Outcome</Text>
            </View>
            <View style={styles.outcomeCard}>
              <Text style={styles.outcomeText}>{recommendations.expected_outcome}</Text>
            </View>
          </Animated.View>
        )}

        {/* Aftercare Tips with Products */}
        {recommendations.aftercare_tips?.length > 0 && (
          <Animated.View entering={FadeInDown.delay(350)} style={styles.section}>
            <View style={styles.sectionTitleRow}>
              <Ionicons name="heart" size={20} color="#D4AF37" />
              <Text style={styles.sectionTitle}>Aftercare Tips</Text>
            </View>
            <View style={styles.tipsCard}>
              {recommendations.aftercare_tips.map((tip: string, index: number) => (
                <View key={index} style={styles.tipItem}>
                  <Ionicons name="checkmark" size={16} color="#2ECC71" />
                  <Text style={styles.tipText}>{tip}</Text>
                </View>
              ))}
            </View>
          </Animated.View>
        )}

        {/* Buy Products */}
        <Animated.View entering={FadeInDown.delay(400)} style={styles.section}>
          <View style={styles.sectionHeader}>
            <View style={styles.sectionTitleRow}>
              <Ionicons name="bag" size={20} color="#D4AF37" />
              <Text style={styles.sectionTitle}>Recommended Products</Text>
            </View>
          </View>
          <View style={styles.productsGrid}>
            {ADDON_PRODUCTS.map((product) => (
              <TouchableOpacity
                key={product.id}
                style={styles.productCard}
                onPress={() => handleAddToCart(product)}
              >
                <View style={styles.productIcon}>
                  <Ionicons name="bag-outline" size={24} color="#D4AF37" />
                </View>
                <Text style={styles.productName} numberOfLines={2}>{product.name}</Text>
                <Text style={styles.productPrice}>₹{product.price}</Text>
                <TouchableOpacity style={styles.productAddBtn} onPress={() => handleAddToCart(product)}>
                  <Text style={styles.productAddText}>Add to Cart</Text>
                </TouchableOpacity>
              </TouchableOpacity>
            ))}
          </View>
        </Animated.View>

        {/* Maintenance Tips */}
        {recommendations.maintenance_tips?.length > 0 && (
          <Animated.View entering={FadeInDown.delay(450)} style={styles.section}>
            <View style={styles.sectionTitleRow}>
              <Ionicons name="calendar" size={20} color="#D4AF37" />
              <Text style={styles.sectionTitle}>Maintenance Tips</Text>
            </View>
            <View style={styles.tipsCard}>
              {recommendations.maintenance_tips.map((tip: string, index: number) => (
                <View key={index} style={styles.tipItem}>
                  <Ionicons name="calendar-outline" size={16} color="#D4AF37" />
                  <Text style={styles.tipText}>{tip}</Text>
                </View>
              ))}
            </View>
          </Animated.View>
        )}

        {/* Upsell */}
        {recommendations.upsell_suggestions?.length > 0 && (
          <Animated.View entering={FadeInDown.delay(500)} style={styles.section}>
            <View style={styles.sectionTitleRow}>
              <Ionicons name="trending-up" size={20} color="#9B59B6" />
              <Text style={styles.sectionTitle}>Next Visit Suggestions</Text>
            </View>
            <View style={styles.upsellContainer}>
              {recommendations.upsell_suggestions.map((suggestion: string, index: number) => (
                <View key={index} style={styles.upsellItem}>
                  <Ionicons name="arrow-forward-circle" size={18} color="#9B59B6" />
                  <Text style={styles.upsellText}>{suggestion}</Text>
                </View>
              ))}
            </View>
          </Animated.View>
        )}
      </ScrollView>

      {/* Checkout Bar */}
      {cartCount > 0 && (
        <Animated.View
          entering={FadeInDown}
          style={[styles.checkoutBar, { paddingBottom: insets.bottom + 16 }]}
        >
          <View style={styles.checkoutInfo}>
            <Text style={styles.checkoutItems}>{cartCount} items in cart</Text>
          </View>
          <TouchableOpacity style={styles.checkoutButton} onPress={goToCart}>
            <Ionicons name="cart" size={20} color="#0A0A0A" />
            <Text style={styles.checkoutButtonText}>Go to Cart</Text>
          </TouchableOpacity>
        </Animated.View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0A0A0A',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  headerBackButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(255, 255, 255, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  headerTitle: {
    fontSize: 18,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  cartButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(255, 255, 255, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  cartBadge: {
    position: 'absolute',
    top: -4,
    right: -4,
    width: 18,
    height: 18,
    borderRadius: 9,
    backgroundColor: '#D4AF37',
    justifyContent: 'center',
    alignItems: 'center',
  },
  cartBadgeText: {
    fontSize: 10,
    fontWeight: '700',
    color: '#0A0A0A',
  },
  emptyContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 40,
  },
  emptyText: {
    fontSize: 16,
    color: 'rgba(255, 255, 255, 0.6)',
    marginTop: 16,
    textAlign: 'center',
  },
  backButton: {
    marginTop: 24,
    paddingVertical: 12,
    paddingHorizontal: 24,
    backgroundColor: '#D4AF37',
    borderRadius: 24,
  },
  backButtonText: {
    fontSize: 14,
    fontWeight: '600',
    color: '#0A0A0A',
  },
  scrollContent: {
    paddingHorizontal: 20,
    paddingBottom: 120,
  },
  heroSection: {
    alignItems: 'center',
    paddingVertical: 20,
  },
  heroIcon: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 2,
    borderColor: 'rgba(212, 175, 55, 0.3)',
  },
  heroTitle: {
    fontSize: 22,
    fontWeight: '700',
    color: '#FFFFFF',
    marginTop: 12,
  },
  heroSubtitle: {
    fontSize: 13,
    color: 'rgba(255, 255, 255, 0.5)',
    textAlign: 'center',
    marginTop: 4,
  },
  section: {
    marginTop: 20,
  },
  sectionHeader: {
    marginBottom: 12,
  },
  sectionTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 12,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  serviceCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
  },
  serviceInfo: {
    flex: 1,
  },
  serviceName: {
    fontSize: 15,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  serviceReason: {
    fontSize: 12,
    color: 'rgba(255, 255, 255, 0.6)',
    marginTop: 4,
  },
  serviceResult: {
    fontSize: 11,
    color: '#D4AF37',
    marginTop: 4,
  },
  addButton: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: '#D4AF37',
    justifyContent: 'center',
    alignItems: 'center',
  },
  metricsRow: {
    flexDirection: 'row',
    gap: 12,
    marginTop: 16,
  },
  metricCard: {
    flex: 1,
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 12,
    padding: 14,
    alignItems: 'center',
  },
  metricLabel: {
    fontSize: 11,
    color: 'rgba(255, 255, 255, 0.5)',
    marginTop: 6,
  },
  metricValue: {
    fontSize: 14,
    fontWeight: '600',
    color: '#FFFFFF',
    marginTop: 2,
  },
  addonCard: {
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 12,
    padding: 14,
    marginRight: 10,
    width: 140,
    alignItems: 'center',
  },
  addonName: {
    fontSize: 12,
    color: '#FFFFFF',
    textAlign: 'center',
  },
  addonPrice: {
    fontSize: 14,
    fontWeight: '600',
    color: '#D4AF37',
    marginTop: 6,
  },
  addonAddBtn: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: 'rgba(212, 175, 55, 0.2)',
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: 8,
  },
  outcomeCard: {
    backgroundColor: 'rgba(46, 204, 113, 0.1)',
    borderRadius: 12,
    padding: 14,
  },
  outcomeText: {
    fontSize: 13,
    color: '#FFFFFF',
    lineHeight: 20,
  },
  tipsCard: {
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 12,
    padding: 14,
  },
  tipItem: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: 10,
    gap: 10,
  },
  tipText: {
    fontSize: 13,
    color: 'rgba(255, 255, 255, 0.7)',
    flex: 1,
    lineHeight: 18,
  },
  productsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
  },
  productCard: {
    width: '48%',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 12,
    padding: 14,
    alignItems: 'center',
  },
  productIcon: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 8,
  },
  productName: {
    fontSize: 12,
    color: '#FFFFFF',
    textAlign: 'center',
    height: 32,
  },
  productPrice: {
    fontSize: 14,
    fontWeight: '600',
    color: '#D4AF37',
    marginTop: 4,
  },
  productAddBtn: {
    backgroundColor: 'rgba(212, 175, 55, 0.2)',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 12,
    marginTop: 8,
  },
  productAddText: {
    fontSize: 11,
    color: '#D4AF37',
    fontWeight: '500',
  },
  upsellContainer: {
    backgroundColor: 'rgba(155, 89, 182, 0.1)',
    borderRadius: 12,
    padding: 14,
  },
  upsellItem: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 10,
    gap: 10,
  },
  upsellText: {
    fontSize: 13,
    color: 'rgba(255, 255, 255, 0.7)',
    flex: 1,
  },
  checkoutBar: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#121212',
    paddingHorizontal: 20,
    paddingTop: 16,
    borderTopWidth: 1,
    borderTopColor: 'rgba(255,255,255,0.1)',
  },
  checkoutInfo: {
    flex: 1,
  },
  checkoutItems: {
    fontSize: 14,
    color: '#FFFFFF',
  },
  checkoutButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#D4AF37',
    paddingVertical: 12,
    paddingHorizontal: 20,
    borderRadius: 24,
    gap: 8,
  },
  checkoutButtonText: {
    fontSize: 14,
    fontWeight: '600',
    color: '#0A0A0A',
  },
});
