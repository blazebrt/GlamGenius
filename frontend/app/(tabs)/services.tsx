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
import Animated, { 
  FadeIn, 
  FadeInDown, 
  Layout,
  SlideInRight,
} from 'react-native-reanimated';
import { api } from '../../src/services/api';
import { useCartStore } from '../../src/store/cartStore';

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
    const cat = CATEGORIES.find(c => c.id === category);
    return cat?.icon || 'grid-outline';
  };

  const isInCart = (serviceId: string) => {
    return items.some(item => item.id === serviceId);
  };

  const handleAddToCart = (service: Service) => {
    if (isInCart(service.id)) {
      removeItem(service.id);
    } else {
      // Extract price from price_range (take minimum)
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
        <View style={styles.headerTop}>
          <View>
            <Text style={styles.headerTitle}>Our Services</Text>
            <Text style={styles.headerSubtitle}>Premium treatments for you</Text>
          </View>
          {cartCount > 0 && (
            <TouchableOpacity 
              style={styles.cartButton}
              onPress={() => router.push('/cart')}
            >
              <Ionicons name="cart" size={22} color="#D4AF37" />
              <View style={styles.cartBadge}>
                <Text style={styles.cartBadgeText}>{cartCount}</Text>
              </View>
            </TouchableOpacity>
          )}
        </View>
      </Animated.View>

      {/* Search Bar */}
      <Animated.View entering={FadeIn.delay(100).duration(300)} style={styles.searchContainer}>
        <Ionicons name="search" size={20} color="rgba(255,255,255,0.5)" />
        <TextInput
          style={styles.searchInput}
          placeholder="Search services..."
          placeholderTextColor="rgba(255,255,255,0.4)"
          value={searchQuery}
          onChangeText={setSearchQuery}
        />
        {searchQuery.length > 0 && (
          <TouchableOpacity onPress={() => setSearchQuery('')}>
            <Ionicons name="close-circle" size={20} color="rgba(255,255,255,0.5)" />
          </TouchableOpacity>
        )}
      </Animated.View>

      {/* Category Filters - Always Visible */}
      <Animated.View entering={FadeIn.delay(200).duration(300)} style={styles.categoriesWrapper}>
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.categoriesContent}
        >
          {CATEGORIES.map((category, index) => (
            <Animated.View 
              key={category.id} 
              entering={SlideInRight.delay(index * 50).duration(300)}
            >
              <TouchableOpacity
                style={[
                  styles.categoryChip,
                  selectedCategory === category.id && styles.categoryChipActive,
                ]}
                onPress={() => setSelectedCategory(category.id)}
                activeOpacity={0.7}
              >
                <Ionicons
                  name={category.icon as any}
                  size={18}
                  color={selectedCategory === category.id ? '#0A0A0A' : '#D4AF37'}
                />
                <Text
                  style={[
                    styles.categoryChipText,
                    selectedCategory === category.id && styles.categoryChipTextActive,
                  ]}
                >
                  {category.label}
                </Text>
              </TouchableOpacity>
            </Animated.View>
          ))}
        </ScrollView>
      </Animated.View>

      {/* Results Count */}
      <View style={styles.resultsBar}>
        <Text style={styles.resultsText}>
          {filteredServices.length} {filteredServices.length === 1 ? 'service' : 'services'} found
        </Text>
      </View>

      {/* Services List */}
      {loading ? (
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color="#D4AF37" />
          <Text style={styles.loadingText}>Loading services...</Text>
        </View>
      ) : (
        <ScrollView
          showsVerticalScrollIndicator={false}
          contentContainerStyle={styles.servicesContent}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              tintColor="#D4AF37"
              colors={['#D4AF37']}
            />
          }
        >
          {filteredServices.length === 0 ? (
            <View style={styles.emptyContainer}>
              <Ionicons name="search" size={48} color="rgba(255,255,255,0.2)" />
              <Text style={styles.emptyText}>No services found</Text>
              <Text style={styles.emptySubtext}>Try a different search or category</Text>
            </View>
          ) : (
            filteredServices.map((service, index) => {
              const inCart = isInCart(service.id);
              return (
                <Animated.View
                  key={service.id}
                  entering={FadeInDown.delay(index * 30).duration(300)}
                  layout={Layout.springify()}
                >
                  <TouchableOpacity
                    style={styles.serviceCard}
                    onPress={() => router.push({ pathname: '/service-details', params: { serviceId: service.id } })}
                    activeOpacity={0.8}
                  >
                    <View style={styles.serviceIconContainer}>
                      <Ionicons
                        name={getCategoryIcon(service.category) as any}
                        size={24}
                        color="#D4AF37"
                      />
                    </View>
                    <View style={styles.serviceInfo}>
                      <Text style={styles.serviceName}>{service.name}</Text>
                      <Text style={styles.serviceDescription} numberOfLines={2}>
                        {service.description}
                      </Text>
                      <View style={styles.serviceMeta}>
                        <View style={styles.serviceMetaItem}>
                          <Ionicons name="time-outline" size={14} color="#D4AF37" />
                          <Text style={styles.serviceMetaText}>
                            {service.duration_minutes} min
                          </Text>
                        </View>
                        <View style={styles.serviceMetaItem}>
                          <Ionicons name="pricetag-outline" size={14} color="#D4AF37" />
                          <Text style={styles.serviceMetaText}>{service.price_range}</Text>
                        </View>
                      </View>
                    </View>
                    <TouchableOpacity
                      style={[styles.addButton, inCart && styles.addButtonActive]}
                      onPress={(e) => {
                        e.stopPropagation();
                        handleAddToCart(service);
                      }}
                    >
                      <Ionicons 
                        name={inCart ? "checkmark" : "add"} 
                        size={20} 
                        color={inCart ? "#FFFFFF" : "#0A0A0A"} 
                      />
                    </TouchableOpacity>
                  </TouchableOpacity>
                </Animated.View>
              );
            })
          )}
        </ScrollView>
      )}

      {/* Floating Cart Button */}
      {cartCount > 0 && (
        <Animated.View 
          entering={FadeInDown.duration(300)}
          style={[styles.floatingCart, { bottom: insets.bottom + 80 }]}
        >
          <TouchableOpacity 
            style={styles.floatingCartButton}
            onPress={() => router.push('/cart')}
          >
            <View style={styles.floatingCartLeft}>
              <Ionicons name="cart" size={20} color="#0A0A0A" />
              <Text style={styles.floatingCartText}>{cartCount} items in cart</Text>
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
    backgroundColor: '#0A0A0A',
  },
  header: {
    paddingHorizontal: 20,
    paddingTop: 8,
    paddingBottom: 12,
  },
  headerTop: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
  },
  headerTitle: {
    fontSize: 28,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  headerSubtitle: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.5)',
    marginTop: 4,
  },
  cartButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: 'rgba(212, 175, 55, 0.15)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  cartBadge: {
    position: 'absolute',
    top: -2,
    right: -2,
    backgroundColor: '#E74C3C',
    width: 18,
    height: 18,
    borderRadius: 9,
    justifyContent: 'center',
    alignItems: 'center',
  },
  cartBadgeText: {
    fontSize: 10,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  searchContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255, 255, 255, 0.08)',
    marginHorizontal: 20,
    paddingHorizontal: 16,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.1)',
  },
  searchInput: {
    flex: 1,
    paddingVertical: 12,
    marginLeft: 10,
    fontSize: 16,
    color: '#FFFFFF',
  },
  categoriesWrapper: {
    marginTop: 16,
    minHeight: 50,
  },
  categoriesContent: {
    paddingHorizontal: 20,
    paddingVertical: 4,
    flexDirection: 'row',
    alignItems: 'center',
  },
  categoryChip: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 24,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    borderWidth: 1,
    borderColor: 'rgba(212, 175, 55, 0.3)',
    marginRight: 10,
    gap: 6,
  },
  categoryChipActive: {
    backgroundColor: '#D4AF37',
    borderColor: '#D4AF37',
  },
  categoryChipText: {
    fontSize: 14,
    color: '#D4AF37',
    fontWeight: '600',
  },
  categoryChipTextActive: {
    color: '#0A0A0A',
  },
  resultsBar: {
    paddingHorizontal: 20,
    paddingVertical: 12,
  },
  resultsText: {
    fontSize: 13,
    color: 'rgba(255, 255, 255, 0.5)',
  },
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    gap: 12,
  },
  loadingText: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.5)',
  },
  servicesContent: {
    paddingHorizontal: 20,
    paddingBottom: 180,
  },
  emptyContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingVertical: 60,
    gap: 8,
  },
  emptyText: {
    fontSize: 18,
    fontWeight: '600',
    color: '#FFFFFF',
    marginTop: 12,
  },
  emptySubtext: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.5)',
  },
  serviceCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.08)',
  },
  serviceIconContainer: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  serviceInfo: {
    flex: 1,
    marginLeft: 14,
    marginRight: 10,
  },
  serviceName: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  serviceDescription: {
    fontSize: 13,
    color: 'rgba(255, 255, 255, 0.5)',
    marginTop: 4,
    lineHeight: 18,
  },
  serviceMeta: {
    flexDirection: 'row',
    marginTop: 8,
    gap: 16,
  },
  serviceMetaItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  serviceMetaText: {
    fontSize: 12,
    color: 'rgba(255, 255, 255, 0.6)',
  },
  addButton: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: '#D4AF37',
    justifyContent: 'center',
    alignItems: 'center',
  },
  addButtonActive: {
    backgroundColor: '#2ECC71',
  },
  floatingCart: {
    position: 'absolute',
    left: 20,
    right: 20,
  },
  floatingCartButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#D4AF37',
    paddingVertical: 14,
    paddingHorizontal: 20,
    borderRadius: 28,
    shadowColor: '#D4AF37',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
    elevation: 8,
  },
  floatingCartLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  floatingCartText: {
    fontSize: 15,
    fontWeight: '600',
    color: '#0A0A0A',
  },
  floatingCartAction: {
    fontSize: 14,
    fontWeight: '600',
    color: '#0A0A0A',
  },
});
