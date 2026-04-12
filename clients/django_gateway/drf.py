"""
Optional Django REST Framework integration.

Provides DRF-compatible authentication and permission classes that read the
same gateway context as GatewayMiddleware.

Install:
    pip install djangorestframework

Usage:

    # settings.py — set globally
    REST_FRAMEWORK = {
        "DEFAULT_AUTHENTICATION_CLASSES": [
            "django_gateway.drf.GatewayAuthentication",
        ],
        "DEFAULT_PERMISSION_CLASSES": [
            "django_gateway.drf.IsGatewayAuthenticated",
        ],
    }

    # Or per-view:
    from django_gateway.drf import GatewayAuthentication, IsGatewayAuthenticated, HasTenant, HasRole

    class MyView(APIView):
        authentication_classes = [GatewayAuthentication]
        permission_classes = [IsGatewayAuthenticated, HasTenant]

    class AdminView(APIView):
        authentication_classes = [GatewayAuthentication]
        permission_classes = [HasRole("admin")]
"""

from __future__ import annotations

try:
    from rest_framework.authentication import BaseAuthentication
    from rest_framework.permissions import BasePermission
    from rest_framework.exceptions import AuthenticationFailed
    _DRF_AVAILABLE = True
except ImportError:
    _DRF_AVAILABLE = False


if not _DRF_AVAILABLE:
    raise ImportError(
        "djangorestframework is required for django_gateway.drf. "
        "Install it with: pip install djangorestframework"
    )


from django_gateway.middleware import GatewayUser, GatewayTenant, _conf


# ── Authentication ─────────────────────────────────────────────────────────────

class GatewayAuthentication(BaseAuthentication):
    """
    DRF authentication backend that reads gateway-injected identity headers.

    Returns a (GatewayUser, None) tuple when X-User-Id is present.
    Returns None (unauthenticated) when the header is absent so other
    authentication backends in the chain can still run.
    """

    def authenticate(self, request):
        # DRF wraps the Django request; underlying META is on request._request
        meta = getattr(request, "_request", request).META
        user_id = meta.get(_conf("USER_ID_HEADER"))
        if not user_id:
            return None  # let other backends try

        email = meta.get(_conf("USER_EMAIL_HEADER"), "")
        roles_raw = meta.get(_conf("USER_ROLES_HEADER"), "")
        roles = [r.strip() for r in roles_raw.split(",") if r.strip()]

        user = GatewayUser(user_id, email, roles)

        # Also populate tenant if present (mirrors what GatewayMiddleware does)
        tenant_id = meta.get(_conf("TENANT_ID_HEADER"))
        if tenant_id:
            tenant_name = meta.get(_conf("TENANT_NAME_HEADER"))
            request.tenant = GatewayTenant(tenant_id, tenant_name)
        else:
            request.tenant = None

        return (user, None)

    def authenticate_header(self, request):
        return "Bearer"


# ── Permissions ───────────────────────────────────────────────────────────────

class IsGatewayAuthenticated(BasePermission):
    """
    Allow only requests whose user was populated by the gateway.

    Tighter than DRF's built-in IsAuthenticated: rejects plain Django session
    users and anonymous users — only GatewayUser instances pass.
    """

    message = "Gateway authentication required."

    def has_permission(self, request, view):
        return isinstance(request.user, GatewayUser)


class HasTenant(BasePermission):
    """Allow only requests that carry a valid tenant context (API key)."""

    message = "Tenant identification required."

    def has_permission(self, request, view):
        return bool(getattr(request, "tenant", None))


class HasRole(BasePermission):
    """
    Allow only requests where the gateway user holds at least one of the
    specified roles.

    Usage:
        permission_classes = [HasRole("admin")]
        permission_classes = [HasRole("editor", "admin")]   # any one sufficient
    """

    def __init__(self, *roles: str):
        if not roles:
            raise ValueError("HasRole requires at least one role name")
        self.roles = roles
        self.message = (
            f"One of the following roles is required: "
            + ", ".join(f'"{r}"' for r in roles)
        )

    def has_permission(self, request, view):
        user = request.user
        if not isinstance(user, GatewayUser):
            return False
        return user.has_any_role(*self.roles)


class HasAllRoles(BasePermission):
    """
    Allow only requests where the gateway user holds every specified role.

    Usage:
        permission_classes = [HasAllRoles("editor", "verified")]
    """

    def __init__(self, *roles: str):
        if not roles:
            raise ValueError("HasAllRoles requires at least one role name")
        self.roles = roles
        self.message = (
            f"All of the following roles are required: "
            + ", ".join(f'"{r}"' for r in roles)
        )

    def has_permission(self, request, view):
        user = request.user
        if not isinstance(user, GatewayUser):
            return False
        return user.has_all_roles(*self.roles)
