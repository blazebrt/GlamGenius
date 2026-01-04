import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  Alert,
  TextInput,
  ActivityIndicator,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Animated, { FadeInDown, FadeIn, FadeInUp } from 'react-native-reanimated';
import { useCartStore } from '../src/store/cartStore';
import { useUserStore } from '../src/store/userStore';
import { api } from '../src/services/api';

const PAYMENT_METHODS = [
  { id: 'upi', label: 'UPI (GPay, PhonePe, Paytm)', icon: 'phone-portrait-outline', popular: true },
  { id: 'card', label: 'Credit/Debit Card', icon: 'card-outline', popular: false },
  { id: 'netbanking', label: 'Net Banking', icon: 'globe-outline', popular: false },
  { id: 'cod', label: 'Pay at Salon', icon: 'cash-outline', popular: false },
];

type CartStep = 'cart' | 'details' | 'payment' | 'processing' | 'success';

export default function CartScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { items, removeItem, updateQuantity, getTotal, clearCart, loadCart } = useCartStore();
  const { userId, user } = useUserStore();

  const [step, setStep] = useState<CartStep>('cart');
  const [selectedPayment, setSelectedPayment] = useState('upi');
  const [customerName, setCustomerName] = useState(user?.name || '');
  const [customerPhone, setCustomerPhone] = useState('');
  const [bookingId, setBookingId] = useState('');
  const [processing, setProcessing] = useState(false);

  useEffect(() => {
    loadCart();
  }, []);

  const total = getTotal();
  const totalWithTax = Math.round(total * 1.18); // Adding 18% GST

  const handleCheckout = () => {
    if (items.length === 0) {
      Alert.alert('Cart Empty', 'Please add services or products to your cart.');
      return;
    }
    setStep('details');
  };

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
        amount: totalWithTax * 100,
        currency: 'INR',
        items: items.map(item => ({
          id: item.id,
          name: item.name,
          price: item.price,
          quantity: item.quantity,
          type: item.type,
        })),
      });

      // Simulate payment processing delay
      await new Promise(resolve => setTimeout(resolve, 2000 + Math.random() * 1000));

      // Step 2: Verify payment (mock)
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

  const handleBack = () => {
    if (step === 'details') setStep('cart');
    else if (step === 'payment') setStep('details');
    else router.back();
  };

  const handleGoHome = () => {
    router.replace('/(tabs)/home');
  };

  const getTotalDuration = () => {
    return items.reduce((total, item) => total + (item.duration || 0) * item.quantity, 0);
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
          <ActivityIndicator size="large" color="#D4AF37" />
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
        <TouchableOpacity onPress={handleBack} style={styles.backButton}>
          <Ionicons name="arrow-back" size={24} color="#FFFFFF" />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>
          {step === 'cart' ? 'Your Cart' : step === 'details' ? 'Checkout' : 'Payment'}
        </Text>
        {step === 'cart' && items.length > 0 && (
          <TouchableOpacity onPress={clearCart} style={styles.clearButton}>
            <Text style={styles.clearButtonText}>Clear</Text>
          </TouchableOpacity>
        )}
        {step !== 'cart' && <View style={{ width: 40 }} />}
      </View>

      {/* Progress Indicator for Checkout */}
      {step !== 'cart' && (
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
      )}

      {/* Cart Items View */}
      {step === 'cart' && items.length === 0 ? (
        <View style={styles.emptyContainer}>
          <Ionicons name="cart-outline" size={64} color="rgba(255,255,255,0.2)" />
          <Text style={styles.emptyTitle}>Cart is Empty</Text>
          <Text style={styles.emptyText}>Add services or products to get started</Text>
          <TouchableOpacity
            style={styles.browseButton}
            onPress={() => router.push('/(tabs)/services')}
          >
            <Text style={styles.browseButtonText}>Browse Services</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <>
          <ScrollView
            showsVerticalScrollIndicator={false}
            contentContainerStyle={styles.scrollContent}
          >
            {/* Cart Items */}
            {step === 'cart' && (
              <Animated.View entering={FadeIn}>
                <View style={styles.section}>
                  <Text style={styles.sectionTitle}>Selected Services & Products</Text>
                  {items.map((item, index) => (
                    <Animated.View
                      key={item.id}
                      entering={FadeInDown.delay(index * 50)}
                      style={styles.cartItem}
                    >
                      <View style={styles.itemInfo}>
                        <View style={styles.itemBadge}>
                          <Text style={styles.itemBadgeText}>
                            {item.type === 'service' ? 'Service' : item.type === 'product' ? 'Product' : 'Add-on'}
                          </Text>
                        </View>
                        <Text style={styles.itemName}>{item.name}</Text>
                        {item.duration && (
                          <Text style={styles.itemDuration}>{item.duration} min</Text>
                        )}
                      </View>
                      <View style={styles.itemActions}>
                        <View style={styles.quantityControl}>
                          <TouchableOpacity
                            style={styles.quantityButton}
                            onPress={() => updateQuantity(item.id, item.quantity - 1)}
                          >
                            <Ionicons name="remove" size={16} color="#D4AF37" />
                          </TouchableOpacity>
                          <Text style={styles.quantityText}>{item.quantity}</Text>
                          <TouchableOpacity
                            style={styles.quantityButton}
                            onPress={() => updateQuantity(item.id, item.quantity + 1)}
                          >
                            <Ionicons name="add" size={16} color="#D4AF37" />
                          </TouchableOpacity>
                        </View>
                        <Text style={styles.itemPrice}>₹{(item.price * item.quantity).toLocaleString('en-IN')}</Text>
                      </View>
                    </Animated.View>
                  ))}
                </View>

                {/* Summary */}
                <View style={styles.summaryCard}>
                  <View style={styles.summaryRow}>
                    <Text style={styles.summaryLabel}>Subtotal</Text>
                    <Text style={styles.summaryValue}>₹{total.toLocaleString('en-IN')}</Text>
                  </View>
                  <View style={styles.summaryRow}>
                    <Text style={styles.summaryLabel}>Total Duration</Text>
                    <Text style={styles.summaryValue}>{getTotalDuration()} min</Text>
                  </View>
                  <View style={styles.divider} />
                  <View style={styles.summaryRow}>
                    <Text style={styles.totalLabel}>Total Amount</Text>
                    <Text style={styles.totalValue}>₹{total.toLocaleString('en-IN')}</Text>
                  </View>
                </View>

                {/* Offers */}
                <View style={styles.offerCard}>
                  <Ionicons name="gift" size={20} color="#2ECC71" />
                  <View style={styles.offerInfo}>
                    <Text style={styles.offerTitle}>First Visit Offer!</Text>
                    <Text style={styles.offerText}>Get 10% off on your first booking</Text>
                  </View>
                </View>
              </Animated.View>
            )}

            {/* Customer Details Form */}
            {step === 'details' && (
              <Animated.View entering={FadeIn}>
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
                  
                  {items.map((item) => (
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

            {/* Payment Selection */}
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
          <View style={[styles.checkoutContainer, { paddingBottom: insets.bottom + 16 }]}>
            {step === 'cart' && (
              <>
                <View style={styles.checkoutTotal}>
                  <Text style={styles.checkoutTotalLabel}>Total</Text>
                  <Text style={styles.checkoutTotalValue}>₹{total.toLocaleString('en-IN')}</Text>
                </View>
                <TouchableOpacity style={styles.checkoutButton} onPress={handleCheckout}>
                  <Text style={styles.checkoutButtonText}>Proceed to Checkout</Text>
                  <Ionicons name="arrow-forward" size={20} color="#0A0A0A" />
                </TouchableOpacity>
              </>
            )}
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
        </>
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
  clearButton: {
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  clearButtonText: {
    fontSize: 14,
    color: '#E74C3C',
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
  emptyContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: 40,
  },
  emptyTitle: {
    fontSize: 20,
    fontWeight: '600',
    color: '#FFFFFF',
    marginTop: 16,
  },
  emptyText: {
    fontSize: 14,
    color: 'rgba(255,255,255,0.5)',
    marginTop: 8,
  },
  browseButton: {
    backgroundColor: '#D4AF37',
    paddingVertical: 12,
    paddingHorizontal: 24,
    borderRadius: 24,
    marginTop: 24,
  },
  browseButtonText: {
    fontSize: 14,
    fontWeight: '600',
    color: '#0A0A0A',
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
    marginBottom: 12,
  },
  cartItem: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
  },
  itemInfo: {
    flex: 1,
  },
  itemBadge: {
    backgroundColor: 'rgba(212, 175, 55, 0.2)',
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 8,
    alignSelf: 'flex-start',
    marginBottom: 6,
  },
  itemBadgeText: {
    fontSize: 10,
    color: '#D4AF37',
    fontWeight: '500',
  },
  itemName: {
    fontSize: 14,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  itemDuration: {
    fontSize: 12,
    color: 'rgba(255,255,255,0.5)',
    marginTop: 4,
  },
  itemActions: {
    alignItems: 'flex-end',
  },
  quantityControl: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255,255,255,0.1)',
    borderRadius: 16,
    paddingHorizontal: 4,
  },
  quantityButton: {
    width: 28,
    height: 28,
    justifyContent: 'center',
    alignItems: 'center',
  },
  quantityText: {
    fontSize: 14,
    fontWeight: '600',
    color: '#FFFFFF',
    marginHorizontal: 8,
  },
  itemPrice: {
    fontSize: 14,
    fontWeight: '600',
    color: '#D4AF37',
    marginTop: 8,
  },
  summaryCard: {
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    padding: 16,
    marginBottom: 24,
  },
  summaryRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 10,
  },
  summaryLabel: {
    fontSize: 14,
    color: 'rgba(255,255,255,0.6)',
  },
  summaryValue: {
    fontSize: 14,
    color: '#FFFFFF',
  },
  divider: {
    height: 1,
    backgroundColor: 'rgba(255,255,255,0.1)',
    marginVertical: 10,
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
  offerCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(46, 204, 113, 0.1)',
    borderRadius: 12,
    padding: 14,
    gap: 12,
  },
  offerInfo: {
    flex: 1,
  },
  offerTitle: {
    fontSize: 14,
    fontWeight: '600',
    color: '#2ECC71',
  },
  offerText: {
    fontSize: 12,
    color: 'rgba(255,255,255,0.6)',
    marginTop: 2,
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
  checkoutContainer: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#121212',
    paddingHorizontal: 20,
    paddingTop: 16,
    borderTopWidth: 1,
    borderTopColor: 'rgba(255,255,255,0.1)',
  },
  checkoutTotal: {
    flex: 1,
  },
  checkoutTotalLabel: {
    fontSize: 12,
    color: 'rgba(255,255,255,0.5)',
  },
  checkoutTotalValue: {
    fontSize: 20,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  checkoutButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#D4AF37',
    paddingVertical: 14,
    paddingHorizontal: 24,
    borderRadius: 24,
    gap: 8,
  },
  checkoutButtonText: {
    fontSize: 16,
    fontWeight: '600',
    color: '#0A0A0A',
  },
  primaryButton: {
    flex: 1,
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
  processingTitle: {
    fontSize: 22,
    fontWeight: '600',
    color: '#FFFFFF',
    marginTop: 24,
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
