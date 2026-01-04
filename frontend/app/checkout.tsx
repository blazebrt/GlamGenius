import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  Alert,
  TextInput,
} from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeIn, FadeInDown, FadeInUp } from 'react-native-reanimated';
import { api } from '../src/services/api';
import { useCartStore } from '../src/store/cartStore';
import { useUserStore } from '../src/store/userStore';

const PAYMENT_METHODS = [
  { id: 'upi', label: 'UPI (GPay, PhonePe, Paytm)', icon: 'phone-portrait-outline', popular: true },
  { id: 'card', label: 'Credit/Debit Card', icon: 'card-outline', popular: false },
  { id: 'netbanking', label: 'Net Banking', icon: 'globe-outline', popular: false },
  { id: 'cod', label: 'Pay at Salon', icon: 'cash-outline', popular: false },
];

type CheckoutStep = 'details' | 'payment' | 'processing' | 'success';

export default function CheckoutScreen() {
  const router = useRouter();
  const params = useLocalSearchParams();
  const insets = useSafeAreaInsets();
  const { items, getTotal, clearCart } = useCartStore();
  const { userId, user } = useUserStore();

  const [step, setStep] = useState<CheckoutStep>('details');
  const [selectedPayment, setSelectedPayment] = useState('upi');
  const [customerName, setCustomerName] = useState(user?.name || '');
  const [customerPhone, setCustomerPhone] = useState('');
  const [orderId, setOrderId] = useState('');
  const [bookingId, setBookingId] = useState('');
  const [processing, setProcessing] = useState(false);

  const total = getTotal();
  const totalWithTax = Math.round(total * 1.18); // Adding 18% GST

  const handleProceedToPayment = () => {
    if (!customerName.trim()) {
      Alert.alert('Required', 'Please enter your name');
      return;
    }
    if (!customerPhone.trim() || customerPhone.length < 10) {
      Alert.alert('Required', 'Please enter a valid phone number');
      return;
    }
    setStep('payment');
  };

  const handlePayment = async () => {
    setStep('processing');
    setProcessing(true);

    try {
      // Step 1: Create order
      const orderResponse = await api.post('/payments/create-order', {
        user_id: userId,
        amount: totalWithTax * 100, // Convert to paise
        currency: 'INR',
        items: items.map(item => ({
          id: item.id,
          name: item.name,
          price: item.price,
          quantity: item.quantity,
          type: item.type,
        })),
      });

      setOrderId(orderResponse.data.id);

      // Simulate payment processing delay (2-3 seconds)
      await new Promise(resolve => setTimeout(resolve, 2000 + Math.random() * 1000));

      // Step 2: Verify payment (mock - always succeeds)
      const verifyResponse = await api.post('/payments/verify', {
        order_id: orderResponse.data.id,
        payment_id: `pay_mock_${Date.now()}`,
        payment_method: selectedPayment,
      });

      if (verifyResponse.data.success) {
        setBookingId(verifyResponse.data.booking_id);
        setStep('success');
        clearCart();
      } else {
        throw new Error('Payment verification failed');
      }
    } catch (error) {
      console.error('Payment error:', error);
      Alert.alert(
        'Payment Failed',
        'There was an issue processing your payment. Please try again.',
        [{ text: 'OK', onPress: () => setStep('payment') }]
      );
    } finally {
      setProcessing(false);
    }
  };

  const handleGoHome = () => {
    router.replace('/(tabs)/home');
  };

  // Success Screen
  if (step === 'success') {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <Animated.View entering={FadeIn} style={styles.successContainer}>
          <Animated.View entering={FadeInDown.delay(200)} style={styles.successIcon}>
            <Ionicons name="checkmark-circle" size={80} color="#2ECC71" />
          </Animated.View>
          
          <Animated.Text entering={FadeInDown.delay(300)} style={styles.successTitle}>
            Booking Confirmed! 🎉
          </Animated.Text>
          
          <Animated.Text entering={FadeInDown.delay(400)} style={styles.successText}>
            Your appointment has been successfully booked. You will receive a confirmation SMS shortly.
          </Animated.Text>

          <Animated.View entering={FadeInDown.delay(500)} style={styles.bookingDetails}>
            <View style={styles.bookingRow}>
              <Text style={styles.bookingLabel}>Booking ID</Text>
              <Text style={styles.bookingValue}>{bookingId.slice(0, 12)}...</Text>
            </View>
            <View style={styles.bookingRow}>
              <Text style={styles.bookingLabel}>Amount Paid</Text>
              <Text style={styles.bookingValue}>₹{totalWithTax.toLocaleString('en-IN')}</Text>
            </View>
            <View style={styles.bookingRow}>
              <Text style={styles.bookingLabel}>Payment Method</Text>
              <Text style={styles.bookingValue}>{selectedPayment.toUpperCase()}</Text>
            </View>
          </Animated.View>

          <Animated.View entering={FadeInUp.delay(600)} style={styles.successActions}>
            <TouchableOpacity style={styles.primaryButton} onPress={handleGoHome}>
              <Ionicons name="home" size={20} color="#0A0A0A" />
              <Text style={styles.primaryButtonText}>Go to Home</Text>
            </TouchableOpacity>
          </Animated.View>
        </Animated.View>
      </View>
    );
  }

  // Processing Screen
  if (step === 'processing') {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.processingContainer}>
          <Animated.View entering={FadeIn} style={styles.processingIcon}>
            <ActivityIndicator size="large" color="#D4AF37" />
          </Animated.View>
          <Text style={styles.processingTitle}>Processing Payment</Text>
          <Text style={styles.processingText}>
            Please wait while we confirm your payment...
          </Text>
          <Text style={styles.processingNote}>
            Do not close this screen or press back
          </Text>
        </View>
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity onPress={() => step === 'payment' ? setStep('details') : router.back()} style={styles.backButton}>
          <Ionicons name="arrow-back" size={24} color="#FFFFFF" />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>
          {step === 'details' ? 'Checkout' : 'Payment'}
        </Text>
        <View style={{ width: 40 }} />
      </View>

      {/* Progress Indicator */}
      <View style={styles.progressContainer}>
        <View style={[styles.progressStep, step === 'details' && styles.progressStepActive]}>
          <View style={[styles.progressDot, step !== 'details' && styles.progressDotDone]}>
            {step !== 'details' ? (
              <Ionicons name="checkmark" size={14} color="#0A0A0A" />
            ) : (
              <Text style={styles.progressDotText}>1</Text>
            )}
          </View>
          <Text style={styles.progressLabel}>Details</Text>
        </View>
        <View style={styles.progressLine} />
        <View style={[styles.progressStep, step === 'payment' && styles.progressStepActive]}>
          <View style={[styles.progressDot, step === 'payment' && styles.progressDotActive]}>
            <Text style={styles.progressDotText}>2</Text>
          </View>
          <Text style={styles.progressLabel}>Payment</Text>
        </View>
      </View>

      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.scrollContent}>
        {step === 'details' && (
          <Animated.View entering={FadeIn}>
            {/* Customer Details */}
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>Your Details</Text>
              
              <View style={styles.inputContainer}>
                <Ionicons name="person-outline" size={20} color="rgba(255,255,255,0.5)" />
                <TextInput
                  style={styles.input}
                  placeholder="Full Name *"
                  placeholderTextColor="rgba(255,255,255,0.4)"
                  value={customerName}
                  onChangeText={setCustomerName}
                />
              </View>

              <View style={styles.inputContainer}>
                <Ionicons name="call-outline" size={20} color="rgba(255,255,255,0.5)" />
                <TextInput
                  style={styles.input}
                  placeholder="Phone Number *"
                  placeholderTextColor="rgba(255,255,255,0.4)"
                  value={customerPhone}
                  onChangeText={setCustomerPhone}
                  keyboardType="phone-pad"
                  maxLength={10}
                />
              </View>
            </View>

            {/* Order Summary */}
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>Order Summary</Text>
              
              {items.map((item, index) => (
                <View key={item.id} style={styles.orderItem}>
                  <View style={styles.orderItemInfo}>
                    <Text style={styles.orderItemName}>{item.name}</Text>
                    <Text style={styles.orderItemQty}>x{item.quantity}</Text>
                  </View>
                  <Text style={styles.orderItemPrice}>₹{(item.price * item.quantity).toLocaleString('en-IN')}</Text>
                </View>
              ))}

              <View style={styles.divider} />
              
              <View style={styles.summaryRow}>
                <Text style={styles.summaryLabel}>Subtotal</Text>
                <Text style={styles.summaryValue}>₹{total.toLocaleString('en-IN')}</Text>
              </View>
              <View style={styles.summaryRow}>
                <Text style={styles.summaryLabel}>GST (18%)</Text>
                <Text style={styles.summaryValue}>₹{Math.round(total * 0.18).toLocaleString('en-IN')}</Text>
              </View>
              <View style={styles.divider} />
              <View style={styles.summaryRow}>
                <Text style={styles.totalLabel}>Total</Text>
                <Text style={styles.totalValue}>₹{totalWithTax.toLocaleString('en-IN')}</Text>
              </View>
            </View>
          </Animated.View>
        )}

        {step === 'payment' && (
          <Animated.View entering={FadeIn}>
            {/* Amount to Pay */}
            <View style={styles.amountCard}>
              <Text style={styles.amountLabel}>Amount to Pay</Text>
              <Text style={styles.amountValue}>₹{totalWithTax.toLocaleString('en-IN')}</Text>
            </View>

            {/* Payment Methods */}
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>Select Payment Method</Text>
              
              {PAYMENT_METHODS.map((method) => (
                <TouchableOpacity
                  key={method.id}
                  style={[
                    styles.paymentOption,
                    selectedPayment === method.id && styles.paymentOptionSelected,
                  ]}
                  onPress={() => setSelectedPayment(method.id)}
                >
                  <View style={styles.paymentOptionLeft}>
                    <Ionicons
                      name={method.icon as any}
                      size={24}
                      color={selectedPayment === method.id ? '#D4AF37' : 'rgba(255,255,255,0.6)'}
                    />
                    <View style={styles.paymentLabelContainer}>
                      <Text
                        style={[
                          styles.paymentLabel,
                          selectedPayment === method.id && styles.paymentLabelSelected,
                        ]}
                      >
                        {method.label}
                      </Text>
                      {method.popular && (
                        <View style={styles.popularBadge}>
                          <Text style={styles.popularBadgeText}>Popular</Text>
                        </View>
                      )}
                    </View>
                  </View>
                  {selectedPayment === method.id ? (
                    <Ionicons name="checkmark-circle" size={24} color="#D4AF37" />
                  ) : (
                    <Ionicons name="ellipse-outline" size={24} color="rgba(255,255,255,0.3)" />
                  )}
                </TouchableOpacity>
              ))}
            </View>

            {/* Security Note */}
            <View style={styles.securityNote}>
              <Ionicons name="shield-checkmark" size={18} color="#2ECC71" />
              <Text style={styles.securityNoteText}>
                Your payment is 100% secure. We use industry-standard encryption.
              </Text>
            </View>

            {/* Mock Notice */}
            <View style={styles.mockNotice}>
              <Ionicons name="information-circle" size={18} color="#F39C12" />
              <Text style={styles.mockNoticeText}>
                Demo Mode: This is a simulated payment flow. No actual charges will be made.
              </Text>
            </View>
          </Animated.View>
        )}
      </ScrollView>

      {/* Bottom Action Button */}
      <View style={[styles.bottomContainer, { paddingBottom: insets.bottom + 16 }]}>
        {step === 'details' && (
          <TouchableOpacity style={styles.primaryButton} onPress={handleProceedToPayment}>
            <Text style={styles.primaryButtonText}>Proceed to Payment</Text>
            <Ionicons name="arrow-forward" size={20} color="#0A0A0A" />
          </TouchableOpacity>
        )}
        {step === 'payment' && (
          <TouchableOpacity style={styles.primaryButton} onPress={handlePayment}>
            <Text style={styles.primaryButtonText}>Pay ₹{totalWithTax.toLocaleString('en-IN')}</Text>
            <Ionicons name="lock-closed" size={20} color="#0A0A0A" />
          </TouchableOpacity>
        )}
      </View>
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
  backButton: {
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
  progressContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 40,
    paddingVertical: 16,
  },
  progressStep: {
    alignItems: 'center',
  },
  progressStepActive: {},
  progressDot: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: 'rgba(255,255,255,0.1)',
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 2,
    borderColor: 'rgba(255,255,255,0.2)',
  },
  progressDotActive: {
    backgroundColor: '#D4AF37',
    borderColor: '#D4AF37',
  },
  progressDotDone: {
    backgroundColor: '#D4AF37',
    borderColor: '#D4AF37',
  },
  progressDotText: {
    fontSize: 12,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  progressLabel: {
    fontSize: 11,
    color: 'rgba(255,255,255,0.5)',
    marginTop: 6,
  },
  progressLine: {
    flex: 1,
    height: 2,
    backgroundColor: 'rgba(255,255,255,0.1)',
    marginHorizontal: 12,
  },
  scrollContent: {
    paddingHorizontal: 20,
    paddingBottom: 120,
  },
  section: {
    marginBottom: 24,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
    marginBottom: 16,
  },
  inputContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 12,
    paddingHorizontal: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: 'rgba(255, 255, 255, 0.1)',
  },
  input: {
    flex: 1,
    paddingVertical: 16,
    marginLeft: 12,
    fontSize: 16,
    color: '#FFFFFF',
  },
  orderItem: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 10,
  },
  orderItemInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    flex: 1,
  },
  orderItemName: {
    fontSize: 14,
    color: '#FFFFFF',
    flex: 1,
  },
  orderItemQty: {
    fontSize: 12,
    color: 'rgba(255,255,255,0.5)',
    marginLeft: 8,
  },
  orderItemPrice: {
    fontSize: 14,
    fontWeight: '600',
    color: '#D4AF37',
  },
  divider: {
    height: 1,
    backgroundColor: 'rgba(255,255,255,0.1)',
    marginVertical: 12,
  },
  summaryRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 8,
  },
  summaryLabel: {
    fontSize: 14,
    color: 'rgba(255,255,255,0.6)',
  },
  summaryValue: {
    fontSize: 14,
    color: '#FFFFFF',
  },
  totalLabel: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  totalValue: {
    fontSize: 18,
    fontWeight: '700',
    color: '#D4AF37',
  },
  amountCard: {
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
    borderRadius: 16,
    padding: 20,
    alignItems: 'center',
    marginBottom: 24,
    borderWidth: 1,
    borderColor: 'rgba(212, 175, 55, 0.3)',
  },
  amountLabel: {
    fontSize: 14,
    color: 'rgba(255,255,255,0.6)',
  },
  amountValue: {
    fontSize: 32,
    fontWeight: '700',
    color: '#D4AF37',
    marginTop: 4,
  },
  paymentOption: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 12,
    padding: 16,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.1)',
  },
  paymentOptionSelected: {
    borderColor: '#D4AF37',
    backgroundColor: 'rgba(212, 175, 55, 0.1)',
  },
  paymentOptionLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    flex: 1,
  },
  paymentLabelContainer: {
    marginLeft: 14,
  },
  paymentLabel: {
    fontSize: 15,
    color: 'rgba(255,255,255,0.7)',
  },
  paymentLabelSelected: {
    color: '#FFFFFF',
    fontWeight: '500',
  },
  popularBadge: {
    backgroundColor: 'rgba(46, 204, 113, 0.2)',
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 8,
    marginTop: 4,
    alignSelf: 'flex-start',
  },
  popularBadgeText: {
    fontSize: 10,
    color: '#2ECC71',
    fontWeight: '600',
  },
  securityNote: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(46, 204, 113, 0.1)',
    borderRadius: 12,
    padding: 14,
    gap: 10,
    marginBottom: 12,
  },
  securityNoteText: {
    flex: 1,
    fontSize: 12,
    color: 'rgba(255,255,255,0.7)',
    lineHeight: 18,
  },
  mockNotice: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(243, 156, 18, 0.1)',
    borderRadius: 12,
    padding: 14,
    gap: 10,
  },
  mockNoticeText: {
    flex: 1,
    fontSize: 12,
    color: 'rgba(255,255,255,0.7)',
    lineHeight: 18,
  },
  bottomContainer: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    paddingHorizontal: 20,
    paddingTop: 16,
    backgroundColor: '#121212',
    borderTopWidth: 1,
    borderTopColor: 'rgba(255,255,255,0.1)',
  },
  primaryButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#D4AF37',
    paddingVertical: 16,
    borderRadius: 28,
    gap: 10,
  },
  primaryButtonText: {
    fontSize: 16,
    fontWeight: '600',
    color: '#0A0A0A',
  },
  processingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 40,
  },
  processingIcon: {
    marginBottom: 24,
  },
  processingTitle: {
    fontSize: 22,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  processingText: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.6)',
    textAlign: 'center',
    marginTop: 8,
  },
  processingNote: {
    fontSize: 12,
    color: '#E74C3C',
    marginTop: 24,
  },
  successContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 30,
  },
  successIcon: {
    marginBottom: 24,
  },
  successTitle: {
    fontSize: 26,
    fontWeight: '700',
    color: '#FFFFFF',
    textAlign: 'center',
  },
  successText: {
    fontSize: 14,
    color: 'rgba(255, 255, 255, 0.6)',
    textAlign: 'center',
    marginTop: 12,
    lineHeight: 22,
  },
  bookingDetails: {
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 20,
    marginTop: 24,
    width: '100%',
  },
  bookingRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 12,
  },
  bookingLabel: {
    fontSize: 13,
    color: 'rgba(255,255,255,0.5)',
  },
  bookingValue: {
    fontSize: 14,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  successActions: {
    marginTop: 32,
    width: '100%',
  },
});
