import re

content = open('../frontend/src/__tests__/purchaseMemoryCard.test.tsx', 'r').read()

content = content.replace(
    'render(<PurchaseMemoryCard guard={guard(state)} />);',
    'render(<PurchaseMemoryCard guardState={state} occurredAt="2026-09-01T00:00:00Z" considerationCount={1} />);'
)
content = content.replace(
    'render(<PurchaseMemoryCard guard={guard(\'historical_context_incomplete\')} />);',
    'render(<PurchaseMemoryCard guardState="historical_context_incomplete" occurredAt="2026-09-01T00:00:00Z" considerationCount={1} />);'
)
content = content.replace(
    'rerender(<PurchaseMemoryCard guard={guard(\'identity_insufficient\')} />);',
    'rerender(<PurchaseMemoryCard guardState="identity_insufficient" occurredAt="2026-09-01T00:00:00Z" considerationCount={1} />);'
)
content = content.replace(
    'render(<PurchaseMemoryCard guard={guard(\'no_step9a_prior_event\')} />);',
    'render(<PurchaseMemoryCard guardState="no_step9a_prior_event" occurredAt="2026-09-01T00:00:00Z" considerationCount={1} />);'
)
content = content.replace(
    'render(<PurchaseMemoryCard guard={value} />);',
    'render(<PurchaseMemoryCard guardState="exact_prior_waiting" occurredAt="2026-09-01T00:00:00Z" considerationCount={3} />);'
)

open('../frontend/src/__tests__/purchaseMemoryCard.test.tsx', 'w').write(content)
print("Updated purchaseMemoryCard.test.tsx")
