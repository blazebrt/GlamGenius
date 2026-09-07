"""Reviewed customer wording, keyed.

Not a domain. Nothing in here decides anything, reads a profile, or touches the
database: the governed layers emit copy *keys*, and this package turns a key
into the exact sentence a reviewer approved. Keeping it apart from the domains
is what makes that separation checkable — a module with no access to signals,
claims or policies cannot quietly become a second decision engine.
"""
