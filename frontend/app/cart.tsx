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
import { COLORS, FONTS, SPACING, RADIUS } from '../src/theme/colors';

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

  useEffect(() => { loadCart(); }, []);

  const total = getTotal();
  const totalWithTax = Math.round(total * 1.18);

  const handleCheckout = () => {
    if (items.length === 0) { Alert.alert('Cart Empty', 'Please add services or products.'); return; }
    setStep('details');
  };

  const handleProceedToPayment = () => {
    if (!customerName.trim()) { Alert.alert('Required', 'Please enter your name'); return; }
    if (!customerPhone.trim() || customerPhone.length < 10) { Alert.alert('Required', 'Please enter a valid phone number'); return; }
    setStep('payment');
  };

  const handlePayment = async () => {
    setStep('processing');
    setProcessing(true);
    try {
      const orderResponse = await api.post('/payments/create-order', {
        user_id: userId, amount: totalWithTax * 100, currency: 'INR',
        items: items.map(item => ({ id: item.id, name: item.name, price: item.price, quantity: item.quantity, type: item.type })),
      });
      await new Promise(resolve => setTimeout(resolve, 2000));
      const verifyResponse = await api.post('/payments/verify', {
        order_id: orderResponse.data.id, payment_id: `pay_mock_${Date.now()}`, payment_method: selectedPayment,
      });
      if (verifyResponse.data.success) { setBookingId(verifyResponse.data.booking_id); setStep('success'); clearCart(); }
      else throw new Error('Payment failed');
    } catch (error) {
      Alert.alert('Payment Failed', 'Please try again.', [{ text: 'OK', onPress: () => setStep('payment') }]);
    } finally { setProcessing(false); }
  };

  const handleBack = () => {
    if (step === 'details') setStep('cart');
    else if (step === 'payment') setStep('details');
    else router.back();
  };

  const getTotalDuration = () => items.reduce((t, i) => t + (i.duration || 0) * i.quantity, 0);

  if (step === 'success') {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <Animated.View entering={FadeIn} style={styles.successContainer}>
          <View style={styles.successIcon}>
            <Ionicons name="checkmark-circle" size={80} color={COLORS.black} />
          </View>
          <Text style={styles.successTitle}>Booking Confirmed!</Text>
          <Text style={styles.successText}>Your appointment has been booked successfully. You'll receive a confirmation SMS shortly.</Text>
          <View style={styles.bookingDetails}>
            <View style={styles.bookingRow}>
              <Text style={styles.bookingLabel}>Booking ID</Text>
              <Text style={styles.bookingValue}>{bookingId.slice(0, 12)}...</Text>
            </View>
            <View style={styles.bookingRow}>
              <Text style={styles.bookingLabel}>Amount Paid</Text>
              <Text style={styles.bookingValue}>₹{totalWithTax.toLocaleString('en-IN')}</Text>
            </View>
            <View style={styles.bookingRow}>
              <Text style={styles.bookingLabel}>Payment</Text>
              <Text style={styles.bookingValue}>{selectedPayment.toUpperCase()}</Text>
            </View>
          </View>
          <TouchableOpacity style={styles.primaryButton} onPress={() => router.replace('/(tabs)/home')}>
            <Text style={styles.primaryButtonText}>Go to Home</Text>
          </TouchableOpacity>
        </Animated.View>
      </View>
    );
  }

  if (step === 'processing') {
    return (
      <View style={[styles.container, { paddingTop: insets.top }]}>
        <View style={styles.processingContainer}>
          <ActivityIndicator size="large" color={COLORS.black} />
          <Text style={styles.processingTitle}>Processing Payment</Text>
          <Text style={styles.processingText}>Please wait while we confirm your payment...</Text>
        </View>
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity onPress={handleBack} style={styles.backButton}>
          <Ionicons name="arrow-back" size={24} color={COLORS.textPrimary} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>
          {step === 'cart' ? 'Your Cart' : step === 'details' ? 'Checkout' : 'Payment'}
        </Text>
        {step === 'cart' && items.length > 0 ? (
          <TouchableOpacity onPress={clearCart}>
            <Text style={styles.clearText}>Clear</Text>
          </TouchableOpacity>
        ) : <View style={{ width: 40 }} />}
      </View>

      {step === 'cart' && items.length === 0 ? (
        <View style={styles.emptyContainer}>
          <Ionicons name="cart-outline" size={64} color={COLORS.border} />
          <Text style={styles.emptyTitle}>Your cart is empty</Text>
          <Text style={styles.emptySubtitle}>Add services or products to get started</Text>
          <TouchableOpacity style={styles.browseButton} onPress={() => router.push('/(tabs)/services')}>
            <Text style={styles.browseButtonText}>Browse Services</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <>
          <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.scrollContent}>
            {/* Cart Items */}
            {step === 'cart' && items.map((item, index) => (
              <Animated.View key={item.id} entering={FadeInDown.delay(index * 50)} style={styles.cartItem}>
                <View style={styles.itemInfo}>
                  <Text style={styles.itemType}>{item.type}</Text>
                  <Text style={styles.itemName}>{item.name}</Text>
                  {item.duration && <Text style={styles.itemDuration}>{item.duration} min</Text>}
                </View>
                <View style={styles.itemActions}>
                  <View style={styles.quantityControl}>
                    <TouchableOpacity style={styles.quantityBtn} onPress={() => updateQuantity(item.id, item.quantity - 1)}>
                      <Ionicons name="remove" size={16} color={COLORS.black} />
                    </TouchableOpacity>
                    <Text style={styles.quantityText}>{item.quantity}</Text>
                    <TouchableOpacity style={styles.quantityBtn} onPress={() => updateQuantity(item.id, item.quantity + 1)}>
                      <Ionicons name="add" size={16} color={COLORS.black} />
                    </TouchableOpacity>
                  </View>
                  <Text style={styles.itemPrice}>₹{(item.price * item.quantity).toLocaleString('en-IN')}</Text>
                </View>
              </Animated.View>
            ))}

            {/* Cart Summary */}
            {step === 'cart' && (
              <View style={styles.summaryCard}>
                <View style={styles.summaryRow}>
                  <Text style={styles.summaryLabel}>Subtotal</Text>
                  <Text style={styles.summaryValue}>₹{total.toLocaleString('en-IN')}</Text>
                </View>
                <View style={styles.summaryRow}>
                  <Text style={styles.summaryLabel}>Duration</Text>
                  <Text style={styles.summaryValue}>{getTotalDuration()} min</Text>
                </View>
                <View style={styles.divider} />
                <View style={styles.summaryRow}>
                  <Text style={styles.totalLabel}>Total</Text>
                  <Text style={styles.totalValue}>₹{total.toLocaleString('en-IN')}</Text>
                </View>
              </View>
            )}

            {/* Details Step */}
            {step === 'details' && (
              <Animated.View entering={FadeIn}>
                <Text style={styles.sectionTitle}>Your Details</Text>
                <View style={styles.inputContainer}>
                  <Ionicons name="person-outline" size={20} color={COLORS.textMuted} />
                  <TextInput 
                    style={styles.input} 
                    placeholder="Full Name *" 
                    placeholderTextColor={COLORS.textMuted} 
                    value={customerName} 
                    onChangeText={setCustomerName} 
                  />
                </View>
                <View style={styles.inputContainer}>
                  <Ionicons name="call-outline" size={20} color={COLORS.textMuted} />
                  <TextInput 
                    style={styles.input} 
                    placeholder="Phone Number *" 
                    placeholderTextColor={COLORS.textMuted} 
                    value={customerPhone} 
                    onChangeText={setCustomerPhone} 
                    keyboardType="phone-pad" 
                    maxLength={10} 
                  />
                </View>

                <Text style={[styles.sectionTitle, { marginTop: 24 }]}>Order Summary</Text>
                {items.map(item => (
                  <View key={item.id} style={styles.orderItem}>
                    <Text style={styles.orderItemName}>{item.name} x{item.quantity}</Text>
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
                <View style={styles.summaryRow}>
                  <Text style={styles.totalLabel}>Total</Text>
                  <Text style={styles.totalValue}>₹{totalWithTax.toLocaleString('en-IN')}</Text>
                </View>
              </Animated.View>
            )}

            {/* Payment Step */}
            {step === 'payment' && (
              <Animated.View entering={FadeIn}>
                <View style={styles.amountCard}>
                  <Text style={styles.amountLabel}>AMOUNT TO PAY</Text>
                  <Text style={styles.amountValue}>₹{totalWithTax.toLocaleString('en-IN')}</Text>
                </View>

                <Text style={styles.sectionTitle}>Payment Method</Text>
                {PAYMENT_METHODS.map(method => (
                  <TouchableOpacity 
                    key={method.id} 
                    style={[styles.paymentOption, selectedPayment === method.id && styles.paymentOptionActive]} 
                    onPress={() => setSelectedPayment(method.id)}
                  >
                    <Ionicons 
                      name={method.icon as any} 
                      size={22} 
                      color={selectedPayment === method.id ? COLORS.black : COLORS.textSecondary} 
                    />
                    <View style={styles.paymentInfo}>
                      <Text style={[styles.paymentLabel, selectedPayment === method.id && styles.paymentLabelActive]}>
                        {method.label}
                      </Text>
                      {method.popular && (
                        <View style={styles.popularBadge}>
                          <Text style={styles.popularText}>Popular</Text>
                        </View>
                      )}
                    </View>
                    <View style={[styles.radioOuter, selectedPayment === method.id && styles.radioOuterActive]}>
                      {selectedPayment === method.id && <View style={styles.radioInner} />}
                    </View>
                  </TouchableOpacity>
                ))}

                <View style={styles.securityNote}>
                  <Ionicons name="shield-checkmark" size={18} color={COLORS.black} />
                  <Text style={styles.securityText}>Your payment is 100% secure</Text>
                </View>
              </Animated.View>
            )}

            <View style={{ height: 120 }} />
          </ScrollView>

          {/* Bottom CTA */}
          <View style={[styles.bottomBar, { paddingBottom: insets.bottom + 20 }]}>
            {step === 'cart' && (
              <TouchableOpacity style={styles.primaryButton} onPress={handleCheckout}>
                <Text style={styles.primaryButtonText}>Proceed to Checkout</Text>
                <Ionicons name="arrow-forward" size={20} color={COLORS.white} />
              </TouchableOpacity>
            )}
            {step === 'details' && (
              <TouchableOpacity style={styles.primaryButton} onPress={handleProceedToPayment}>
                <Text style={styles.primaryButtonText}>Proceed to Payment</Text>
                <Ionicons name="arrow-forward" size={20} color={COLORS.white} />
              </TouchableOpacity>
            )}
            {step === 'payment' && (
              <TouchableOpacity style={styles.primaryButton} onPress={handlePayment}>
                <Text style={styles.primaryButtonText}>Pay ₹{totalWithTax.toLocaleString('en-IN')}</Text>
                <Ionicons name="lock-closed" size={20} color={COLORS.white} />
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
    backgroundColor: COLORS.background,
  },
  header: { 
    flexDirection: 'row', 
    alignItems: 'center', 
    justifyContent: 'space-between', 
    paddingHorizontal: SPACING.md, 
    paddingVertical: SPACING.sm,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
  },
  backButton: { 
    width: 44, 
    height: 44, 
    borderRadius: 22, 
    backgroundColor: COLORS.backgroundSecondary, 
    justifyContent: 'center', 
    alignItems: 'center',
  },
  headerTitle: { 
    fontSize: FONTS.sizes.h3, 
    fontFamily: FONTS.family.heading, 
    color: COLORS.textPrimary,
  },
  clearText: { 
    fontSize: FONTS.sizes.body, 
    fontFamily: FONTS.family.bodyMedium,
    color: COLORS.textSecondary,
  },
  emptyContainer: { 
    flex: 1, 
    justifyContent: 'center', 
    alignItems: 'center', 
    paddingHorizontal: SPACING.xl,
  },
  emptyTitle: { 
    fontSize: FONTS.sizes.h3, 
    fontFamily: FONTS.family.heading, 
    color: COLORS.textPrimary,
    marginTop: SPACING.md,
  },
  emptySubtitle: {
    fontSize: FONTS.sizes.body,
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
    marginTop: SPACING.xs,
  },
  browseButton: { 
    backgroundColor: COLORS.black, 
    paddingVertical: 14, 
    paddingHorizontal: 28, 
    borderRadius: RADIUS.full, 
    marginTop: SPACING.lg,
  },
  browseButtonText: { 
    fontSize: FONTS.sizes.body, 
    fontFamily: FONTS.family.bodySemibold, 
    color: COLORS.white,
  },
  scrollContent: { 
    padding: SPACING.lg, 
    paddingBottom: 140,
  },
  cartItem: { 
    flexDirection: 'row', 
    justifyContent: 'space-between', 
    backgroundColor: COLORS.card, 
    borderRadius: RADIUS.md, 
    padding: SPACING.md, 
    marginBottom: SPACING.sm, 
    borderWidth: 1, 
    borderColor: COLORS.border,
  },
  itemInfo: { 
    flex: 1,
  },
  itemType: { 
    fontSize: FONTS.sizes.micro, 
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.textSecondary, 
    textTransform: 'uppercase', 
    letterSpacing: 1,
    marginBottom: 4,
  },
  itemName: { 
    fontSize: FONTS.sizes.bodyLg, 
    fontFamily: FONTS.family.bodySemibold, 
    color: COLORS.textPrimary,
  },
  itemDuration: { 
    fontSize: FONTS.sizes.bodySm, 
    fontFamily: FONTS.family.body,
    color: COLORS.textMuted, 
    marginTop: 4,
  },
  itemActions: { 
    alignItems: 'flex-end', 
    gap: SPACING.sm,
  },
  quantityControl: { 
    flexDirection: 'row', 
    alignItems: 'center', 
    backgroundColor: COLORS.backgroundSecondary, 
    borderRadius: RADIUS.full, 
    paddingHorizontal: 4,
  },
  quantityBtn: { 
    width: 32, 
    height: 32, 
    justifyContent: 'center', 
    alignItems: 'center',
  },
  quantityText: { 
    fontSize: FONTS.sizes.body, 
    fontFamily: FONTS.family.bodySemibold, 
    color: COLORS.textPrimary, 
    marginHorizontal: SPACING.sm,
  },
  itemPrice: { 
    fontSize: FONTS.sizes.bodyLg, 
    fontFamily: FONTS.family.bodyBold, 
    color: COLORS.black,
  },
  summaryCard: { 
    backgroundColor: COLORS.card, 
    borderRadius: RADIUS.lg, 
    padding: SPACING.lg, 
    marginTop: SPACING.sm, 
    borderWidth: 1, 
    borderColor: COLORS.border,
  },
  summaryRow: { 
    flexDirection: 'row', 
    justifyContent: 'space-between', 
    marginBottom: SPACING.sm,
  },
  summaryLabel: { 
    fontSize: FONTS.sizes.body, 
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
  },
  summaryValue: { 
    fontSize: FONTS.sizes.body, 
    fontFamily: FONTS.family.bodyMedium,
    color: COLORS.textPrimary,
  },
  divider: { 
    height: 1, 
    backgroundColor: COLORS.border, 
    marginVertical: SPACING.md,
  },
  totalLabel: { 
    fontSize: FONTS.sizes.bodyLg, 
    fontFamily: FONTS.family.bodySemibold, 
    color: COLORS.textPrimary,
  },
  totalValue: { 
    fontSize: FONTS.sizes.h3, 
    fontFamily: FONTS.family.heading, 
    color: COLORS.black,
  },
  sectionTitle: { 
    fontSize: FONTS.sizes.h4, 
    fontFamily: FONTS.family.heading, 
    color: COLORS.textPrimary, 
    marginBottom: SPACING.md,
  },
  inputContainer: { 
    flexDirection: 'row', 
    alignItems: 'center', 
    backgroundColor: COLORS.card, 
    borderRadius: RADIUS.md, 
    paddingHorizontal: SPACING.md, 
    marginBottom: SPACING.sm, 
    borderWidth: 1, 
    borderColor: COLORS.border,
  },
  input: { 
    flex: 1, 
    paddingVertical: 16, 
    marginLeft: SPACING.sm, 
    fontSize: FONTS.sizes.body, 
    fontFamily: FONTS.family.body,
    color: COLORS.textPrimary,
  },
  orderItem: { 
    flexDirection: 'row', 
    justifyContent: 'space-between', 
    paddingVertical: SPACING.sm,
  },
  orderItemName: { 
    fontSize: FONTS.sizes.body, 
    fontFamily: FONTS.family.body,
    color: COLORS.textPrimary,
  },
  orderItemPrice: { 
    fontSize: FONTS.sizes.body, 
    fontFamily: FONTS.family.bodyMedium, 
    color: COLORS.black,
  },
  amountCard: { 
    backgroundColor: COLORS.backgroundSecondary, 
    borderRadius: RADIUS.lg, 
    padding: SPACING.xl, 
    alignItems: 'center', 
    marginBottom: SPACING.lg,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  amountLabel: { 
    fontSize: FONTS.sizes.caption, 
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.textSecondary,
    letterSpacing: 1,
  },
  amountValue: { 
    fontSize: FONTS.sizes.display, 
    fontFamily: FONTS.family.heading, 
    color: COLORS.black, 
    marginTop: 4,
  },
  paymentOption: { 
    flexDirection: 'row', 
    alignItems: 'center', 
    backgroundColor: COLORS.card, 
    borderRadius: RADIUS.md, 
    padding: SPACING.md, 
    marginBottom: SPACING.sm, 
    borderWidth: 1, 
    borderColor: COLORS.border,
  },
  paymentOptionActive: { 
    borderColor: COLORS.black, 
    backgroundColor: COLORS.backgroundSecondary,
  },
  paymentInfo: { 
    flex: 1, 
    marginLeft: SPACING.md,
  },
  paymentLabel: { 
    fontSize: FONTS.sizes.body, 
    fontFamily: FONTS.family.body,
    color: COLORS.textPrimary,
  },
  paymentLabelActive: { 
    fontFamily: FONTS.family.bodySemibold, 
    color: COLORS.black,
  },
  popularBadge: { 
    backgroundColor: COLORS.primaryLight, 
    paddingHorizontal: 8, 
    paddingVertical: 3, 
    borderRadius: RADIUS.sm, 
    marginTop: 4, 
    alignSelf: 'flex-start',
  },
  popularText: { 
    fontSize: FONTS.sizes.micro, 
    fontFamily: FONTS.family.bodySemibold,
    color: COLORS.textSecondary,
  },
  radioOuter: { 
    width: 22, 
    height: 22, 
    borderRadius: 11, 
    borderWidth: 2, 
    borderColor: COLORS.border, 
    justifyContent: 'center', 
    alignItems: 'center',
  },
  radioOuterActive: { 
    borderColor: COLORS.black,
  },
  radioInner: { 
    width: 12, 
    height: 12, 
    borderRadius: 6, 
    backgroundColor: COLORS.black,
  },
  securityNote: { 
    flexDirection: 'row', 
    alignItems: 'center', 
    backgroundColor: COLORS.backgroundSecondary, 
    borderRadius: RADIUS.md, 
    padding: SPACING.md, 
    marginTop: SPACING.md, 
    gap: SPACING.sm,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  securityText: { 
    fontSize: FONTS.sizes.bodySm, 
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
  },
  bottomBar: { 
    position: 'absolute', 
    bottom: 0, 
    left: 0, 
    right: 0, 
    paddingHorizontal: SPACING.lg, 
    paddingTop: SPACING.md, 
    backgroundColor: COLORS.background, 
    borderTopWidth: 1, 
    borderTopColor: COLORS.border,
  },
  primaryButton: { 
    flexDirection: 'row', 
    alignItems: 'center', 
    justifyContent: 'center', 
    backgroundColor: COLORS.black, 
    paddingVertical: 18, 
    borderRadius: RADIUS.full, 
    gap: SPACING.sm,
  },
  primaryButtonText: { 
    fontSize: FONTS.sizes.bodyLg, 
    fontFamily: FONTS.family.bodySemibold, 
    color: COLORS.white,
  },
  processingContainer: { 
    flex: 1, 
    justifyContent: 'center', 
    alignItems: 'center', 
    gap: SPACING.md,
  },
  processingTitle: { 
    fontSize: FONTS.sizes.h2, 
    fontFamily: FONTS.family.heading, 
    color: COLORS.textPrimary,
  },
  processingText: { 
    fontSize: FONTS.sizes.body, 
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary,
  },
  successContainer: { 
    flex: 1, 
    justifyContent: 'center', 
    alignItems: 'center', 
    padding: SPACING.xl,
  },
  successIcon: { 
    marginBottom: SPACING.lg,
  },
  successTitle: { 
    fontSize: FONTS.sizes.h1, 
    fontFamily: FONTS.family.heading, 
    color: COLORS.textPrimary,
  },
  successText: { 
    fontSize: FONTS.sizes.body, 
    fontFamily: FONTS.family.body,
    color: COLORS.textSecondary, 
    textAlign: 'center', 
    marginTop: SPACING.sm, 
    lineHeight: 24,
  },
  bookingDetails: { 
    backgroundColor: COLORS.backgroundSecondary, 
    borderRadius: RADIUS.lg, 
    padding: SPACING.lg, 
    marginTop: SPACING.xl, 
    width: '100%',
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  bookingRow: { 
    flexDirection: 'row', 
    justifyContent: 'space-between', 
    marginBottom: SPACING.sm,
  },
  bookingLabel: { 
    fontSize: FONTS.sizes.bodySm, 
    fontFamily: FONTS.family.body,
    color: COLORS.textMuted,
  },
  bookingValue: { 
    fontSize: FONTS.sizes.body, 
    fontFamily: FONTS.family.bodySemibold, 
    color: COLORS.textPrimary,
  },
});
