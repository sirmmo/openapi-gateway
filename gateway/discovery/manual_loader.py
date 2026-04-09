import json
import logging
from pathlib import Path
from gateway.settings import settings
from gateway.config.schema import GatewayConfig, ServiceConfig
from gateway.discovery.openapi_fetcher import fetch_and_register

logger = logging.getLogger(__name__)


async def load_manual_config():
    path = Path(settings.config_path)
    if not path.exists():
        logger.info("No services.json found, skipping manual config")
        return
    try:
        data = json.loads(path.read_text())
        config = GatewayConfig(**data)
    except Exception as e:
        logger.error(f"Failed to parse services.json: {e}")
        return

    for svc in config.services:
        labels = _service_to_labels(svc)
        await fetch_and_register(f"manual:{svc.name}", labels)
        logger.info(f"Loaded manual service: {svc.name}")


async def reload_manual_config():
    from gateway.registry.route_registry import registry
    manual_ids = [sid for sid in registry._store if sid.startswith("manual:")]
    for sid in manual_ids:
        registry.deregister(sid)
    await load_manual_config()


def _service_to_labels(svc: ServiceConfig) -> dict:
    """
    Genera label senza namespace: il services.json è già per-gateway,
    quindi gateway.enable (senza namespace) è corretto qui.
    """
    labels = {
        "gateway.enable": "true",
        "gateway.name": svc.name,
        "gateway.port": str(svc.port),
        "gateway.docs": svc.docs_path,
    }
    if svc.host:
        labels["gateway.host"] = svc.host
    if svc.prefix is not None:
        labels["gateway.prefix"] = svc.prefix
    if svc.auth_required is not None:
        labels["gateway.auth.required"] = str(svc.auth_required).lower()
    if svc.auth_override_paths:
        labels["gateway.auth.override.paths"] = ",".join(svc.auth_override_paths)
    if svc.filter:
        if svc.filter.tags:
            labels["gateway.filter.tags"] = ",".join(svc.filter.tags)
        if svc.filter.paths:
            labels["gateway.filter.paths"] = ",".join(svc.filter.paths)
        if svc.filter.operations:
            labels["gateway.filter.operations"] = ",".join(svc.filter.operations)
    if svc.exclude:
        if svc.exclude.tags:
            labels["gateway.exclude.tags"] = ",".join(svc.exclude.tags)
        if svc.exclude.paths:
            labels["gateway.exclude.paths"] = ",".join(svc.exclude.paths)
        if svc.exclude.operations:
            labels["gateway.exclude.operations"] = ",".join(svc.exclude.operations)
    return labels
