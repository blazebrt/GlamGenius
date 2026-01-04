import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  TextInput,
  Alert,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useUserStore } from '../../src/store/userStore';

export default function ProfileScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, updateUser, userId, setUserId } = useUserStore();
  const [isEditing, setIsEditing] = useState(false);
  const [name, setName] = useState('');
  const [age, setAge] = useState('');

  useEffect(() => {
    if (user) {
      setName(user.name || '');
      setAge(user.age?.toString() || '');
    }
  }, [user]);

  const handleSave = async () => {
    await updateUser({
      name,
      age: age ? parseInt(age) : undefined,
    });
    setIsEditing(false);
  };

  const handleLogout = () => {
    Alert.alert(
      'Reset Profile',
      'This will clear your profile data. Are you sure?',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Reset',
          style: 'destructive',
          onPress: async () => {
            await AsyncStorage.removeItem('glamgenius_user_id');
            setUserId('');
            router.replace('/');
          },
        },
      ]
    );
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.scrollContent}>
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.headerTitle}>My Profile</Text>
          <TouchableOpacity
            style={styles.editButton}
            onPress={() => (isEditing ? handleSave() : setIsEditing(true))}
          >
            <Text style={styles.editButtonText}>{isEditing ? 'Save' : 'Edit'}</Text>
          </TouchableOpacity>
        </View>

        {/* Avatar Section */}
        <View style={styles.avatarSection}>
          <View style={styles.avatar}>
            <Ionicons name="person" size={48} color="#D4AF37" />
          </View>
          {isEditing ? (
            <TextInput
              style={styles.nameInput}
              value={name}
              onChangeText={setName}
              placeholder="Your Name"
              placeholderTextColor="rgba(255,255,255,0.4)"
            />
          ) : (
            <Text style={styles.userName}>{user?.name || 'Guest User'}</Text>
          )}
          <Text style={styles.userSubtitle}>Beauty Enthusiast</Text>
        </View>

        {/* Profile Info */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Personal Info</Text>
          
          <View style={styles.infoCard}>
            <View style={styles.infoRow}>
              <View style={styles.infoIcon}>
                <Ionicons name="calendar-outline" size={20} color="#D4AF37" />
              </View>
              <Text style={styles.infoLabel}>Age</Text>
              {isEditing ? (
                <TextInput
                  style={styles.infoInput}
                  value={age}
                  onChangeText={setAge}
                  keyboardType="numeric"
                  placeholder="Enter age"
                  placeholderTextColor="rgba(255,255,255,0.4)"
                />
              ) : (
                <Text style={styles.infoValue}>{user?.age || 'Not set'}</Text>
              )}
            </View>

            <View style={styles.divider} />

            <View style={styles.infoRow}>
              <View style={styles.infoIcon}>
                <Ionicons name="wallet-outline" size={20} color="#D4AF37" />
              </View>
              <Text style={styles.infoLabel}>Budget</Text>
              <Text style={styles.infoValue}>
                {user?.budget_range ? user.budget_range.charAt(0).toUpperCase() + user.budget_range.slice(1) : 'Not set'}
              </Text>
            </View>
          </View>
        </View>

        {/* Beauty Profile */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Beauty Profile</Text>
          
          <View style={styles.infoCard}>
            <View style={styles.infoRow}>
              <View style={styles.infoIcon}>
                <Ionicons name="happy-outline" size={20} color="#D4AF37" />
              </View>
              <Text style={styles.infoLabel}>Face Shape</Text>
              <Text style={styles.infoValue}>
                {user?.face_shape ? user.face_shape.charAt(0).toUpperCase() + user.face_shape.slice(1) : 'Take a scan'}
              </Text>
            </View>

            <View style={styles.divider} />

            <View style={styles.infoRow}>
              <View style={styles.infoIcon}>
                <Ionicons name="water-outline" size={20} color="#D4AF37" />
              </View>
              <Text style={styles.infoLabel}>Skin Type</Text>
              <Text style={styles.infoValue}>
                {user?.skin_type ? user.skin_type.charAt(0).toUpperCase() + user.skin_type.slice(1) : 'Take a scan'}
              </Text>
            </View>

            <View style={styles.divider} />

            <View style={styles.infoRow}>
              <View style={styles.infoIcon}>
                <Ionicons name="leaf-outline" size={20} color="#D4AF37" />
              </View>
              <Text style={styles.infoLabel}>Hair Type</Text>
              <Text style={styles.infoValue}>
                {user?.hair_type ? user.hair_type.charAt(0).toUpperCase() + user.hair_type.slice(1) : 'Take a scan'}
              </Text>
            </View>
          </View>
        </View>

        {/* Concerns */}
        {(user?.skin_concerns?.length > 0 || user?.hair_concerns?.length > 0) && (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Concerns</Text>
            <View style={styles.tagsContainer}>
              {user?.skin_concerns?.map((concern: string, index: number) => (
                <View key={`skin-${index}`} style={styles.tag}>
                  <Text style={styles.tagText}>Skin: {concern}</Text>
                </View>
              ))}
              {user?.hair_concerns?.map((concern: string, index: number) => (
                <View key={`hair-${index}`} style={styles.tag}>
                  <Text style={styles.tagText}>Hair: {concern}</Text>
                </View>
              ))}
            </View>
          </View>
        )}

        {/* Actions */}
        <View style={styles.section}>
          <TouchableOpacity style={styles.actionButton} onPress={() => router.push('/scan')}>
            <Ionicons name="scan-outline" size={22} color="#D4AF37" />
            <Text style={styles.actionButtonText}>Update Beauty Profile</Text>
            <Ionicons name="chevron-forward" size={20} color="rgba(255,255,255,0.3)" />
          </TouchableOpacity>

          <TouchableOpacity style={styles.actionButton} onPress={() => router.push('/quiz')}>
            <Ionicons name="help-circle-outline" size={22} color="#D4AF37" />
            <Text style={styles.actionButtonText}>Retake Style Quiz</Text>
            <Ionicons name="chevron-forward" size={20} color="rgba(255,255,255,0.3)" />
          </TouchableOpacity>

          <TouchableOpacity style={[styles.actionButton, styles.dangerButton]} onPress={handleLogout}>
            <Ionicons name="log-out-outline" size={22} color="#E74C3C" />
            <Text style={[styles.actionButtonText, { color: '#E74C3C' }]}>Reset Profile</Text>
            <Ionicons name="chevron-forward" size={20} color="rgba(255,255,255,0.3)" />
          </TouchableOpacity>
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0A0A0A',
  },
  scrollContent: {
    paddingHorizontal: 20,
    paddingBottom: 100,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 16,
  },
  headerTitle: {
    fontSize: 28,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  editButton: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 20,
    backgroundColor: 'rgba(212, 175, 55, 0.2)',
  },
  editButtonText: {
    fontSize: 14,
    fontWeight: '600',
    color: '#D4AF37',
  },
  avatarSection: {
    alignItems: 'center',
    paddingVertical: 24,
  },
  avatar: {
    width: 100,
    height: 100,
    borderRadius: 50,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 2,
    borderColor: 'rgba(212, 175, 55, 0.3)',
  },
  userName: {
    fontSize: 24,
    fontWeight: '600',
    color: '#FFFFFF',
    marginTop: 16,
  },
  nameInput: {
    fontSize: 24,
    fontWeight: '600',
    color: '#FFFFFF',
    marginTop: 16,
    textAlign: 'center',
    borderBottomWidth: 1,
    borderBottomColor: '#D4AF37',
    paddingBottom: 4,
    minWidth: 150,
  },
  userSubtitle: {
    fontSize: 14,
    color: '#D4AF37',
    marginTop: 4,
  },
  section: {
    marginTop: 24,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: 'rgba(255, 255, 255, 0.6)',
    marginBottom: 12,
  },
  infoCard: {
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 16,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.08)',
  },
  infoRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  infoIcon: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  infoLabel: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.6)',
    marginLeft: 12,
    flex: 1,
  },
  infoValue: {
    fontSize: 14,
    fontWeight: '500',
    color: '#FFFFFF',
  },
  infoInput: {
    fontSize: 14,
    fontWeight: '500',
    color: '#FFFFFF',
    borderBottomWidth: 1,
    borderBottomColor: '#D4AF37',
    paddingBottom: 4,
    minWidth: 80,
    textAlign: 'right',
  },
  divider: {
    height: 1,
    backgroundColor: 'rgba(255, 255, 255, 0.08)',
    marginVertical: 12,
  },
  tagsContainer: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  tag: {
    backgroundColor: 'rgba(212, 175, 55, 0.15)',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
  },
  tagText: {
    fontSize: 12,
    color: '#D4AF37',
    textTransform: 'capitalize',
  },
  actionButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 12,
    padding: 16,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.08)',
  },
  actionButtonText: {
    flex: 1,
    fontSize: 15,
    color: '#FFFFFF',
    marginLeft: 12,
  },
  dangerButton: {
    borderColor: 'rgba(231, 76, 60, 0.3)',
  },
});
