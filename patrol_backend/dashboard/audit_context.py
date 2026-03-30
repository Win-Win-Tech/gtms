from contextvars import ContextVar


_current_user = ContextVar("audit_current_user", default=None)
_current_source = ContextVar("audit_current_source", default="unknown")


def set_audit_actor(user=None, source="unknown"):
    token_user = _current_user.set(user)
    token_source = _current_source.set(source or "unknown")
    return token_user, token_source


def reset_audit_actor(tokens):
    if not tokens:
        return
    token_user, token_source = tokens
    _current_user.reset(token_user)
    _current_source.reset(token_source)


def get_audit_user():
    return _current_user.get()


def get_audit_source():
    return _current_source.get()

