import time
import fnmatch
import httpx
from fastapi import Request, HTTPException
from jose import jwt, JWTError
from gateway.settings import settings
import gateway.labels as lbl

_jwks_cache: dict = {}
_jwks_fetched_at: float = 0.0


async def _get_jwks() -> dict:
    global _jwks_cache, _jwks_fetched_at
    now = time.monotonic()
    if not _jwks_cache or (now - _jwks_fetched_at) > settings.auth_jwks_ttl_seconds:
        if settings.auth_jwks_url:
            async with httpx.AsyncClient() as client:
                r = await client.get(settings.auth_jwks_url)
                _jwks_cache = r.json()
                _jwks_fetched_at = now
    return _jwks_cache


def _parse_override_paths(labels: dict) -> list[dict]:
    raw = lbl.parse_csv(labels, "auth.override.paths")
    result = []
    for entry in raw:
        if ":" in entry:
            method, path = entry.split(":", 1)
            result.append({"method": method.upper(), "path": path})
        else:
            result.append({"method": "*", "path": entry})
    return result


def _is_override_path(route: dict, overrides: list[dict]) -> bool:
    for o in overrides:
        path_match = fnmatch.fnmatch(route["path"], o["path"])
        method_match = o["method"] == "*" or o["method"] == route["method"].upper()
        if path_match and method_match:
            return True
    return False


def _requires_auth(route: dict, labels: dict) -> bool:
    if not settings.auth_required:
        return False
    auth_required = lbl.get(labels, "auth.required")
    if auth_required is not None and auth_required.lower() == "false":
        return False
    overrides = _parse_override_paths(labels)
    if _is_override_path(route, overrides):
        return False
    return True


def inject_claims_headers(headers: dict, claims: dict) -> dict:
    headers["X-User-Id"] = str(claims.get(settings.auth_claim_id, ""))
    headers["X-User-Email"] = str(claims.get(settings.auth_claim_email, ""))
    roles = claims.get(settings.auth_claim_roles, [])
    headers["X-User-Roles"] = ",".join(roles) if isinstance(roles, list) else str(roles)
    return headers


async def check_auth(request: Request, route: dict, labels: dict) -> dict | None:
    """
    Ritorna claims JWT se auth ok, None se non richiesta.
    Solleva HTTPException 401 se auth fallisce.
    """
    if not _requires_auth(route, labels):
        return None

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token = auth_header.removeprefix("Bearer ").strip()

    try:
        jwks = await _get_jwks()
        claims = jwt.decode(token, jwks, options={"verify_aud": False})
        return claims
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")
