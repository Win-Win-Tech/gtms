from .audit_context import reset_audit_actor, set_audit_actor


class AuditActorMiddleware:
    """
    Stores request user/source in contextvars so signals can attribute edits.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        source = "api" if getattr(user, "is_authenticated", False) else "unknown"
        tokens = set_audit_actor(user=user if getattr(user, "is_authenticated", False) else None, source=source)
        try:
            return self.get_response(request)
        finally:
            reset_audit_actor(tokens)

