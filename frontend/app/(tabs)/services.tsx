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
import { COLORS, FONTS } from '../../src/theme/colors';

const CATEGORIES = [
  { id: 'All', icon: 'grid-outline', label: 'All' },
  { id: 'Hair', icon: 'cut-outline', label: 'Hair' },
  { id: 'Skin', icon: 'flower-outline', label: 'Skin' },
  { id: 'Makeup', icon: 'color-palette-outline', label: 'Makeup' },
  { id: 'Nails', icon: 'hand-left-outline', label: 'Nails' },
  { id: 'Grooming', icon: 'man-outline', label: 'Grooming' },
];

interface Service {
  id: string;
  name: string;
  category: string;
  description: string;
  price_range: string;
  duration_minutes: number;
  suitable_for: string[];
  benefits: string[];
}

export default function ServicesScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { addItem, items, removeItem } = useCartStore();

  const [services, setServices] = useState<Service[]>([]);
  const [filteredServices, setFilteredServices] = useState<Service[]>([]);
  const [selectedCategory, setSelectedCategory] = useState('All');
  const [searchQuery, setSearchQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    loadServices();
  }, []);

  useEffect(() => {
    filterServices();
  }, [selectedCategory, searchQuery, services]);

  const loadServices = async () => {
    try {
      setLoading(true);
      const response = await api.get('/services');
      setServices(response.data);
      setFilteredServices(response.data);
    } catch (error) {
      console.error('Error loading services:', error);
    } finally {
      setLoading(false);
    }
  };

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await loadServices();
    setRefreshing(false);
  }, []);

  const filterServices = () => {
    let filtered = services;
    if (selectedCategory !== 'All') {
      filtered = filtered.filter((s) => s.category === selectedCategory);
    }
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      filtered = filtered.filter(
        (s) =>
          s.name.toLowerCase().includes(query) ||
          s.description.toLowerCase().includes(query)
      );
    }
    setFilteredServices(filtered);
  };

  const getCategoryIcon = (category: string) => {
    const cat = CATEGORIES.find((c) => c.id === category);
    return cat?.icon || 'grid-outline';
  };

  const isInCart = (serviceId: string) => {
    return items.some((item) => item.id === serviceId);
  };

  const handleAddToCart = (service: Service) => {
    if (isInCart(service.id)) {
      removeItem(service.id);
    } else {
      const priceMatch = service.price_range?.match(/[\d,]+/);
      const price = priceMatch ? parseInt(priceMatch[0].replace(/,/g, '')) : 999;
      addItem({
        id: service.id,
        type: 'service',
        name: service.name,
        price: price,
        duration: service.duration_minutes,
      });
    }
  };

  const cartCount = items.length;

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Header */}
      <Animated.View entering={FadeIn.duration(300)} style={styles.header}>
        <View>
          <Text style={styles.headerTitle}>Services</Text>
          <Text style={styles.headerSubtitle}>Find the perfect treatment</Text>
        </View>
        {cartCount > 0 && (
          <TouchableOpacity style={styles.cartButton} onPress={() => router.push('/cart')}>
            <Ionicons name="cart-outline" size={22} color="#0EA5E9" />
            <View style={styles.cartBadge}>
              <Text style={styles.cartBadgeText}>{cartCount}</Text>
            </View>
          </TouchableOpacity>
        )}
      </Animated.View>

      {/* Search Bar */}
      <Animated.View entering={FadeIn.delay(100)} style={styles.searchContainer}>
        <Ionicons name="search" size={20} color="#94A3B8" />
        <TextInput
          style={styles.searchInput}
          placeholder="Search services..."
          placeholderTextColor="#94A3B8"
          value={searchQuery}
          onChangeText={setSearchQuery}
        />
        {searchQuery.length > 0 && (
          <TouchableOpacity onPress={() => setSearchQuery('')}>
            <Ionicons name="close-circle" size={20} color="#94A3B8" />
          </TouchableOpacity>
        )}
      </Animated.View>

      {/* Categories */}
      <Animated.View entering={FadeIn.delay(200)} style={styles.categoriesWrapper}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.categoriesContent}>
          {CATEGORIES.map((category) => (
            <TouchableOpacity
              key={category.id}
              style={[styles.categoryChip, selectedCategory === category.id && styles.categoryChipActive]}
              onPress={() => setSelectedCategory(category.id)}
            >
              <Ionicons
                name={category.icon as any}
                size={16}
                color={selectedCategory === category.id ? '#FFFFFF' : '#64748B'}
              />
              <Text style={[styles.categoryChipText, selectedCategory === category.id && styles.categoryChipTextActive]}>
                {category.label}
              </Text>
            </TouchableOpacity>
          ))}
        </ScrollView>
      </Animated.View>

      {/* Results Count */}
      <View style={styles.resultsBar}>
        <Text style={styles.resultsText}>{filteredServices.length} services found</Text>
      </View>

      {/* Services List */}
      {loading ? (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color="#0EA5E9" />
        </View>
      ) : (
        <ScrollView
          showsVerticalScrollIndicator={false}
          contentContainerStyle={styles.servicesContent}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#0EA5E9" />}
        >
          {filteredServices.length === 0 ? (
            <View style={styles.emptyContainer}>
              <Ionicons name="search" size={48} color="#E2E8F0" />
              <Text style={styles.emptyText}>No services found</Text>
            </View>
          ) : (
            filteredServices.map((service, index) => {
              const inCart = isInCart(service.id);
              return (
                <Animated.View key={service.id} entering={FadeInDown.delay(index * 30)} layout={Layout.springify()}>
                  <TouchableOpacity
                    style={styles.serviceCard}
                    onPress={() => router.push({ pathname: '/service-details', params: { serviceId: service.id } })}
                  >
                    <View style={styles.serviceIconContainer}>
                      <Ionicons name={getCategoryIcon(service.category) as any} size={22} color="#0EA5E9" />
                    </View>
                    <View style={styles.serviceInfo}>
                      <Text style={styles.serviceName}>{service.name}</Text>
                      <Text style={styles.serviceDescription} numberOfLines={2}>{service.description}</Text>
                      <View style={styles.serviceMeta}>
                        <View style={styles.serviceMetaItem}>
                          <Ionicons name="time-outline" size={14} color="#64748B" />
                          <Text style={styles.serviceMetaText}>{service.duration_minutes} min</Text>
                        </View>
                        <Text style={styles.servicePrice}>{service.price_range}</Text>
                      </View>
                    </View>
                    <TouchableOpacity
                      style={[styles.addButton, inCart && styles.addButtonActive]}
                      onPress={(e) => { e.stopPropagation(); handleAddToCart(service); }}
                    >
                      <Ionicons name={inCart ? 'checkmark' : 'add'} size={20} color="#FFFFFF" />
                    </TouchableOpacity>
                  </TouchableOpacity>
                </Animated.View>
              );
            })
          )}
        </ScrollView>
      )}

      {/* Floating Cart */}
      {cartCount > 0 && (
        <Animated.View entering={FadeInDown} style={[styles.floatingCart, { bottom: insets.bottom + 80 }]}>
          <TouchableOpacity style={styles.floatingCartButton} onPress={() => router.push('/cart')}>
            <View style={styles.floatingCartLeft}>
              <Ionicons name="cart" size={20} color="#FFFFFF" />
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
  container: { flex: 1, backgroundColor: '#F8FAFC' },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 20, paddingVertical: 12 },
  headerTitle: { fontSize: 24, fontWeight: '700', color: '#1E293B' },
  headerSubtitle: { fontSize: 14, color: '#64748B', marginTop: 2 },
  cartButton: { width: 44, height: 44, borderRadius: 22, backgroundColor: '#E0F2FE', justifyContent: 'center', alignItems: 'center' },
  cartBadge: { position: 'absolute', top: -2, right: -2, backgroundColor: '#EF4444', width: 18, height: 18, borderRadius: 9, justifyContent: 'center', alignItems: 'center' },
  cartBadgeText: { fontSize: 10, fontWeight: '700', color: '#FFFFFF' },
  searchContainer: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#FFFFFF', marginHorizontal: 20, paddingHorizontal: 16, borderRadius: 12, borderWidth: 1, borderColor: '#E2E8F0' },
  searchInput: { flex: 1, paddingVertical: 12, marginLeft: 10, fontSize: 16, color: '#1E293B' },
  categoriesWrapper: { marginTop: 16, minHeight: 50 },
  categoriesContent: { paddingHorizontal: 20, paddingVertical: 4 },
  categoryChip: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 10, borderRadius: 24, backgroundColor: '#FFFFFF', borderWidth: 1, borderColor: '#E2E8F0', marginRight: 10, gap: 6 },
  categoryChipActive: { backgroundColor: '#0EA5E9', borderColor: '#0EA5E9' },
  categoryChipText: { fontSize: 14, color: '#64748B', fontWeight: '500' },
  categoryChipTextActive: { color: '#FFFFFF' },
  resultsBar: { paddingHorizontal: 20, paddingVertical: 12 },
  resultsText: { fontSize: 13, color: '#94A3B8' },
  loadingContainer: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  servicesContent: { paddingHorizontal: 20, paddingBottom: 180 },
  emptyContainer: { flex: 1, justifyContent: 'center', alignItems: 'center', paddingVertical: 60 },
  emptyText: { fontSize: 16, color: '#94A3B8', marginTop: 12 },
  serviceCard: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#FFFFFF', borderRadius: 16, padding: 16, marginBottom: 12, borderWidth: 1, borderColor: '#E2E8F0' },
  serviceIconContainer: { width: 48, height: 48, borderRadius: 24, backgroundColor: '#E0F2FE', justifyContent: 'center', alignItems: 'center' },
  serviceInfo: { flex: 1, marginLeft: 14, marginRight: 10 },
  serviceName: { fontSize: 16, fontWeight: '600', color: '#1E293B' },
  serviceDescription: { fontSize: 13, color: '#64748B', marginTop: 4, lineHeight: 18 },
  serviceMeta: { flexDirection: 'row', alignItems: 'center', marginTop: 8, gap: 16 },
  serviceMetaItem: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  serviceMetaText: { fontSize: 12, color: '#64748B' },
  servicePrice: { fontSize: 14, fontWeight: '600', color: '#0EA5E9' },
  addButton: { width: 36, height: 36, borderRadius: 18, backgroundColor: '#0EA5E9', justifyContent: 'center', alignItems: 'center' },
  addButtonActive: { backgroundColor: '#10B981' },
  floatingCart: { position: 'absolute', left: 20, right: 20 },
  floatingCartButton: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: '#0EA5E9', paddingVertical: 14, paddingHorizontal: 20, borderRadius: 28 },
  floatingCartLeft: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  floatingCartText: { fontSize: 15, fontWeight: '600', color: '#FFFFFF' },
  floatingCartAction: { fontSize: 14, fontWeight: '600', color: '#FFFFFF' },
});
