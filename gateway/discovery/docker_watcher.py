import asyncio
import socket
import docker
import logging
from gateway.settings import settings
from gateway.discovery.openapi_fetcher import fetch_and_register
from gateway.registry.route_registry import registry
import gateway.labels as lbl

logger = logging.getLogger(__name__)

RELEVANT_CONTAINER_EVENTS = {"start", "die", "stop", "update"}
RELEVANT_SERVICE_EVENTS = {"create", "update", "remove"}


class _ServiceAdapter:
    """Wraps a Docker Swarm service to expose the same .name interface as a container."""
    def __init__(self, service):
        self.name = service.name
        self.id = service.id
        self.labels = service.attrs.get("Spec", {}).get("Labels", {})


# ── Network filtering ──────────────────────────────────────────────────────────

def _gateway_networks(client) -> set[str]:
    """
    Return the Docker network names to use for filtering.

    Resolution order:
      1. GATEWAY_DOCKER_NETWORKS env var (comma-separated) — explicit, no heuristics.
      2. Auto-detect: inspect the gateway's own container via socket.gethostname(),
         which Docker sets to the container ID.
      3. Empty set — filter disabled (local dev or detection failed).
    """
    if settings.docker_networks:
        nets = {n.strip() for n in settings.docker_networks.split(",") if n.strip()}
        logger.debug(f"Gateway networks (configured): {', '.join(sorted(nets))}")
        return nets

    try:
        container = client.containers.get(socket.gethostname())
        nets = set(container.attrs["NetworkSettings"]["Networks"].keys())
        logger.debug(f"Gateway networks (auto-detected): {', '.join(sorted(nets))}")
        return nets
    except Exception:
        logger.debug("Could not determine gateway networks — network filter disabled")
        return set()


def _container_networks(container) -> set[str]:
    return set(container.attrs.get("NetworkSettings", {}).get("Networks", {}).keys())


def _service_networks(service) -> set[str]:
    # Swarm services list attached networks under Spec.Networks[].Target
    return {
        n["Target"]
        for n in service.attrs.get("Spec", {}).get("Networks", [])
        if n.get("Target")
    }


def _on_gateway_network(networks: set[str], gateway_nets: set[str]) -> bool:
    """True when there is at least one shared network, or filtering is disabled."""
    return not gateway_nets or bool(networks & gateway_nets)


# ── Scanning ───────────────────────────────────────────────────────────────────

def _collect_enabled(client) -> list[tuple]:
    """
    Blocking: returns (service_id, labels, obj) for every gateway-enabled
    container and Swarm service that shares a network with this gateway.
    """
    gateway_nets = _gateway_networks(client)
    found = []

    for c in client.containers.list():
        if not lbl.is_enabled(c.labels):
            continue
        if not _on_gateway_network(_container_networks(c), gateway_nets):
            logger.debug(f"Scan — skipping container {c.name}: not on gateway network")
            continue
        found.append((c.id, c.labels, c))
        logger.debug(f"Scan — container: {c.name} ({c.id[:12]})")

    try:
        for s in client.services.list():
            adapter = _ServiceAdapter(s)
            if not lbl.is_enabled(adapter.labels):
                continue
            if not _on_gateway_network(_service_networks(s), gateway_nets):
                logger.debug(f"Scan — skipping service {s.name}: not on gateway network")
                continue
            found.append((f"service:{s.id}", adapter.labels, adapter))
            logger.debug(f"Scan — swarm service: {s.name} ({s.id[:12]})")
    except docker.errors.APIError:
        pass  # not in Swarm mode

    return found


async def rediscover():
    """
    Re-scan all running containers and Swarm services and re-register any
    that carry gateway labels and share a network with this gateway.
    Called on boot and from the admin API.
    """
    loop = asyncio.get_event_loop()
    client = docker.DockerClient(base_url=settings.docker_socket, timeout=None)
    entries = await loop.run_in_executor(None, _collect_enabled, client)
    logger.info(f"Docker scan complete: {len(entries)} gateway-enabled object(s) found")
    tasks = [fetch_and_register(sid, labels, obj) for sid, labels, obj in entries]
    await asyncio.gather(*tasks)
    return len(entries)


# ── Event watcher ──────────────────────────────────────────────────────────────

async def watch_docker_events():
    loop = asyncio.get_event_loop()
    logger.info(f"Docker event watcher started (namespace: {settings.namespace or 'none'})")

    while True:
        try:
            client = docker.DockerClient(base_url=settings.docker_socket, timeout=None)

            entries = await loop.run_in_executor(None, _collect_enabled, client)
            logger.info(f"Boot scan complete: {len(entries)} gateway-enabled object(s) found")
            for service_id, labels, obj in entries:
                asyncio.create_task(fetch_and_register(service_id, labels, obj))

            await loop.run_in_executor(None, _blocking_watch, client, loop)
        except Exception as e:
            logger.warning(f"Docker watcher error: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)


def _blocking_watch(client, loop):
    # Resolve gateway networks once per watch session; reconnect on error recomputes.
    gateway_nets = _gateway_networks(client)

    for event in client.events(decode=True, filters={"type": ["container", "service"]}):
        event_type = event.get("Type", "")
        action = event.get("Action", "")

        if event_type == "container":
            _handle_container_event(event, action, client, loop, gateway_nets)
        elif event_type == "service":
            _handle_service_event(event, action, client, loop, gateway_nets)


def _handle_container_event(event, action, client, loop, gateway_nets):
    if action not in RELEVANT_CONTAINER_EVENTS:
        return

    container_id = event["Actor"]["ID"]
    attrs = event["Actor"].get("Attributes", {})

    try:
        container = client.containers.get(container_id)
        labels = container.labels
        name = container.name
        nets = _container_networks(container)
    except Exception:
        labels = attrs
        container = None
        name = attrs.get("name", container_id[:12])
        nets = set()

    if not lbl.is_enabled(labels):
        return

    if not _on_gateway_network(nets, gateway_nets):
        logger.debug(f"Container event ignored: {name} not on gateway network")
        return

    if action in {"start", "update"}:
        logger.info(f"Container {action}: {name} ({container_id[:12]}) — scheduling discovery")
        asyncio.run_coroutine_threadsafe(
            fetch_and_register(container_id, labels, container), loop
        )
    elif action in {"die", "stop"}:
        registry.deregister(container_id)
        logger.info(f"Container {action}: {name} ({container_id[:12]}) — deregistered")


def _handle_service_event(event, action, client, loop, gateway_nets):
    if action not in RELEVANT_SERVICE_EVENTS:
        return

    service_id = event["Actor"]["ID"]
    service_key = f"service:{service_id}"
    attrs = event["Actor"].get("Attributes", {})
    name = attrs.get("name", service_id[:12])

    if action == "remove":
        registry.deregister(service_key)
        logger.info(f"Swarm service removed: {name} ({service_id[:12]}) — deregistered")
        return

    try:
        service = client.services.get(service_id)
        adapter = _ServiceAdapter(service)
        labels = adapter.labels
        nets = _service_networks(service)
    except Exception:
        labels = attrs
        adapter = None
        nets = set()
        logger.warning(f"Swarm service {action}: {name} ({service_id[:12]}) — could not inspect, using event attributes")

    if not lbl.is_enabled(labels):
        return

    if not _on_gateway_network(nets, gateway_nets):
        logger.debug(f"Service event ignored: {name} not on gateway network")
        return

    logger.info(f"Swarm service {action}: {name} ({service_id[:12]}) — scheduling discovery")
    asyncio.run_coroutine_threadsafe(
        fetch_and_register(service_key, labels, adapter), loop
    )
