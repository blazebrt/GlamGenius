import re

content = open('../frontend/src/components/shopping/PurchaseMemoryCard.tsx', 'r').read()

# Make transport independent
content = content.replace('export function PurchaseMemoryCard({ guard }: { guard: PurchaseGuard | null }) {',
'''export interface PurchaseMemoryProps {
  guardState: 'exact_prior_bought' | 'exact_prior_waiting' | 'exact_prior_skipped' | 'exact_prior_consideration' | 'historical_context_incomplete' | 'identity_insufficient' | 'no_step9a_prior_event' | null;
  occurredAt: string | null;
  considerationCount: number;
}

export function PurchaseMemoryCard({ guardState, occurredAt, considerationCount }: PurchaseMemoryProps) {''')

content = content.replace('if (!guard) return null;', 'if (!guardState || guardState === "no_step9a_prior_event") return null;')
content = content.replace('const message = messageFor(guard.guard_state);', 'const message = messageFor(guardState);')
content = content.replace('const date = displayDate(guard.most_recent?.occurred_at || null);', 'const date = displayDate(occurredAt);')
content = content.replace('{guard.prior_consideration_count > 1 && <Text style={styles.note}>{PURCHASE_MEMORY.count(guard.prior_consideration_count)}</Text>}', '{considerationCount > 1 && <Text style={styles.note}>{PURCHASE_MEMORY.count(considerationCount)}</Text>}')

open('../frontend/src/components/shopping/PurchaseMemoryCard.tsx', 'w').write(content)
print("Updated PurchaseMemoryCard.tsx")
