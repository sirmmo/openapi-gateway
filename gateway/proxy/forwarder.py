import httpx
from fastapi import Request, Response
from gateway.registry.route_registry import registry
from gateway.auth.middleware import check_auth, inject_claims_headers
from gateway.settings import settings


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

    headers = dict(request.headers)
    headers.pop("host", None)

    prefix = route.get("prefix")
    upstream_path = _strip_prefix(path, prefix) if prefix else path
    if prefix:
        headers["X-Gateway-Original-Path"] = path
        headers["X-Gateway-Service"] = service_name

    if settings.auth_mode == "validate" and claims is not None:
        headers.pop("authorization", None)
        headers = inject_claims_headers(headers, claims)

    target_url = f"{route['base_url']}{upstream_path}"
    body = await request.body()

    async with httpx.AsyncClient() as client:
        upstream = await client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body,
            params=request.query_params,
            timeout=30.0,
        )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=dict(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )
