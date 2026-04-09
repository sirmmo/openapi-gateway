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
    client = docker.DockerClient(base_url=settings.docker_socket)
    logger.info(f"Docker event watcher started (namespace: {settings.namespace or 'none'})")

    # Boot scan: registra container già running
    for container in client.containers.list():
        if lbl.is_enabled(container.labels):
            asyncio.create_task(
                fetch_and_register(container.id, container.labels, container)
            )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _blocking_watch, client)


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
