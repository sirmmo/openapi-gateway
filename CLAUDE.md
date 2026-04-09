# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the gateway

```bash
# Build and start
docker compose up -d

# Run locally without Docker (requires a running Docker socket)
pip install -e .
uvicorn gateway.main:app --host 0.0.0.0 --port 8000 --reload
```

There are no automated tests in this repository yet.

## Architecture

This is a **self-configuring API gateway** built with FastAPI. It discovers backend services via Docker events, introspects their OpenAPI specs, and proxies requests with optional JWT authentication.

### Request flow

```
client → forward_request() → registry.resolve() → check_auth() → httpx upstream
```

### Key components

- **`gateway/labels.py`** — All label reads go through here. Implements the `gateway.{namespace}.{key}` → `gateway.{key}` fallback logic. The `enable` key has no fallback (namespace-strict).
- **`gateway/settings.py`** — Pydantic-settings config, all env vars prefixed `GATEWAY_`.
- **`gateway/registry/route_registry.py`** — Thread-safe in-memory store of `ServiceEntry` objects. Detects `path_conflict` on registration. `resolve()` uses `fnmatch` to match incoming paths.
- **`gateway/registry/filter.py`** — Parses `filter.*` / `exclude.*` labels into a `FilterSpec`. `filter` and `exclude` are mutually exclusive; both present → `FilterMode.ERROR`.
- **`gateway/discovery/docker_watcher.py`** — Streams Docker events; triggers `fetch_and_register` on container start/update, `registry.deregister` on stop/die.
- **`gateway/discovery/openapi_fetcher.py`** — Fetches `/openapi.json` from a service, applies the filter, builds route dicts, calls `registry.register`. Retries with exponential backoff.
- **`gateway/discovery/manual_loader.py`** — Loads `services.json` at startup. Converts `ServiceConfig` to the same label dict format that Docker-discovered services use, so all downstream logic is unified.
- **`gateway/auth/middleware.py`** — Validates Bearer JWT against a JWKS endpoint (cached per TTL). In `validate` mode, strips the `Authorization` header and injects `X-User-Id`, `X-User-Email`, `X-User-Roles`.
- **`gateway/proxy/forwarder.py`** — Strips the prefix from the path, injects gateway headers, proxies via `httpx`.
- **`gateway/api/admin.py`** — `/_gateway/status`, `/_gateway/routes`, `/_gateway/reload` — all gated by `X-Gateway-Admin-Secret`.

### Route dict schema

Each route stored in the registry has this shape:

```python
{
    "path": "/users/{id}",          # original path from OpenAPI spec
    "exposed_path": "/api/users/*", # path clients actually call (with prefix)
    "method": "GET",
    "operationId": "getUser",
    "tags": ["users"],
    "base_url": "http://my-api:8080",
    "prefix": "/api",               # None if no prefix
    "summary": "...",
}
```

### Namespace logic

When `GATEWAY_NAMESPACE=public` is set, label resolution tries `gateway.public.{key}` first and falls back to `gateway.{key}`. The `enable` key never falls back — a service must explicitly set `gateway.public.enable: "true"` to appear on the public gateway. With no namespace set, only `gateway.*` labels are read (legacy mode).

### Manual services vs Docker services

`services.json` entries are converted to the same label dict format by `_service_to_labels()` in `manual_loader.py`. They always use bare `gateway.*` keys (no namespace), because the file is per-gateway by design. The service ID is prefixed with `manual:` (e.g. `manual:legacy-crm`).
