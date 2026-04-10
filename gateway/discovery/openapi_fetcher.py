import httpx
import asyncio
import logging
import re
from gateway.settings import settings
from gateway.registry.route_registry import registry
from gateway.registry.filter import parse_labels, FilterMode, apply_filter
import gateway.labels as lbl

logger = logging.getLogger(__name__)


def _path_to_glob(path: str) -> str:
    """Convert OpenAPI path params like {id} to fnmatch wildcards."""
    return re.sub(r'\{[^}]+\}', '*', path)


def _resolve_service_name(labels: dict, container) -> str:
    return (
        lbl.get(labels, "name")
        or labels.get("com.docker.compose.service")
        or (container.name.lstrip("/") if container else "unknown")
    )


def _resolve_base_url(labels: dict, container) -> str:
    host = lbl.get(labels, "host")
    port = lbl.get_default(labels, "port", "8000")
    if host:
        return f"http://{host}:{port}"
    name = _resolve_service_name(labels, container)
    return f"http://{name}:{port}"


def _resolve_prefix(labels: dict, service_name: str) -> str | None:
    val = (lbl.get(labels, "prefix") or "").strip()
    if not val:
        return None
    if val.lower() == "true":
        return f"/{service_name}"
    return "/" + val.strip("/")


def _extract_routes(spec: dict, base_url: str, prefix: str | None, filter_spec) -> list[dict]:
    routes = []
    for path, methods in spec.get("paths", {}).items():
        for method, operation in methods.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}:
                continue
            raw_exposed = f"{prefix}{path}" if prefix else path
            route = {
                "path": path,
                "exposed_path": _path_to_glob(raw_exposed),
                "method": method.upper(),
                "operationId": operation.get("operationId", ""),
                "tags": operation.get("tags", []),
                "base_url": base_url,
                "prefix": prefix,
                "summary": operation.get("summary", ""),
            }
            if apply_filter(filter_spec, route):
                routes.append(route)
    return routes


async def fetch_and_register(service_id: str, labels: dict, container=None):
    service_name = _resolve_service_name(labels, container)
    base_url = _resolve_base_url(labels, container)
    docs_path = lbl.get_default(labels, "docs", settings.docs_default)
    url = f"{base_url}{docs_path}"
    prefix = _resolve_prefix(labels, service_name)
    filter_spec = parse_labels(labels)

    if filter_spec.mode == FilterMode.ERROR:
        logger.warning(
            f"Service '{service_name}' ha sia filter che exclude — "
            "tutte le route BLOCCATE fino a correzione configurazione."
        )
        registry.register_error(service_id, labels, service_name, reason="filter_conflict")
        return

    for attempt in range(settings.discovery_retry_attempts):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=5.0)
                response.raise_for_status()
                openapi_spec = response.json()
                routes = _extract_routes(openapi_spec, base_url, prefix, filter_spec)
                registry.register(service_id, routes, labels, service_name, raw_spec=openapi_spec)
                logger.info(
                    f"Registered {len(routes)} routes for '{service_name}' "
                    f"({'prefix: ' + prefix if prefix else 'no prefix'})"
                )
                return
        except Exception as e:
            wait = settings.discovery_retry_backoff ** attempt
            logger.warning(f"Attempt {attempt+1} failed for {url}: {e}. Retry in {wait:.1f}s")
            await asyncio.sleep(wait)

    logger.error(f"Failed to fetch OpenAPI spec for '{service_name}' after all attempts")
