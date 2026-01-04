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
          <View style={styles.successIcon}><Ionicons name="checkmark-circle" size={80} color="#10B981" /></View>
          <Text style={styles.successTitle}>Booking Confirmed! 🎉</Text>
          <Text style={styles.successText}>Your appointment has been booked. You'll receive a confirmation SMS shortly.</Text>
          <View style={styles.bookingDetails}>
            <View style={styles.bookingRow}><Text style={styles.bookingLabel}>Booking ID</Text><Text style={styles.bookingValue}>{bookingId.slice(0, 12)}...</Text></View>
            <View style={styles.bookingRow}><Text style={styles.bookingLabel}>Amount Paid</Text><Text style={styles.bookingValue}>₹{totalWithTax.toLocaleString('en-IN')}</Text></View>
            <View style={styles.bookingRow}><Text style={styles.bookingLabel}>Payment</Text><Text style={styles.bookingValue}>{selectedPayment.toUpperCase()}</Text></View>
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
          <ActivityIndicator size="large" color="#0EA5E9" />
          <Text style={styles.processingTitle}>Processing Payment</Text>
          <Text style={styles.processingText}>Please wait...</Text>
        </View>
      </View>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <TouchableOpacity onPress={handleBack} style={styles.backButton}><Ionicons name="arrow-back" size={24} color="#1E293B" /></TouchableOpacity>
        <Text style={styles.headerTitle}>{step === 'cart' ? 'Your Cart' : step === 'details' ? 'Checkout' : 'Payment'}</Text>
        {step === 'cart' && items.length > 0 ? (
          <TouchableOpacity onPress={clearCart}><Text style={styles.clearText}>Clear</Text></TouchableOpacity>
        ) : <View style={{ width: 40 }} />}
      </View>

      {step === 'cart' && items.length === 0 ? (
        <View style={styles.emptyContainer}>
          <Ionicons name="cart-outline" size={64} color="#E2E8F0" />
          <Text style={styles.emptyTitle}>Your cart is empty</Text>
          <TouchableOpacity style={styles.browseButton} onPress={() => router.push('/(tabs)/services')}>
            <Text style={styles.browseButtonText}>Browse Services</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <>
          <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.scrollContent}>
            {step === 'cart' && items.map((item, index) => (
              <Animated.View key={item.id} entering={FadeInDown.delay(index * 50)} style={styles.cartItem}>
                <View style={styles.itemInfo}>
                  <Text style={styles.itemType}>{item.type}</Text>
                  <Text style={styles.itemName}>{item.name}</Text>
                  {item.duration && <Text style={styles.itemDuration}>{item.duration} min</Text>}
                </View>
                <View style={styles.itemActions}>
                  <View style={styles.quantityControl}>
                    <TouchableOpacity style={styles.quantityBtn} onPress={() => updateQuantity(item.id, item.quantity - 1)}><Ionicons name="remove" size={16} color="#0EA5E9" /></TouchableOpacity>
                    <Text style={styles.quantityText}>{item.quantity}</Text>
                    <TouchableOpacity style={styles.quantityBtn} onPress={() => updateQuantity(item.id, item.quantity + 1)}><Ionicons name="add" size={16} color="#0EA5E9" /></TouchableOpacity>
                  </View>
                  <Text style={styles.itemPrice}>₹{(item.price * item.quantity).toLocaleString('en-IN')}</Text>
                </View>
              </Animated.View>
            ))}

            {step === 'cart' && (
              <View style={styles.summaryCard}>
                <View style={styles.summaryRow}><Text style={styles.summaryLabel}>Subtotal</Text><Text style={styles.summaryValue}>₹{total.toLocaleString('en-IN')}</Text></View>
                <View style={styles.summaryRow}><Text style={styles.summaryLabel}>Duration</Text><Text style={styles.summaryValue}>{getTotalDuration()} min</Text></View>
                <View style={styles.divider} />
                <View style={styles.summaryRow}><Text style={styles.totalLabel}>Total</Text><Text style={styles.totalValue}>₹{total.toLocaleString('en-IN')}</Text></View>
              </View>
            )}

            {step === 'details' && (
              <Animated.View entering={FadeIn}>
                <Text style={styles.sectionTitle}>Your Details</Text>
                <View style={styles.inputContainer}>
                  <Ionicons name="person-outline" size={20} color="#94A3B8" />
                  <TextInput style={styles.input} placeholder="Full Name *" placeholderTextColor="#94A3B8" value={customerName} onChangeText={setCustomerName} />
                </View>
                <View style={styles.inputContainer}>
                  <Ionicons name="call-outline" size={20} color="#94A3B8" />
                  <TextInput style={styles.input} placeholder="Phone Number *" placeholderTextColor="#94A3B8" value={customerPhone} onChangeText={setCustomerPhone} keyboardType="phone-pad" maxLength={10} />
                </View>
                <Text style={[styles.sectionTitle, { marginTop: 24 }]}>Order Summary</Text>
                {items.map(item => (
                  <View key={item.id} style={styles.orderItem}>
                    <Text style={styles.orderItemName}>{item.name} x{item.quantity}</Text>
                    <Text style={styles.orderItemPrice}>₹{(item.price * item.quantity).toLocaleString('en-IN')}</Text>
                  </View>
                ))}
                <View style={styles.divider} />
                <View style={styles.summaryRow}><Text style={styles.summaryLabel}>Subtotal</Text><Text style={styles.summaryValue}>₹{total.toLocaleString('en-IN')}</Text></View>
                <View style={styles.summaryRow}><Text style={styles.summaryLabel}>GST (18%)</Text><Text style={styles.summaryValue}>₹{Math.round(total * 0.18).toLocaleString('en-IN')}</Text></View>
                <View style={styles.summaryRow}><Text style={styles.totalLabel}>Total</Text><Text style={styles.totalValue}>₹{totalWithTax.toLocaleString('en-IN')}</Text></View>
              </Animated.View>
            )}

            {step === 'payment' && (
              <Animated.View entering={FadeIn}>
                <View style={styles.amountCard}>
                  <Text style={styles.amountLabel}>Amount to Pay</Text>
                  <Text style={styles.amountValue}>₹{totalWithTax.toLocaleString('en-IN')}</Text>
                </View>
                <Text style={styles.sectionTitle}>Payment Method</Text>
                {PAYMENT_METHODS.map(method => (
                  <TouchableOpacity key={method.id} style={[styles.paymentOption, selectedPayment === method.id && styles.paymentOptionActive]} onPress={() => setSelectedPayment(method.id)}>
                    <Ionicons name={method.icon as any} size={22} color={selectedPayment === method.id ? '#0EA5E9' : '#64748B'} />
                    <View style={styles.paymentInfo}>
                      <Text style={[styles.paymentLabel, selectedPayment === method.id && styles.paymentLabelActive]}>{method.label}</Text>
                      {method.popular && <View style={styles.popularBadge}><Text style={styles.popularText}>Popular</Text></View>}
                    </View>
                    <View style={[styles.radioOuter, selectedPayment === method.id && styles.radioOuterActive]}>
                      {selectedPayment === method.id && <View style={styles.radioInner} />}
                    </View>
                  </TouchableOpacity>
                ))}
                <View style={styles.securityNote}>
                  <Ionicons name="shield-checkmark" size={18} color="#10B981" />
                  <Text style={styles.securityText}>Your payment is 100% secure</Text>
                </View>
              </Animated.View>
            )}
          </ScrollView>

          <View style={[styles.bottomBar, { paddingBottom: insets.bottom + 16 }]}>
            {step === 'cart' && (
              <TouchableOpacity style={styles.primaryButton} onPress={handleCheckout}>
                <Text style={styles.primaryButtonText}>Proceed to Checkout</Text>
                <Ionicons name="arrow-forward" size={20} color="#FFFFFF" />
              </TouchableOpacity>
            )}
            {step === 'details' && (
              <TouchableOpacity style={styles.primaryButton} onPress={handleProceedToPayment}>
                <Text style={styles.primaryButtonText}>Proceed to Payment</Text>
                <Ionicons name="arrow-forward" size={20} color="#FFFFFF" />
              </TouchableOpacity>
            )}
            {step === 'payment' && (
              <TouchableOpacity style={styles.primaryButton} onPress={handlePayment}>
                <Text style={styles.primaryButtonText}>Pay ₹{totalWithTax.toLocaleString('en-IN')}</Text>
                <Ionicons name="lock-closed" size={20} color="#FFFFFF" />
              </TouchableOpacity>
            )}
          </View>
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F8FAFC' },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 16, paddingVertical: 12 },
  backButton: { width: 40, height: 40, borderRadius: 20, backgroundColor: '#FFFFFF', justifyContent: 'center', alignItems: 'center', borderWidth: 1, borderColor: '#E2E8F0' },
  headerTitle: { fontSize: 18, fontWeight: '600', color: '#1E293B' },
  clearText: { fontSize: 14, color: '#EF4444', fontWeight: '500' },
  emptyContainer: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: 12 },
  emptyTitle: { fontSize: 18, fontWeight: '600', color: '#64748B' },
  browseButton: { backgroundColor: '#0EA5E9', paddingVertical: 12, paddingHorizontal: 24, borderRadius: 24, marginTop: 12 },
  browseButtonText: { fontSize: 14, fontWeight: '600', color: '#FFFFFF' },
  scrollContent: { padding: 20, paddingBottom: 120 },
  cartItem: { flexDirection: 'row', justifyContent: 'space-between', backgroundColor: '#FFFFFF', borderRadius: 14, padding: 16, marginBottom: 12, borderWidth: 1, borderColor: '#E2E8F0' },
  itemInfo: { flex: 1 },
  itemType: { fontSize: 11, color: '#0EA5E9', fontWeight: '600', textTransform: 'uppercase', marginBottom: 4 },
  itemName: { fontSize: 15, fontWeight: '600', color: '#1E293B' },
  itemDuration: { fontSize: 13, color: '#64748B', marginTop: 4 },
  itemActions: { alignItems: 'flex-end', gap: 8 },
  quantityControl: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#F1F5F9', borderRadius: 20, paddingHorizontal: 4 },
  quantityBtn: { width: 28, height: 28, justifyContent: 'center', alignItems: 'center' },
  quantityText: { fontSize: 14, fontWeight: '600', color: '#1E293B', marginHorizontal: 8 },
  itemPrice: { fontSize: 15, fontWeight: '600', color: '#0EA5E9' },
  summaryCard: { backgroundColor: '#FFFFFF', borderRadius: 16, padding: 18, marginTop: 8, borderWidth: 1, borderColor: '#E2E8F0' },
  summaryRow: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 10 },
  summaryLabel: { fontSize: 14, color: '#64748B' },
  summaryValue: { fontSize: 14, color: '#1E293B' },
  divider: { height: 1, backgroundColor: '#E2E8F0', marginVertical: 12 },
  totalLabel: { fontSize: 16, fontWeight: '600', color: '#1E293B' },
  totalValue: { fontSize: 18, fontWeight: '700', color: '#0EA5E9' },
  sectionTitle: { fontSize: 16, fontWeight: '600', color: '#1E293B', marginBottom: 14 },
  inputContainer: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#FFFFFF', borderRadius: 12, paddingHorizontal: 16, marginBottom: 12, borderWidth: 1, borderColor: '#E2E8F0' },
  input: { flex: 1, paddingVertical: 14, marginLeft: 12, fontSize: 16, color: '#1E293B' },
  orderItem: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 8 },
  orderItemName: { fontSize: 14, color: '#1E293B' },
  orderItemPrice: { fontSize: 14, fontWeight: '500', color: '#0EA5E9' },
  amountCard: { backgroundColor: '#E0F2FE', borderRadius: 16, padding: 20, alignItems: 'center', marginBottom: 24 },
  amountLabel: { fontSize: 14, color: '#0284C7' },
  amountValue: { fontSize: 32, fontWeight: '700', color: '#0EA5E9', marginTop: 4 },
  paymentOption: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#FFFFFF', borderRadius: 14, padding: 16, marginBottom: 10, borderWidth: 1, borderColor: '#E2E8F0' },
  paymentOptionActive: { borderColor: '#0EA5E9', backgroundColor: '#E0F2FE' },
  paymentInfo: { flex: 1, marginLeft: 14 },
  paymentLabel: { fontSize: 15, color: '#1E293B' },
  paymentLabelActive: { fontWeight: '500', color: '#0284C7' },
  popularBadge: { backgroundColor: '#D1FAE5', paddingHorizontal: 8, paddingVertical: 2, borderRadius: 8, marginTop: 4, alignSelf: 'flex-start' },
  popularText: { fontSize: 10, color: '#059669', fontWeight: '600' },
  radioOuter: { width: 22, height: 22, borderRadius: 11, borderWidth: 2, borderColor: '#E2E8F0', justifyContent: 'center', alignItems: 'center' },
  radioOuterActive: { borderColor: '#0EA5E9' },
  radioInner: { width: 12, height: 12, borderRadius: 6, backgroundColor: '#0EA5E9' },
  securityNote: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#D1FAE5', borderRadius: 12, padding: 14, marginTop: 16, gap: 10 },
  securityText: { fontSize: 13, color: '#059669' },
  bottomBar: { position: 'absolute', bottom: 0, left: 0, right: 0, paddingHorizontal: 20, paddingTop: 16, backgroundColor: '#FFFFFF', borderTopWidth: 1, borderTopColor: '#E2E8F0' },
  primaryButton: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', backgroundColor: '#0EA5E9', paddingVertical: 16, borderRadius: 28, gap: 10 },
  primaryButtonText: { fontSize: 16, fontWeight: '600', color: '#FFFFFF' },
  processingContainer: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: 12 },
  processingTitle: { fontSize: 20, fontWeight: '600', color: '#1E293B' },
  processingText: { fontSize: 14, color: '#64748B' },
  successContainer: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: 30 },
  successIcon: { marginBottom: 20 },
  successTitle: { fontSize: 24, fontWeight: '700', color: '#1E293B' },
  successText: { fontSize: 14, color: '#64748B', textAlign: 'center', marginTop: 8, lineHeight: 22 },
  bookingDetails: { backgroundColor: '#F8FAFC', borderRadius: 16, padding: 20, marginTop: 24, width: '100%' },
  bookingRow: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 12 },
  bookingLabel: { fontSize: 13, color: '#64748B' },
  bookingValue: { fontSize: 14, fontWeight: '600', color: '#1E293B' },
});
