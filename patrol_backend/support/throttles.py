"""Scoped rate limits for support write endpoints (Phase 4 hardening)."""

from rest_framework.throttling import ScopedRateThrottle


class SupportCreateThrottle(ScopedRateThrottle):
    scope = "support_create"


class SupportCommentThrottle(ScopedRateThrottle):
    scope = "support_comment"


class SupportAttachmentThrottle(ScopedRateThrottle):
    scope = "support_attachment"


class SupportReopenThrottle(ScopedRateThrottle):
    scope = "support_reopen"
