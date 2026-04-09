from typing import Optional
from gateway.settings import settings


def _ns() -> Optional[str]:
    return settings.namespace


def get(labels: dict, key: str) -> Optional[str]:
    """
    Legge gateway.{namespace}.{key} con fallback a gateway.{key}.
    Se namespace non è configurato, legge direttamente gateway.{key}.
    """
    ns = _ns()
    if ns:
        val = labels.get(f"gateway.{ns}.{key}")
        if val is not None:
            return val
    return labels.get(f"gateway.{key}")


def get_default(labels: dict, key: str, default: str) -> str:
    val = get(labels, key)
    return val if val is not None else default


def is_enabled(labels: dict) -> bool:
    """
    enable è namespace-strict: nessun fallback.
    gateway.{ns}.enable deve essere esplicitamente "true".
    Senza namespace, legge gateway.enable.
    """
    ns = _ns()
    key = f"gateway.{ns}.enable" if ns else "gateway.enable"
    return labels.get(key, "false").lower() == "true"


def has_prefix(labels: dict, prefix: str) -> bool:
    """Verifica se esiste almeno una label gateway.{ns}.{prefix}* o gateway.{prefix}*."""
    ns = _ns()
    for k in labels:
        if ns and k.startswith(f"gateway.{ns}.{prefix}"):
            return True
        if k.startswith(f"gateway.{prefix}"):
            return True
    return False


def parse_csv(labels: dict, key: str) -> list[str]:
    val = get(labels, key) or ""
    return [v.strip() for v in val.split(",") if v.strip()]
