import logging
import httpx
from fastapi import Request, Response
from gateway.registry.route_registry import registry
from gateway.auth.middleware import check_auth, inject_claims_headers
from gateway.auth.api_keys import check_api_key
from gateway.settings import settings

logger = logging.getLogger(__name__)

# Headers that must not be forwarded to upstream
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "transfer-encoding", "te",
    "trailers", "upgrade", "proxy-authorization", "proxy-authenticate",
    "content-length",  # let httpx set this from the actual body
})


def _strip_prefix(path: str, prefix: str) -> str:
    if prefix and path.startswith(prefix):
        stripped = path[len(prefix):]
        return stripped if stripped.startswith("/") else f"/{stripped}"
    return path


async def forward_request(request: Request, path: str) -> Response:
    result = registry.resolve(path, request.method)

    if result is None:
        return Response(content="Not Found", status_code=404)

    route, labels, service_name = result
    claims = await check_auth(request, route, labels)
    tenant = check_api_key(request, route, labels)

    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    headers.pop("host", None)

    prefix = route.get("prefix")
    upstream_path = _strip_prefix(path, prefix) if prefix else path
    if prefix:
        headers["X-Gateway-Original-Path"] = path
        headers["X-Gateway-Service"] = service_name

    if settings.auth_mode == "validate" and claims is not None:
        headers.pop("authorization", None)
        headers = inject_claims_headers(headers, claims)

    if tenant is not None:
        # Strip the raw key — upstream never sees the secret
        headers.pop(settings.api_key_header.lower(), None)
        headers["X-Tenant-Id"] = tenant["tenant_id"]
        if tenant.get("tenant_name"):
            headers["X-Tenant-Name"] = tenant["tenant_name"]

    target_url = f"{route['base_url']}{upstream_path}"
    body = await request.body()

    try:
        async with httpx.AsyncClient() as client:
            upstream = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body,
                params=request.query_params,
                timeout=30.0,
            )
    except httpx.TimeoutException:
        logger.warning(f"Upstream timeout: {request.method} {target_url}")
        return Response(content="Gateway Timeout", status_code=504)
    except httpx.RequestError as e:
        logger.warning(f"Upstream connection error: {e}")
        return Response(content="Bad Gateway", status_code=502)

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=dict(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )
