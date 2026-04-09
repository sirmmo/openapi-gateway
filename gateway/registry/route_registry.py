import threading
import logging
from dataclasses import dataclass, field
from typing import Optional
import fnmatch

logger = logging.getLogger(__name__)


@dataclass
class ServiceEntry:
    service_id: str
    service_name: str
    routes: list[dict]
    labels: dict
    error: bool = False
    error_reason: Optional[str] = None        # "filter_conflict" | "path_conflict"


class RouteRegistry:
    def __init__(self):
        self._store: dict[str, ServiceEntry] = {}
        self._lock = threading.RLock()

    def register(self, service_id: str, routes: list[dict], labels: dict, service_name: str):
        with self._lock:
            conflicts = self._detect_conflicts(service_id, routes)
            if conflicts:
                for c in conflicts:
                    logger.warning(
                        f"Service '{service_name}' BLOCCATO: path conflict "
                        f"'{c['method']} {c['exposed_path']}' "
                        f"già registrato da '{c['owner']}'"
                    )
                self._store[service_id] = ServiceEntry(
                    service_id=service_id,
                    service_name=service_name,
                    routes=[],
                    labels=labels,
                    error=True,
                    error_reason="path_conflict",
                )
                return

            self._store[service_id] = ServiceEntry(
                service_id=service_id,
                service_name=service_name,
                routes=routes,
                labels=labels,
                error=False,
            )

    def register_error(self, service_id: str, labels: dict, service_name: str = "",
                       reason: str = "filter_conflict"):
        with self._lock:
            self._store[service_id] = ServiceEntry(
                service_id=service_id,
                service_name=service_name or service_id,
                routes=[],
                labels=labels,
                error=True,
                error_reason=reason,
            )

    def deregister(self, service_id: str):
        with self._lock:
            self._store.pop(service_id, None)

    def resolve(self, path: str, method: str) -> Optional[tuple[dict, dict, str]]:
        """Ritorna (route, labels, service_name) o None."""
        with self._lock:
            for entry in self._store.values():
                if entry.error:
                    continue
                for route in entry.routes:
                    if route["method"] == method.upper():
                        if fnmatch.fnmatch(path, route["exposed_path"]):
                            return route, entry.labels, entry.service_name
        return None

    def all_routes(self) -> list[dict]:
        with self._lock:
            result = []
            for entry in self._store.values():
                for r in entry.routes:
                    result.append({
                        **r,
                        "service_id": entry.service_id,
                        "service_name": entry.service_name,
                        "error": entry.error,
                    })
            return result

    def status(self) -> dict:
        with self._lock:
            return {
                entry.service_name: {
                    "service_id": sid,
                    "routes": len(entry.routes),
                    "error": entry.error,
                    "error_reason": entry.error_reason,
                    "labels": {k: v for k, v in entry.labels.items() if k.startswith("gateway.")},
                }
                for sid, entry in self._store.items()
            }

    def _detect_conflicts(self, service_id: str, routes: list[dict]) -> list[dict]:
        conflicts = []
        for entry in self._store.values():
            if entry.service_id == service_id or entry.error:
                continue
            existing = {(r["method"], r["exposed_path"]) for r in entry.routes}
            for route in routes:
                key = (route["method"], route["exposed_path"])
                if key in existing:
                    conflicts.append({**route, "owner": entry.service_name})
        return conflicts


registry = RouteRegistry()
