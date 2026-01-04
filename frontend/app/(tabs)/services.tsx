import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  TextInput,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeInDown } from 'react-native-reanimated';
import { api } from '../../src/services/api';

const CATEGORIES = ['All', 'Hair', 'Skin', 'Makeup', 'Nails'];

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
  const [services, setServices] = useState<Service[]>([]);
  const [filteredServices, setFilteredServices] = useState<Service[]>([]);
  const [selectedCategory, setSelectedCategory] = useState('All');
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    loadServices();
  }, []);

  useEffect(() => {
    filterServices();
  }, [selectedCategory, searchQuery, services]);

  const loadServices = async () => {
    try {
      const response = await api.get('/services');
      setServices(response.data);
      setFilteredServices(response.data);
    } catch (error) {
      console.error('Error loading services:', error);
    }
  };

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
    switch (category) {
      case 'Hair':
        return 'cut';
      case 'Skin':
        return 'flower';
      case 'Makeup':
        return 'color-palette';
      case 'Nails':
        return 'hand-left';
      default:
        return 'grid';
    }
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Our Services</Text>
        <Text style={styles.headerSubtitle}>Premium treatments for you</Text>
      </View>

      {/* Search Bar */}
      <View style={styles.searchContainer}>
        <Ionicons name="search" size={20} color="rgba(255,255,255,0.5)" />
        <TextInput
          style={styles.searchInput}
          placeholder="Search services..."
          placeholderTextColor="rgba(255,255,255,0.4)"
          value={searchQuery}
          onChangeText={setSearchQuery}
        />
      </View>

      {/* Category Filters */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.categoriesContainer}
        contentContainerStyle={styles.categoriesContent}
      >
        {CATEGORIES.map((category) => (
          <TouchableOpacity
            key={category}
            style={[
              styles.categoryChip,
              selectedCategory === category && styles.categoryChipActive,
            ]}
            onPress={() => setSelectedCategory(category)}
          >
            <Ionicons
              name={getCategoryIcon(category) as any}
              size={16}
              color={selectedCategory === category ? '#0A0A0A' : '#D4AF37'}
            />
            <Text
              style={[
                styles.categoryChipText,
                selectedCategory === category && styles.categoryChipTextActive,
              ]}
            >
              {category}
            </Text>
          </TouchableOpacity>
        ))}
      </ScrollView>

      {/* Services List */}
      <ScrollView
        showsVerticalScrollIndicator={false}
        contentContainerStyle={styles.servicesContent}
      >
        {filteredServices.map((service, index) => (
          <Animated.View
            key={service.id}
            entering={FadeInDown.delay(index * 50)}
          >
            <TouchableOpacity
              style={styles.serviceCard}
              onPress={() => router.push({ pathname: '/service-details', params: { serviceId: service.id } })}
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
              <Ionicons name="chevron-forward" size={20} color="rgba(255,255,255,0.3)" />
            </TouchableOpacity>
          </Animated.View>
        ))}
      </ScrollView>
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
    paddingVertical: 16,
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
  categoriesContainer: {
    marginTop: 16,
    maxHeight: 50,
  },
  categoriesContent: {
    paddingHorizontal: 20,
    gap: 10,
  },
  categoryChip: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 20,
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
    fontWeight: '500',
  },
  categoryChipTextActive: {
    color: '#0A0A0A',
  },
  servicesContent: {
    paddingHorizontal: 20,
    paddingTop: 20,
    paddingBottom: 100,
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
});
