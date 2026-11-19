from rest_framework.permissions import BasePermission

from authapp.site_access import is_org_admin


def is_support_user(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    role = (getattr(user, "role", None) or "").strip().lower()
    return role == "support"


def is_super_admin_user(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    role = (getattr(user, "role", None) or "").strip().lower()
    return role in ("superadmin", "super_admin")


def is_organisation_admin(user):
    """Organisation-scoped Admin role (role == 'admin', not Super Admin)."""
    return is_org_admin(user)


def can_view_ticket(user, ticket):
    """
    Ticket visibility:
    - SUPPORT / Super Admin → any ticket
    - Org Admin (role admin) → any ticket in their organisation
    - Regular users → tickets they created only
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if is_support_user(user):
        return True
    if str(getattr(ticket, "created_by_id", "")) == str(getattr(user, "id", "")):
        return True
    if is_organisation_admin(user):
        org_id = getattr(user, "location_id", None)
        ticket_org = getattr(ticket, "organisation_id", None)
        return bool(org_id and ticket_org and str(org_id) == str(ticket_org))
    return False


class IsSupportUser(BasePermission):
    """SUPPORT role or Super Admin (for ticket ops)."""

    def has_permission(self, request, view):
        return is_support_user(request.user)


class IsTicketCreatorOrSupport(BasePermission):
    def has_object_permission(self, request, view, obj):
        return can_view_ticket(request.user, obj)
