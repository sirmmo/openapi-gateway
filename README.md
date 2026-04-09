# openapi-gateway

> A lightweight, self-configuring API gateway that discovers services via Docker events, introspects their OpenAPI specs, and proxies requests — with per-service filtering, prefix routing, pluggable authentication, and multi-gateway namespace support.

---

## How it works

1. Services join the Docker network and add `gateway.enable: "true"` (or `gateway.{namespace}.enable: "true"`) to their labels
2. The gateway listens to Docker events and fetches `/openapi.json` from each new service
3. Routes are registered in-memory, filtered by label configuration
4. Incoming requests are authenticated (optional) and forwarded upstream
5. External services can be registered manually via `services.json`

No config files to maintain per-service. No restarts required.

---

## Quickstart

```bash
# 1. Clone
git clone https://github.com/your-org/openapi-gateway
cd openapi-gateway

# 2. Configure
cp .env.example .env
# edit .env: set JWKS_URL, ADMIN_SECRET, etc.

# 3. Run
docker compose up -d
```

Your gateway is now listening on `http://localhost:8000`.

To expose a service, add these labels to its container:

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

**Label resolution with namespace:**

```
gateway.{namespace}.{key}   →  found    →  use it
gateway.{namespace}.{key}   →  missing  →  fallback to gateway.{key}
gateway.{key}               →  missing  →  use hardcoded default
```

**`enable` has no fallback** — a service must explicitly opt in to each gateway:

```yaml
# This service is exposed on both gateways, with different config
labels:
  # Public gateway (GATEWAY_NAMESPACE=public)
  gateway.public.enable: "true"
  gateway.public.filter.tags: "public"
  gateway.public.prefix: "/api"
  gateway.public.auth.required: "true"

  # Internal gateway (GATEWAY_NAMESPACE=internal)
  gateway.internal.enable: "true"
  gateway.internal.auth.required: "false"

  # Shared defaults — used as fallback by both gateways
  gateway.port: "8080"
  gateway.docs: "/openapi.json"
```

**No namespace set** (`GATEWAY_NAMESPACE` absent): the gateway reads only `gateway.*` labels — fully backwards compatible.

### Multi-gateway docker-compose example

```yaml
services:
  gateway-public:
    build: .
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
    build: .
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

---

## Label reference

### Identity & host resolution

| Label | Default | Description |
|---|---|---|
| `gateway.{ns}.enable` | — | Set to `"true"` to enable discovery on this gateway. **No fallback.** |
| `gateway.{ns}.port` | `8000` | Port the service listens on |
| `gateway.{ns}.host` | — | Explicit hostname (for external services or override) |
| `gateway.{ns}.name` | auto | Service name override. Falls back to `com.docker.compose.service` then container name |
| `gateway.{ns}.docs` | `/openapi.json` | Path to the OpenAPI schema |

All labels above support the `gateway.{key}` fallback (except `enable`).

**Host resolution order:**

```
gateway.{ns}.host present  →  http://{host}:{port}
gateway.{ns}.host absent   →  http://{name}:{port}
                                  └─ gateway.{ns}.name
                                      └─ gateway.name
                                          └─ com.docker.compose.service
                                              └─ container name
```

### Prefix routing

| Label value | Exposed path |
|---|---|
| `gateway.{ns}.prefix: "true"` | `/{service-name}/{original-path}` |
| `gateway.{ns}.prefix: "/api/v1"` | `/api/v1/{original-path}` |
| *(absent)* | `/{original-path}` (conflict → error) |

When a prefix is active, the gateway strips it before forwarding and injects two headers:

```
X-Gateway-Original-Path: /api/v1/users/42
X-Gateway-Service: my-api
```

### Filtering

Filter and exclude are **mutually exclusive**. Setting both blocks the service (`filter_conflict`).

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

- Multiple values are comma-separated
- Paths support glob patterns (`*`)
- A route matches if **any** of the specified criteria match (OR logic within each label)
- `filter` and `exclude` are mutually exclusive — only one type may be present per service per gateway

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

**Auth hierarchy (first match wins):**

```
1. GATEWAY_AUTH_REQUIRED=false                   → all routes open (global)
2. gateway.{ns}.auth.required: "false"           → all routes open (per service)
3. gateway.{ns}.auth.override.paths match        → this route open
4. default                                       → auth required
```

---

## Authentication modes

Set via `GATEWAY_AUTH_MODE` environment variable.

### `relay` (default)

Gateway validates the JWT, then passes the `Authorization: Bearer ...` header downstream unchanged. Services can validate the token themselves or trust the gateway.

### `validate`

Gateway validates the JWT and removes the `Authorization` header. Claims are injected as headers:

```
X-User-Id:     <value of configured claim>
X-User-Email:  <value of configured claim>
X-User-Roles:  <comma-separated roles>
```

Claim names are configurable via environment variables.

---

## External services (`services.json`)

`services.json` is **per-gateway** by design — each gateway instance loads its own file via `GATEWAY_CONFIG_PATH`. No namespace field needed in the JSON.

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

**Example status response:**

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

---

## Error states

| `error_reason` | Cause | Effect |
|---|---|---|
| `filter_conflict` | Both `filter.*` and `exclude.*` set on the same gateway | All routes blocked |
| `path_conflict` | `exposed_path` collides with an already-registered route | Incoming service blocked, existing service unaffected |
| *(not registered)* | OpenAPI fetch failed after all retries | Service absent from registry |

Resolve configuration errors and restart the affected container — the gateway will re-discover it automatically.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `GATEWAY_NAMESPACE` | — | Label namespace (`gateway.{ns}.*`). Absent = legacy mode |
| `GATEWAY_AUTH_JWKS_URL` | — | JWKS endpoint of your identity provider |
| `GATEWAY_AUTH_REQUIRED` | `true` | Global auth on/off |
| `GATEWAY_AUTH_MODE` | `relay` | `relay` or `validate` |
| `GATEWAY_AUTH_JWKS_TTL_SECONDS` | `300` | JWKS cache TTL in seconds |
| `GATEWAY_AUTH_CLAIM_ID` | `sub` | JWT claim → `X-User-Id` |
| `GATEWAY_AUTH_CLAIM_EMAIL` | `email` | JWT claim → `X-User-Email` |
| `GATEWAY_AUTH_CLAIM_ROLES` | `roles` | JWT claim → `X-User-Roles` |
| `GATEWAY_ADMIN_SECRET` | — | Required to access `/_gateway/*` endpoints |
| `GATEWAY_DOCS_DEFAULT` | `/openapi.json` | Default OpenAPI schema path |
| `GATEWAY_CONFIG_PATH` | `/config/services.json` | Path to manual service registry |
| `GATEWAY_DOCKER_SOCKET` | `unix://var/run/docker.sock` | Docker socket |
| `GATEWAY_DISCOVERY_RETRY_ATTEMPTS` | `5` | OpenAPI fetch retry count |
| `GATEWAY_DISCOVERY_RETRY_BACKOFF` | `2.0` | Exponential backoff base (seconds) |

### Identity provider JWKS URLs

| Provider | JWKS URL |
|---|---|
| Keycloak | `https://{host}/realms/{realm}/protocol/openid-connect/certs` |
| Zitadel | `https://{host}/oauth/v2/keys` |
| Auth0 | `https://{domain}/.well-known/jwks.json` |
| Clerk | `https://{domain}/.well-known/jwks.json` |

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

- `labels.py` — centralizes label resolution with namespace + fallback logic
- `docker_watcher` — listens to Docker events, triggers discovery on container lifecycle changes
- `openapi_fetcher` — fetches and parses `/openapi.json`, applies filters, registers routes
- `route_registry` — in-memory store, resolves incoming paths, detects conflicts
- `forwarder` — proxies requests via `httpx`, handles prefix strip and header injection
- `auth/middleware` — validates JWT via JWKS, injects claims in `validate` mode
- `manual_loader` — loads `services.json`, normalizes to same label format as Docker labels
- `admin` — `/_gateway/*` endpoints for observability and hot-reload

---

## Scaling

The route registry is in-memory per instance. This is intentional: each gateway instance independently discovers all services on the Docker network and maintains a complete local registry. Docker's internal DNS and load balancing handle traffic distribution between gateway instances and between service replicas.

No external state store is required.

---

## Requirements

- Python 3.11+
- Docker with API access (`/var/run/docker.sock`)
- Services must expose an OpenAPI 3.x schema (FastAPI's `/openapi.json` works out of the box)

---

## License

MIT
