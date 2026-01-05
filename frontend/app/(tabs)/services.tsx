import React, { useEffect, useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  TextInput,
  ActivityIndicator,
  RefreshControl,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeIn, FadeInDown, Layout } from 'react-native-reanimated';
import { api } from '../../src/services/api';
import { useCartStore } from '../../src/store/cartStore';
import { COLORS, FONTS, SPACING, RADIUS, SHADOWS } from '../../src/theme/colors';

const CATEGORIES = [
  { id: 'All', label: 'All', icon: 'apps-outline' },
  { id: 'Hair', label: 'Hair', icon: 'cut-outline' },
  { id: 'Skin', label: 'Skin', icon: 'sparkles-outline' },
  { id: 'Scalp', label: 'Scalp', icon: 'scan-outline' },
  { id: 'Body', label: 'Body', icon: 'body-outline' },
];

export default function ServicesScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { addItem, items: cartItems, removeItem } = useCartStore();
  
  const [services, setServices] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('All');

  const cartCount = cartItems.length;

  const fetchServices = async () => {
    try {
      const response = await api.get('/services');
      setServices(response.data);
    } catch (error) {
      console.error('Error fetching services:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchServices();
  }, []);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await fetchServices();
    setRefreshing(false);
  }, []);

  const filteredServices = services.filter(service => {
    const matchesSearch = service.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
                          service.description?.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesCategory = selectedCategory === 'All' || service.category === selectedCategory;
    return matchesSearch && matchesCategory;
  });

  const handleToggleCart = (service: any) => {
    const existing = cartItems.find(item => item.id === service.id);
    if (existing) {
      // Remove from cart if already added
      removeItem(service.id);
    } else {
      // Add to cart
      addItem({
        id: service.id,
        name: service.name,
        price: service.base_price || 999,
        type: 'service',
        duration: service.duration_minutes || 30,
      });
    }
  };

  const isInCart = (serviceId: string) => cartItems.some(item => item.id === serviceId);

  const getCategoryIcon = (category: string) => {
    const found = CATEGORIES.find(c => c.id === category);
    return found?.icon || 'sparkles-outline';
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Header */}
      <Animated.View entering={FadeIn} style={styles.header}>
        <View>
          <Text style={styles.headerLabel}>TREATMENTS</Text>
          <Text style={styles.headerTitle}>Services</Text>
        </View>
        {cartCount > 0 && (
          <TouchableOpacity style={styles.cartButton} onPress={() => router.push('/cart')}>
            <Ionicons name="cart-outline" size={22} color={COLORS.primary} />
            <View style={styles.cartBadge}>
              <Text style={styles.cartBadgeText}>{cartCount}</Text>
            </View>
          </TouchableOpacity>
        )}
      </Animated.View>

      {/* Search */}
      <Animated.View entering={FadeIn.delay(100)} style={styles.searchContainer}>
        <Ionicons name="search-outline" size={20} color={COLORS.textMuted} />
        <TextInput
          style={styles.searchInput}
          placeholder="Search treatments..."
          placeholderTextColor={COLORS.textMuted}
          value={searchQuery}
          onChangeText={setSearchQuery}
        />
        {searchQuery.length > 0 && (
          <TouchableOpacity onPress={() => setSearchQuery('')}>
            <Ionicons name="close-circle" size={20} color={COLORS.textMuted} />
          </TouchableOpacity>
        )}
      </Animated.View>

      {/* Categories */}
      <Animated.View entering={FadeIn.delay(150)} style={styles.categoriesWrapper}>
        <ScrollView 
          horizontal 
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.categoriesContent}
        >
          {CATEGORIES.map((category) => (
            <TouchableOpacity
              key={category.id}
              style={[
                styles.categoryChip,
                selectedCategory === category.id && styles.categoryChipActive
              ]}
              onPress={() => setSelectedCategory(category.id)}
            >
              <Ionicons
                name={category.icon as any}
                size={16}
                color={selectedCategory === category.id ? COLORS.white : COLORS.textSecondary}
              />
              <Text style={[
                styles.categoryChipText,
                selectedCategory === category.id && styles.categoryChipTextActive
              ]}>
                {category.label}
              </Text>
            </TouchableOpacity>
          ))}
        </ScrollView>
      </Animated.View>

      {/* Results Count */}
      <View style={styles.resultsBar}>
        <Text style={styles.resultsText}>{filteredServices.length} treatments available</Text>
      </View>

      {/* Services List */}
      {loading ? (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color={COLORS.primary} />
        </View>
      ) : (
        <ScrollView
          showsVerticalScrollIndicator={false}
          contentContainerStyle={styles.servicesContent}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={COLORS.primary} />
          }
        >
          {filteredServices.length === 0 ? (
            <View style={styles.emptyContainer}>
              <Ionicons name="search" size={48} color={COLORS.border} />
              <Text style={styles.emptyText}>No treatments found</Text>
            </View>
          ) : (
            filteredServices.map((service, index) => {
              const inCart = isInCart(service.id);
              return (
                <Animated.View 
                  key={service.id} 
                  entering={FadeInDown.delay(index * 30)} 
                  layout={Layout.springify()}
                >
                  <TouchableOpacity
                    style={styles.serviceCard}
                    onPress={() => router.push({ pathname: '/service-details', params: { serviceId: service.id } })}
                    activeOpacity={0.7}
                  >
                    <View style={styles.serviceIconContainer}>
                      <Ionicons name={getCategoryIcon(service.category) as any} size={24} color={COLORS.primary} />
                    </View>
                    <View style={styles.serviceInfo}>
                      <Text style={styles.serviceName}>{service.name}</Text>
                      <Text style={styles.serviceDescription} numberOfLines={2}>{service.description}</Text>
                      <View style={styles.serviceMeta}>
                        <View style={styles.serviceMetaItem}>
                          <Ionicons name="time-outline" size={14} color={COLORS.textMuted} />
                          <Text style={styles.serviceMetaText}>{service.duration_minutes} min</Text>
                        </View>
                        <Text style={styles.servicePrice}>{service.price_range}</Text>
                      </View>
                    </View>
                    <TouchableOpacity
                      style={[styles.addButton, inCart && styles.addButtonActive]}
                      onPress={(e) => { e.stopPropagation(); handleToggleCart(service); }}
                    >
                      <Ionicons name={inCart ? 'checkmark' : 'add'} size={20} color={inCart ? COLORS.white : COLORS.black} />
                    </TouchableOpacity>
                  </TouchableOpacity>
                </Animated.View>
              );
            })
          )}
          <View style={{ height: 100 }} />
        </ScrollView>
      )}

      {/* Floating Cart */}
      {cartCount > 0 && (
        <Animated.View entering={FadeInDown} style={[styles.floatingCart, { bottom: insets.bottom + 90 }]}>
          <TouchableOpacity style={styles.floatingCartButton} onPress={() => router.push('/cart')}>
            <View style={styles.floatingCartLeft}>
              <Ionicons name="cart" size={20} color={COLORS.white} />
              <Text style={styles.floatingCartText}>{cartCount} items</Text>
            </View>
            <Text style={styles.floatingCartAction}>View Cart →</Text>
          </TouchableOpacity>
        </Animated.View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.backgroundSecondary,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: SPACING.lg,
    paddingVertical: SPACING.md,
    backgroundColor: COLORS.background,
  },
  headerLabel: {
    fontSize: FONTS.sizes.caption,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.primary,
    letterSpacing: 2,
  },
  headerTitle: {
    fontSize: FONTS.sizes.h2,
    fontFamily: FONTS.family.heading,
    color: COLORS.textPrimary,
    marginTop: 2,
  },
  cartButton: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: COLORS.primaryLight,
    justifyContent: 'center',
    alignItems: 'center',
  },
  cartBadge: {
    position: 'absolute',
    top: -2,
    right: -2,
    backgroundColor: COLORS.error,
    width: 18,
    height: 18,
    borderRadius: 9,
    justifyContent: 'center',
    alignItems: 'center',
  },
  cartBadgeText: {
    fontSize: 10,
    fontFamily: FONTS.family.bodyBold,
    color: COLORS.white,
  },
  searchContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.background,
    marginHorizontal: SPACING.lg,
    marginTop: SPACING.md,
    paddingHorizontal: SPACING.md,
    borderRadius: RADIUS.md,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  searchInput: {
    flex: 1,
    paddingVertical: 14,
    marginLeft: 10,
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.body,
    color: COLORS.textPrimary,
  },
  categoriesWrapper: {
    marginTop: SPACING.md,
  },
  categoriesContent: {
    paddingHorizontal: SPACING.lg,
    paddingVertical: 4,
  },
  categoryChip: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: RADIUS.full,
    backgroundColor: COLORS.background,
    borderWidth: 1,
    borderColor: COLORS.border,
    marginRight: 10,
    gap: 6,
  },
  categoryChipActive: {
    backgroundColor: COLORS.primary,
    borderColor: COLORS.primary,
  },
  categoryChipText: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.bodyMedium,
    color: COLORS.textSecondary,
  },
  categoryChipTextActive: {
    color: COLORS.white,
  },
  resultsBar: {
    paddingHorizontal: SPACING.lg,
    paddingVertical: SPACING.sm,
  },
  resultsText: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.body,
    color: COLORS.textMuted,
  },
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  servicesContent: {
    paddingHorizontal: SPACING.lg,
    paddingBottom: 180,
  },
  emptyContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingVertical: 60,
  },
  emptyText: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.body,
    color: COLORS.textMuted,
    marginTop: SPACING.md,
  },
  serviceCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.background,
    borderRadius: RADIUS.lg,
    padding: SPACING.md,
    marginBottom: SPACING.md,
    ...SHADOWS.sm,
  },
  serviceIconContainer: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: COLORS.primaryLight,
    justifyContent: 'center',
    alignItems: 'center',
  },
  serviceInfo: {
    flex: 1,
    marginLeft: SPACING.md,
    marginRight: SPACING.sm,
  },
  serviceName: {
    fontSize: FONTS.sizes.bodyLg,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.textPrimary,
  },
  serviceDescription: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
    marginTop: 4,
    lineHeight: 20,
  },
  serviceMeta: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: SPACING.sm,
    gap: SPACING.md,
  },
  serviceMetaItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  serviceMetaText: {
    fontSize: FONTS.sizes.bodySm,
    fontFamily: FONTS.family.body,
    color: COLORS.textMuted,
  },
  servicePrice: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.bodyBold,
    color: COLORS.primary,
  },
  addButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: COLORS.primary,
    justifyContent: 'center',
    alignItems: 'center',
  },
  addButtonActive: {
    backgroundColor: COLORS.success,
  },
  floatingCart: {
    position: 'absolute',
    left: SPACING.lg,
    right: SPACING.lg,
  },
  floatingCartButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: COLORS.primary,
    paddingVertical: 16,
    paddingHorizontal: 20,
    borderRadius: RADIUS.xl,
  },
  floatingCartLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  floatingCartText: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.white,
  },
  floatingCartAction: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.white,
  },
});
