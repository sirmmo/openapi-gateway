# openapi-gateway

[![CI](https://github.com/sirmmo/openapi-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/sirmmo/openapi-gateway/actions/workflows/ci.yml)
[![Image](https://ghcr-badge.egpl.dev/sirmmo/openapi-gateway/latest_tag?label=ghcr.io)](https://github.com/sirmmo/openapi-gateway/pkgs/container/openapi-gateway)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

A lightweight, self-configuring API gateway that discovers services via Docker events, introspects their OpenAPI specs, and proxies requests — with per-service filtering, prefix routing, pluggable authentication, and multi-gateway namespace support.

**No config files to maintain per service. No restarts required.**

---

## Table of contents

- [How it works](#how-it-works)
- [Quickstart](#quickstart)
- [Namespaces](#namespaces)
- [Label reference](#label-reference)
- [Authentication modes](#authentication-modes)
- [External services](#external-services-servicesjson)
- [Admin endpoints](#admin-endpoints)
- [Error states](#error-states)
- [Environment variables](#environment-variables)
- [Architecture](#architecture)

---

## How it works

1. Services join the Docker network and add `gateway.enable: "true"` (or `gateway.{namespace}.enable: "true"`) to their labels
2. The gateway listens to Docker events and fetches `/openapi.json` from each new service
3. Routes are registered in-memory, filtered by label configuration
4. Incoming requests are authenticated (optional) and forwarded upstream
5. External services can be registered manually via `services.json`

---

## Quickstart

```bash
curl -O https://raw.githubusercontent.com/sirmmo/openapi-gateway/main/docker-compose.yml
cp .env.example .env   # set JWKS_URL, ADMIN_SECRET, etc.
docker compose up -d
```

The `docker-compose.yml` pulls `ghcr.io/sirmmo/openapi-gateway:latest` directly — no build step needed.

Your gateway is now listening on `http://localhost:8000`.

To expose a service, add labels to its container:

```yaml
services:
  my-api:
    image: my-api:latest
    networks:
      - gateway_net
    labels:
      gateway.enable: "true"
      gateway.port: "8080"
```

---

## Namespaces

A single codebase can run multiple gateway instances, each serving a different audience (public, internal, admin, etc.). Each instance is assigned a namespace via `GATEWAY_NAMESPACE`.

**Label resolution order:**

```
gateway.{namespace}.{key}   →  found    →  use it
gateway.{namespace}.{key}   →  missing  →  fallback to gateway.{key}
gateway.{key}               →  missing  →  use hardcoded default
```

> **`enable` has no fallback.** A service must explicitly opt in to each gateway namespace.

**Example — one service on two gateways:**

```yaml
labels:
  # Public gateway (GATEWAY_NAMESPACE=public)
  gateway.public.enable: "true"
  gateway.public.filter.tags: "public"
  gateway.public.prefix: "/api"
  gateway.public.auth.required: "true"

  # Internal gateway (GATEWAY_NAMESPACE=internal)
  gateway.internal.enable: "true"
  gateway.internal.auth.required: "false"

  # Shared defaults (fallback for both)
  gateway.port: "8080"
  gateway.docs: "/openapi.json"
```

When `GATEWAY_NAMESPACE` is absent, the gateway reads only `gateway.*` labels (legacy mode).

<details>
<summary>Multi-gateway docker-compose example</summary>

```yaml
services:
  gateway-public:
    image: ghcr.io/sirmmo/openapi-gateway:latest
    ports:
      - "8000:8000"
    environment:
      - GATEWAY_NAMESPACE=public
      - GATEWAY_AUTH_REQUIRED=true
      - GATEWAY_CONFIG_PATH=/config/public-services.json
      - GATEWAY_ADMIN_SECRET=${ADMIN_SECRET}
    volumes:
      - ./config:/config
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - gateway_net

  gateway-internal:
    image: ghcr.io/sirmmo/openapi-gateway:latest
    ports:
      - "8001:8000"
    environment:
      - GATEWAY_NAMESPACE=internal
      - GATEWAY_AUTH_REQUIRED=false
      - GATEWAY_CONFIG_PATH=/config/internal-services.json
      - GATEWAY_ADMIN_SECRET=${ADMIN_SECRET}
    volumes:
      - ./config:/config
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - gateway_net
```

</details>

---

## Label reference

### Identity & host resolution

| Label | Default | Description |
|---|---|---|
| `gateway.{ns}.enable` | — | Set to `"true"` to enable discovery. **No namespace fallback.** |
| `gateway.{ns}.port` | `8000` | Port the service listens on |
| `gateway.{ns}.host` | — | Explicit hostname (overrides DNS name resolution) |
| `gateway.{ns}.name` | auto | Service name override |
| `gateway.{ns}.docs` | `/openapi.json` | Path to the OpenAPI schema |

All labels above support the `gateway.{key}` fallback (except `enable`).

**Name resolution order:** `gateway.{ns}.name` → `gateway.name` → `com.docker.compose.service` → container name

**Host resolution order:** `gateway.{ns}.host:{port}` → `{name}:{port}`

### Prefix routing

| Label value | Exposed path |
|---|---|
| `gateway.{ns}.prefix: "true"` | `/{service-name}/{original-path}` |
| `gateway.{ns}.prefix: "/api/v1"` | `/api/v1/{original-path}` |
| *(absent)* | `/{original-path}` (conflict → error) |

When a prefix is active, the gateway strips it before forwarding and injects:

```
X-Gateway-Original-Path: /api/v1/users/42
X-Gateway-Service: my-api
```

### Filtering

`filter` and `exclude` are **mutually exclusive**. Setting both blocks the service with a `filter_conflict` error.

**Allowlist** — expose only matching routes:

```yaml
gateway.{ns}.filter.tags: "public,v2"
gateway.{ns}.filter.paths: "/api/v1/*,/health"
gateway.{ns}.filter.operations: "getUser,listItems"
```

**Denylist** — expose everything except matching routes:

```yaml
gateway.{ns}.exclude.tags: "internal,debug"
gateway.{ns}.exclude.paths: "/admin/*,/internal/*"
gateway.{ns}.exclude.operations: "deleteEverything"
```

- Values are comma-separated
- Paths support glob patterns (`*`)
- A route matches if **any** criterion matches (OR logic within each label)

### Authentication

| Label | Default | Description |
|---|---|---|
| `gateway.{ns}.auth.required` | inherits global | Override auth requirement for this service |
| `gateway.{ns}.auth.override.paths` | — | Paths exempt from auth |

**`auth.override.paths` format:**

```yaml
# All methods
gateway.{ns}.auth.override.paths: "/health,/status"

# Specific method
gateway.{ns}.auth.override.paths: "GET:/items/*,POST:/public/register"

# Mixed
gateway.{ns}.auth.override.paths: "/health,GET:/items/*"
```

**Auth decision order (first match wins):**

1. `GATEWAY_AUTH_REQUIRED=false` → all routes open (global)
2. `gateway.{ns}.auth.required: "false"` → all routes open (per service)
3. `gateway.{ns}.auth.override.paths` match → this route open
4. *(default)* → auth required

---

## Authentication modes

Set via `GATEWAY_AUTH_MODE`.

### `relay`

Validates the JWT, then passes the original `Authorization: Bearer ...` header downstream unchanged.

### `validate` (default)

Validates the JWT, removes the `Authorization` header, and injects claims as headers:

```
X-User-Id:     <sub claim>
X-User-Email:  <email claim>
X-User-Roles:  <comma-separated roles>
```

Claim names are configurable via `GATEWAY_AUTH_CLAIM_*` environment variables.

---

## External services (`services.json`)

`services.json` is per-gateway — each instance loads its own file via `GATEWAY_CONFIG_PATH`. No namespace field is needed.

```json
{
  "services": [
    {
      "name": "legacy-crm",
      "host": "crm.internal",
      "port": 8080,
      "docs_path": "/api/openapi.json",
      "prefix": "/crm",
      "auth_required": false,
      "exclude": {
        "paths": ["/admin/*", "/internal/*"]
      }
    },
    {
      "name": "public-api",
      "host": "public.internal",
      "port": 9000,
      "prefix": "true",
      "auth_required": true,
      "auth_override_paths": ["/health", "GET:/items/*"],
      "filter": {
        "tags": ["public"],
        "operations": ["getStatus", "listItems"]
      }
    }
  ]
}
```

Reload without restart:

```bash
curl -X POST http://localhost:8000/_gateway/reload \
  -H "X-Gateway-Admin-Secret: your-secret"
```

---

## Admin endpoints

All admin endpoints require the `X-Gateway-Admin-Secret` header.  
If `GATEWAY_ADMIN_SECRET` is not set, all admin endpoints return `503`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/_gateway/status` | Service registry status |
| `GET` | `/_gateway/routes` | All registered routes |
| `POST` | `/_gateway/reload` | Reload `services.json` |

<details>
<summary>Example status response</summary>

```json
{
  "namespace": "public",
  "services": {
    "my-api": {
      "service_id": "abc123",
      "routes": 12,
      "error": false,
      "error_reason": null,
      "labels": {
        "gateway.public.enable": "true",
        "gateway.port": "8080",
        "gateway.public.filter.tags": "public"
      }
    },
    "broken-service": {
      "service_id": "def456",
      "routes": 0,
      "error": true,
      "error_reason": "filter_conflict",
      "labels": {
        "gateway.public.enable": "true",
        "gateway.public.filter.tags": "public",
        "gateway.public.exclude.paths": "/admin/*"
      }
    }
  }
}
```

</details>

---

## Error states

| `error_reason` | Cause | Effect |
|---|---|---|
| `filter_conflict` | Both `filter.*` and `exclude.*` set for the same gateway | All routes blocked |
| `path_conflict` | `exposed_path` collides with an already-registered route | Incoming service blocked; existing service unaffected |
| *(not registered)* | OpenAPI fetch failed after all retries | Service absent from registry |

Resolve the configuration error and restart the affected container — the gateway will re-discover it automatically.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `GATEWAY_NAMESPACE` | — | Label namespace (`gateway.{ns}.*`). Absent = legacy mode |
| `GATEWAY_AUTH_JWKS_URL` | — | JWKS endpoint of your identity provider |
| `GATEWAY_AUTH_REQUIRED` | `true` | Global auth on/off |
| `GATEWAY_AUTH_MODE` | `validate` | `relay` or `validate` |
| `GATEWAY_AUTH_JWKS_TTL_SECONDS` | `300` | JWKS cache TTL in seconds |
| `GATEWAY_AUTH_CLAIM_ID` | `sub` | JWT claim → `X-User-Id` |
| `GATEWAY_AUTH_CLAIM_EMAIL` | `email` | JWT claim → `X-User-Email` |
| `GATEWAY_AUTH_CLAIM_ROLES` | `roles` | JWT claim → `X-User-Roles` |
| `GATEWAY_ADMIN_SECRET` | — | Required to access `/_gateway/*` endpoints |
| `GATEWAY_DOCS_DEFAULT` | `/openapi.json` | Default OpenAPI schema path |
| `GATEWAY_CONFIG_PATH` | `/config/services.json` | Path to manual service registry |
| `GATEWAY_DOCKER_SOCKET` | `unix://var/run/docker.sock` | Docker socket path |
| `GATEWAY_DISCOVERY_RETRY_ATTEMPTS` | `5` | OpenAPI fetch retry count |
| `GATEWAY_DISCOVERY_RETRY_BACKOFF` | `2.0` | Exponential backoff base (seconds) |
| `GATEWAY_LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

<details>
<summary>JWKS URLs for common identity providers</summary>

| Provider | JWKS URL |
|---|---|
| Keycloak | `https://{host}/realms/{realm}/protocol/openid-connect/certs` |
| Zitadel | `https://{host}/oauth/v2/keys` |
| Auth0 | `https://{domain}/.well-known/jwks.json` |
| Clerk | `https://{domain}/.well-known/jwks.json` |

</details>

---

## Architecture

```
                         ┌─────────────────────────────────────────────────┐
                         │                 Docker Network                   │
                         │                                                  │
  Internet               │  ┌─────────────┐     ┌──────────────────────┐  │
────────────► Traefik ───┼─►│   gateway   │────►│   service-a          │  │
  (TLS, ingress)         │  │   public    │     │  gateway.public.*    │  │
                         │  │   :8000     │     │  gateway.internal.*  │  │
                         │  └─────────────┘     ├──────────────────────┤  │
  Internal               │  ┌─────────────┐────►│   service-b          │  │
─────────────────────────┼─►│   gateway   │     │  gateway.internal.*  │  │
                         │  │   internal  │     └──────────────────────┘  │
                         │  │   :8001     │                                │
                         │  └─────────────┘                                │
                         │        │                                        │
                         │  ┌─────▼──────────────────────────────────┐    │
                         │  │  Docker Events API                     │    │
                         │  │  container start / stop / update       │    │
                         │  └────────────────────────────────────────┘    │
                         └─────────────────────────────────────────────────┘

  Request flow:
  client → Traefik → gateway (auth + routing) → upstream service
```

**Components:**

| Module | Responsibility |
|---|---|
| `labels.py` | Label resolution with namespace + fallback logic |
| `docker_watcher` | Listens to Docker events, triggers discovery on container lifecycle changes |
| `openapi_fetcher` | Fetches `/openapi.json`, applies filters, registers routes |
| `route_registry` | Thread-safe in-memory store; resolves incoming paths, detects conflicts |
| `forwarder` | Proxies requests via `httpx`, strips prefix, injects headers |
| `auth/middleware` | Validates JWT via JWKS; injects claims in `validate` mode |
| `manual_loader` | Loads `services.json`, normalizes to the same label format as Docker labels |
| `admin` | `/_gateway/*` endpoints for observability and hot-reload |

The route registry is in-memory per instance by design. Each gateway instance independently discovers all services on the Docker network — no external state store required.

**Requirements:** Python 3.11+, Docker with API access (`/var/run/docker.sock`), services must expose an OpenAPI 3.x schema.

---

## License

[Apache 2.0](LICENSE)
