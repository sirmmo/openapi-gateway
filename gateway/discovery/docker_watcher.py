import asyncio
import docker
import logging
from gateway.settings import settings
from gateway.discovery.openapi_fetcher import fetch_and_register
from gateway.registry.route_registry import registry
import gateway.labels as lbl

logger = logging.getLogger(__name__)

RELEVANT_EVENTS = {"start", "die", "stop", "update"}


async def watch_docker_events():
    loop = asyncio.get_event_loop()
    logger.info(f"Docker event watcher started (namespace: {settings.namespace or 'none'})")

    while True:
        try:
            # A single client with no read timeout.
            # All calls go through run_in_executor so the event loop is never
            # blocked, and Docker operations (including the long-lived event
            # stream) never raise ReadTimeout regardless of Docker's latency.
            client = docker.DockerClient(base_url=settings.docker_socket, timeout=None)

            def boot_scan():
                return [c for c in client.containers.list() if lbl.is_enabled(c.labels)]

            containers = await loop.run_in_executor(None, boot_scan)
            for container in containers:
                asyncio.create_task(
                    fetch_and_register(container.id, container.labels, container)
                )

            await loop.run_in_executor(None, _blocking_watch, client)
        except Exception as e:
            logger.warning(f"Docker watcher error: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)


def _blocking_watch(client):
    for event in client.events(decode=True, filters={"type": "container"}):
        action = event.get("Action", "")
        if action not in RELEVANT_EVENTS:
            continue

        container_id = event["Actor"]["ID"]
        try:
            container = client.containers.get(container_id)
            labels = container.labels
        except Exception:
            labels = event["Actor"].get("Attributes", {})
            container = None

        if not lbl.is_enabled(labels):
            continue

        if action in {"start", "update"}:
            asyncio.run(fetch_and_register(container_id, labels, container))
        elif action in {"die", "stop"}:
            registry.deregister(container_id)
            logger.info(f"Deregistered service: {container_id[:12]}")
