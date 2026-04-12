# django-openapi-gateway

Django middleware for services running behind [openapi-gateway](https://github.com/sirmmo/openapi-gateway).

Reads the identity and tenancy headers injected by the gateway and exposes them as first-class objects on `request.user` and `request.tenant`.

## Install

```bash
pip install django-openapi-gateway
# with DRF support:
pip install "django-openapi-gateway[drf]"
```

Or directly from the repo:

```bash
pip install "git+https://github.com/sirmmo/openapi-gateway.git#subdirectory=clients"
```

## Setup

Add the middleware **after** Django's own `AuthenticationMiddleware`:

```python
MIDDLEWARE = [
    ...
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_gateway.middleware.GatewayMiddleware",   # ← after
]
```

No `INSTALLED_APPS` entry required.

## What it does

When a request arrives through the gateway, the middleware populates:

| Attribute | Type | Source header | Present when |
|---|---|---|---|
| `request.user` | `GatewayUser` | `X-User-Id/Email/Roles` | JWT was validated |
| `request.tenant` | `GatewayTenant` | `X-Tenant-Id/Name` | API key was validated |
| `request.gateway` | `dict` | `X-Gateway-*` | Behind a prefixed gateway |

When running without the gateway (local dev, tests) the middleware is a no-op — `request.user` is left to Django's normal auth stack and `request.tenant` is `None`.

### GatewayUser

```python
request.user.id                        # "sub-claim-value"
request.user.email                     # "alice@example.com"
request.user.roles                     # ["admin", "editor"]
request.user.is_authenticated          # True
request.user.has_role("admin")         # True / False
request.user.has_any_role("a", "b")    # True if any match
request.user.has_all_roles("a", "b")   # True if all match
```

### GatewayTenant

```python
request.tenant.id    # "acme"
request.tenant.name  # "ACME Corp"
```

### Gateway metadata

```python
request.gateway["original_path"]   # "/api/users/1"  (before prefix strip)
request.gateway["service"]         # "user-service"
```

## Decorators

```python
from django_gateway import require_gateway_auth, require_tenant, require_role, require_all_roles

@require_gateway_auth          # 401 if not a gateway-validated user
def my_view(request): ...

@require_tenant                # 403 if no API key / tenant context
def tenant_view(request): ...

@require_role("admin")         # 403 if user lacks the role
def admin_view(request): ...

@require_role("editor", "admin")     # 403 if user has neither role
def editor_view(request): ...

@require_all_roles("editor", "verified")   # 403 if user is missing any role
def strict_view(request): ...
```

All decorators return `{"detail": "..."}` JSON responses — no HTML login redirects.

**Class-based views** — use `method_decorator`:

```python
from django.utils.decorators import method_decorator

@method_decorator(require_tenant, name="dispatch")
class TenantView(View): ...
```

## Django REST Framework

```python
# settings.py
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "django_gateway.drf.GatewayAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "django_gateway.drf.IsGatewayAuthenticated",
    ],
}
```

Per-view overrides:

```python
from django_gateway.drf import (
    GatewayAuthentication,
    IsGatewayAuthenticated,
    HasTenant,
    HasRole,
    HasAllRoles,
)

class MyView(APIView):
    authentication_classes = [GatewayAuthentication]
    permission_classes = [IsGatewayAuthenticated, HasTenant]

class AdminView(APIView):
    authentication_classes = [GatewayAuthentication]
    permission_classes = [HasRole("admin")]
```

## Configuration

Header names can be overridden in `settings.py` when the gateway is configured with non-default values (`GATEWAY_AUTH_CLAIM_ID`, `GATEWAY_API_KEY_HEADER`, etc.):

```python
GATEWAY = {
    "USER_ID_HEADER":     "HTTP_X_USER_ID",      # default
    "USER_EMAIL_HEADER":  "HTTP_X_USER_EMAIL",    # default
    "USER_ROLES_HEADER":  "HTTP_X_USER_ROLES",    # default
    "TENANT_ID_HEADER":   "HTTP_X_TENANT_ID",     # default
    "TENANT_NAME_HEADER": "HTTP_X_TENANT_NAME",   # default
}
```

Keys are Django `request.META` names: uppercase, hyphens→underscores, `HTTP_` prefix.
