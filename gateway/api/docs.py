import re
import copy
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html

from gateway.registry.route_registry import registry
from gateway.settings import settings

router = APIRouter()

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def _safe_prefix(name: str) -> str:
    """Slugify a service name for use as a schema prefix."""
    return re.sub(r'[^a-zA-Z0-9]', '_', name)


def _rewrite_refs(obj, prefix: str):
    """Recursively rewrite #/components/schemas/X → #/components/schemas/prefix__X."""
    if isinstance(obj, dict):
        return {k: _rewrite_refs(v, prefix) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rewrite_refs(item, prefix) for item in obj]
    if isinstance(obj, str) and obj.startswith("#/components/schemas/"):
        name = obj[len("#/components/schemas/"):]
        return f"#/components/schemas/{prefix}__{name}"
    return obj


def build_merged_spec() -> dict:
    ns = settings.namespace
    title = f"OpenAPI Gateway{' [' + ns + ']' if ns else ''}"

    merged: dict = {
        "openapi": "3.1.0",
        "info": {"title": title, "version": "1.0.0"},
        "paths": {},
        "components": {"schemas": {}},
    }

    for service_name, raw_spec, routes in registry.service_specs():
        prefix = _safe_prefix(service_name)

        # Deep-copy and rewrite all $ref strings so schemas from different
        # services never collide in the merged components section.
        spec = _rewrite_refs(copy.deepcopy(raw_spec), prefix)

        spec_paths = spec.get("paths", {})

        # For each registered (and filtered) route, pull the operation from the
        # cached spec and place it under the gateway-exposed path.
        for route in routes:
            original_path = route["path"]
            service_prefix = route.get("prefix")
            exposed_path = f"{service_prefix}{original_path}" if service_prefix else original_path
            method = route["method"].lower()

            path_item = spec_paths.get(original_path, {})
            if method not in path_item:
                continue

            if exposed_path not in merged["paths"]:
                # Carry over path-level fields (e.g. shared parameters).
                path_level = {k: v for k, v in path_item.items() if k not in _HTTP_METHODS}
                merged["paths"][exposed_path] = path_level

            merged["paths"][exposed_path][method] = path_item[method]

        # Merge schemas under a service-namespaced key.
        for name, schema in spec.get("components", {}).get("schemas", {}).items():
            merged["components"]["schemas"][name] = schema

    # Drop empty components to keep the output clean.
    if not merged["components"]["schemas"]:
        del merged["components"]

    return merged


@router.get(settings.docs_default, include_in_schema=False)
async def openapi_json():
    from fastapi.responses import JSONResponse
    return JSONResponse(build_merged_spec())


@router.get("/docs", include_in_schema=False)
async def swagger_ui() -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url=settings.docs_default,
        title=f"OpenAPI Gateway{' [' + settings.namespace + ']' if settings.namespace else ''} — Swagger UI",
    )


@router.get("/redoc", include_in_schema=False)
async def redoc() -> HTMLResponse:
    return get_redoc_html(
        openapi_url=settings.docs_default,
        title=f"OpenAPI Gateway{' [' + settings.namespace + ']' if settings.namespace else ''} — ReDoc",
    )
