"""
API key store and validation.

Keys are loaded from a JSON file at startup (and on demand via the admin API).
Each key maps to a tenant — an arbitrary label that identifies the caller at
the tenancy level, independent of the JWT user identity.

File format (api_keys.json):
    [
      {"key": "sk-acme-abc123", "tenant_id": "acme", "tenant_name": "ACME Corp"},
      {"key": "sk-beta-xyz789", "tenant_id": "beta"}
    ]

The "tenant_name" field is optional.  Only "key" and "tenant_id" are required.

Injected upstream headers (when a valid key is presented):
    X-Tenant-Id:   tenant_id value
    X-Tenant-Name: tenant_name value  (omitted when not set on the key entry)

The raw API-key header is always stripped from the forwarded request so the
upstream never sees the secret.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request

import gateway.labels as lbl
from gateway.settings import settings

logger = logging.getLogger(__name__)

# raw_key → {"tenant_id": ..., "tenant_name": ...}
_store: dict[str, dict] = {}


# ── Loading ────────────────────────────────────────────────────────────────────

def load_api_keys() -> int:
    """Load (or reload) API keys from the configured file.  Returns key count."""
    global _store
    path = Path(settings.api_keys_path)
    if not path.exists():
        logger.debug(f"API keys file not found: {path} — no keys loaded")
        _store = {}
        return 0
    try:
        entries = json.loads(path.read_text())
        store: dict[str, dict] = {}
        for entry in entries:
            key = entry.get("key")
            tenant_id = entry.get("tenant_id")
            if not key or not tenant_id:
                logger.warning(f"API keys file: entry missing 'key' or 'tenant_id', skipped: {entry}")
                continue
            store[key] = {
                "tenant_id": tenant_id,
                "tenant_name": entry.get("tenant_name"),
            }
        _store = store
        logger.info(f"Loaded {len(_store)} API key(s) from {path}")
        return len(_store)
    except Exception as e:
        logger.error(f"Failed to load API keys from {path}: {e}")
        return 0


def lookup(raw_key: str) -> Optional[dict]:
    """Return the tenant dict for a valid key, or None if not found."""
    return _store.get(raw_key)


def count() -> int:
    return len(_store)


# ── Per-request check ──────────────────────────────────────────────────────────

def _requires_api_key(route: dict, labels: dict) -> bool:
    """Check per-service label first, then fall back to global setting."""
    override = lbl.get(labels, "api_key.required")
    if override is not None:
        return override.lower() == "true"
    return settings.api_key_required


def check_api_key(request: Request, route: dict, labels: dict) -> Optional[dict]:
    """
    Validate the API key on the request.

    Returns:
        tenant dict  — key was present and valid
        None         — key not required and not present

    Raises:
        HTTPException 401 — key required but missing, or present but invalid
    """
    raw_key = request.headers.get(settings.api_key_header.lower())
    required = _requires_api_key(route, labels)

    if not raw_key:
        if required:
            raise HTTPException(
                status_code=401,
                detail=f"Missing {settings.api_key_header} header",
            )
        return None

    tenant = lookup(raw_key)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return tenant
