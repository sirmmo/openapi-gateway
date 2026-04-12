"""
Gateway middleware for Django services running behind openapi-gateway.

Drop this into MIDDLEWARE after Django's own AuthenticationMiddleware:

    MIDDLEWARE = [
        ...
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django_gateway.middleware.GatewayMiddleware",   # ← after
    ]

When the gateway is present it sets request.user (GatewayUser) and
request.tenant (GatewayTenant) from the injected headers.
When running without the gateway (local dev, tests) the middleware is a no-op
— request.user is left to Django's normal auth stack and request.tenant is None.

Header names are configurable in Django settings:

    GATEWAY = {
        "USER_ID_HEADER":    "HTTP_X_USER_ID",     # default
        "USER_EMAIL_HEADER": "HTTP_X_USER_EMAIL",   # default
        "USER_ROLES_HEADER": "HTTP_X_USER_ROLES",   # default
        "TENANT_ID_HEADER":  "HTTP_X_TENANT_ID",    # default
        "TENANT_NAME_HEADER":"HTTP_X_TENANT_NAME",  # default
    }

The keys above are Django META keys (uppercase, hyphens → underscores, HTTP_ prefix).
Adjust them only when the gateway is configured with non-default header names
(GATEWAY_API_KEY_HEADER, GATEWAY_AUTH_CLAIM_ID, etc.).
"""

from __future__ import annotations

from django.conf import settings as django_settings

# ── Defaults ──────────────────────────────────────────────────────────────────

_DEFAULTS: dict[str, str] = {
    "USER_ID_HEADER": "HTTP_X_USER_ID",
    "USER_EMAIL_HEADER": "HTTP_X_USER_EMAIL",
    "USER_ROLES_HEADER": "HTTP_X_USER_ROLES",
    "TENANT_ID_HEADER": "HTTP_X_TENANT_ID",
    "TENANT_NAME_HEADER": "HTTP_X_TENANT_NAME",
}


def _conf(key: str) -> str:
    return getattr(django_settings, "GATEWAY", {}).get(key, _DEFAULTS[key])


# ── User ──────────────────────────────────────────────────────────────────────

class GatewayUser:
    """
    Lightweight user object built from gateway-injected identity headers.

    Implements the minimal Django user interface so the usual checks work:

        request.user.is_authenticated   → True
        request.user.email              → "alice@example.com"
        request.user.roles              → ["admin", "editor"]
        request.user.has_role("admin")  → True
        str(request.user)               → "alice@example.com"

    This is NOT a database-backed user — do not call .save() or query it via the
    ORM.  It is valid only for the duration of the request.
    """

    is_active = True
    is_staff = False
    is_superuser = False

    def __init__(self, user_id: str, email: str = "", roles: list[str] | None = None):
        self.id = user_id
        self.pk = user_id
        self.username = email or user_id
        self.email = email
        self.roles: list[str] = roles or []

    # -- Django user interface -------------------------------------------------

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_username(self) -> str:
        return self.username

    # -- Role helpers ----------------------------------------------------------

    def has_role(self, role: str) -> bool:
        """Return True if the user holds exactly this role."""
        return role in self.roles

    def has_any_role(self, *roles: str) -> bool:
        """Return True if the user holds at least one of the given roles."""
        return any(r in self.roles for r in roles)

    def has_all_roles(self, *roles: str) -> bool:
        """Return True if the user holds every one of the given roles."""
        return all(r in self.roles for r in roles)

    # -- Repr ------------------------------------------------------------------

    def __str__(self) -> str:
        return self.email or self.id

    def __repr__(self) -> str:
        return f"GatewayUser(id={self.id!r}, email={self.email!r}, roles={self.roles!r})"


# ── Tenant ────────────────────────────────────────────────────────────────────

class GatewayTenant:
    """
    Tenant context injected by the gateway's API-key validation layer.

        request.tenant.id    → "acme"
        request.tenant.name  → "ACME Corp"
        str(request.tenant)  → "ACME Corp"

    Present only when the caller sent a valid API key.
    request.tenant is None when no API key was provided (or API keys are disabled
    on the gateway).
    """

    def __init__(self, tenant_id: str, tenant_name: str | None = None):
        self.id = tenant_id
        self.name = tenant_name or tenant_id

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"GatewayTenant(id={self.id!r}, name={self.name!r})"


# ── Middleware ────────────────────────────────────────────────────────────────

class GatewayMiddleware:
    """
    Reads gateway-injected headers and attaches context objects to the request.

    After this middleware runs:

        request.user    — GatewayUser  if X-User-Id is present,
                          unchanged    otherwise (Django's own auth applies)

        request.tenant  — GatewayTenant  if X-Tenant-Id is present,
                          None            otherwise

        request.gateway — dict with raw metadata:
                          {
                            "original_path": "/api/users/1",  # or None
                            "service":       "user-service",  # or None
                          }
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # -- User identity (from JWT validation) -------------------------------
        user_id = request.META.get(_conf("USER_ID_HEADER"))
        if user_id:
            email = request.META.get(_conf("USER_EMAIL_HEADER"), "")
            roles_raw = request.META.get(_conf("USER_ROLES_HEADER"), "")
            roles = [r.strip() for r in roles_raw.split(",") if r.strip()]
            request.user = GatewayUser(user_id, email, roles)

        # -- Tenant (from API key validation) ----------------------------------
        tenant_id = request.META.get(_conf("TENANT_ID_HEADER"))
        if tenant_id:
            tenant_name = request.META.get(_conf("TENANT_NAME_HEADER"))
            request.tenant = GatewayTenant(tenant_id, tenant_name)
        else:
            request.tenant = None

        # -- Gateway routing metadata ------------------------------------------
        request.gateway = {
            "original_path": request.META.get("HTTP_X_GATEWAY_ORIGINAL_PATH"),
            "service": request.META.get("HTTP_X_GATEWAY_SERVICE"),
        }

        return self.get_response(request)
