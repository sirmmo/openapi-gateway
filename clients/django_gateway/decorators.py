"""
View decorators for gateway-aware Django views.

All decorators return JSON error responses so they compose cleanly with API
views (no HTML redirect to a login page).

Function-based views:

    @require_gateway_auth
    def my_view(request): ...

    @require_tenant
    def tenant_view(request): ...

    @require_role("admin")
    def admin_view(request): ...

    @require_role("editor", "admin")    # any one of these is sufficient
    def editor_view(request): ...

    @require_all_roles("editor", "verified")   # every role must be present
    def strict_view(request): ...

Class-based views — use method_decorator:

    from django.utils.decorators import method_decorator

    @method_decorator(require_tenant, name="dispatch")
    class TenantView(View): ...
"""

from __future__ import annotations

import functools
from django.http import JsonResponse


# ── Helpers ───────────────────────────────────────────────────────────────────

def _json_error(message: str, status: int) -> JsonResponse:
    return JsonResponse({"detail": message}, status=status)


# ── Decorators ────────────────────────────────────────────────────────────────

def require_gateway_auth(view_func):
    """
    Reject with 401 if the request user was NOT set by GatewayMiddleware.

    Use this when you need to be certain the request passed through the gateway
    and carries a validated JWT — not just any authenticated Django user.
    """
    from django_gateway.middleware import GatewayUser

    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not isinstance(getattr(request, "user", None), GatewayUser):
            return _json_error("Gateway authentication required.", 401)
        return view_func(request, *args, **kwargs)

    return wrapper


def require_tenant(view_func):
    """
    Reject with 403 if no tenant context is present on the request.

    Tenant context is populated when the caller sends a valid API key that the
    gateway recognises.  Use this on endpoints that must know which tenant is
    calling (multi-tenant data isolation, billing, rate limiting, etc.).
    """
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not getattr(request, "tenant", None):
            return _json_error("Tenant identification required.", 403)
        return view_func(request, *args, **kwargs)

    return wrapper


def require_role(*roles: str):
    """
    Reject with 403 unless the authenticated user holds at least one of the
    specified roles.

    Also rejects with 401 if the request is not authenticated at all.

    Usage:
        @require_role("admin")
        @require_role("editor", "admin")   # either role is sufficient
    """
    if not roles:
        raise ValueError("require_role() needs at least one role name")

    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user = getattr(request, "user", None)
            if user is None or not user.is_authenticated:
                return _json_error("Authentication required.", 401)
            if not getattr(user, "has_any_role", lambda *_: False)(*roles):
                role_list = ", ".join(f'"{r}"' for r in roles)
                return _json_error(
                    f"One of the following roles is required: {role_list}.", 403
                )
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def require_all_roles(*roles: str):
    """
    Reject with 403 unless the authenticated user holds every specified role.

    Usage:
        @require_all_roles("editor", "verified")
    """
    if not roles:
        raise ValueError("require_all_roles() needs at least one role name")

    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user = getattr(request, "user", None)
            if user is None or not user.is_authenticated:
                return _json_error("Authentication required.", 401)
            if not getattr(user, "has_all_roles", lambda *_: False)(*roles):
                role_list = ", ".join(f'"{r}"' for r in roles)
                return _json_error(
                    f"All of the following roles are required: {role_list}.", 403
                )
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator
