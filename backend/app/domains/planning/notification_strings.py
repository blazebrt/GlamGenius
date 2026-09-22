"""Approved customer copy for proactive notifications.

Keeping new notification copy here makes the legal/user-facing surface easy
to review.  These messages describe an account fact; they never diagnose or
promise an outcome.
"""

RUNNING_LOW_TITLE = "{name} may be running low"
RUNNING_LOW_BODY = "You have recorded {uses} uses and {remaining}% remaining."
ROUTINE_DUE_TITLE = "Your {routine} step is ready"
ROUTINE_DUE_BODY = "{step} is in the routine you chose for today."
PURCHASE_RELEVANT_TITLE = "A purchase you deferred may be worth another look"
PURCHASE_RELEVANT_BODY = "The reason you chose to wait is no longer active."


# --- Step 12C: Product Watch -------------------------------------------------
# A push is an attention surface, not the evidence surface. None of these
# states a regulator's words, a before/after value, or any judgement about the
# product; each one only says that something sourced is waiting on the product
# screen, where the official source is shown beside it.
#
# "Matches" is a current-state statement: the register lists a record that
# matches this pack today. It is never "new", "just recalled" or "cleared" —
# when we first noticed a record says nothing about when the regulator issued
# it, and a record's absence from a later download proves nothing at all.
PRODUCT_WATCH_RECORD_MATCH_TITLE = "An official record matches a product you watch"
PRODUCT_WATCH_RECORD_MATCH_BODY = "Open the product to review what FSSAI currently lists."
PRODUCT_WATCH_REGULATORY_CHANGE_TITLE = "An official record for a watched product changed"
PRODUCT_WATCH_REGULATORY_CHANGE_BODY = "Open the product to review the sourced update."
PRODUCT_WATCH_LABEL_CHANGE_TITLE = "Verified pack information changed"
PRODUCT_WATCH_LABEL_CHANGE_BODY = "Open the product to review the sourced difference."
