/** Customer wording for the read-only Step 9A purchase-memory projection. */
export const PURCHASE_MEMORY = {
  title: 'Purchase memory',
  bought: 'You previously marked this exact product as bought.',
  waiting: 'Last time, you chose to wait.',
  skipped: 'Last time, you chose to skip this.',
  considered: 'You have considered this exact product before.',
  count: (count: number) => `You have considered this exact product ${count} times.`,
  incomplete: 'Some older purchase history may not be available here.',
  insufficient: 'Confirm the product details to compare it with your purchase memory.',
  lastDecision: (date: string) => `Last decision: ${date}`,
} as const;
