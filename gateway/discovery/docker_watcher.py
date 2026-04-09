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

    # Two separate clients:
    # - api_client: default timeout, used for one-off calls (containers.list/get)
    # - stream_client: no read timeout, used only for the long-lived event stream
    api_client = docker.DockerClient(base_url=settings.docker_socket)
    stream_client = docker.DockerClient(base_url=settings.docker_socket, timeout=None)

    logger.info(f"Docker event watcher started (namespace: {settings.namespace or 'none'})")

    # Boot scan runs in an executor so it never blocks the event loop
    def boot_scan():
        return [c for c in api_client.containers.list() if lbl.is_enabled(c.labels)]

    containers = await loop.run_in_executor(None, boot_scan)
    for container in containers:
        asyncio.create_task(
            fetch_and_register(container.id, container.labels, container)
        )

    while True:
        try:
            await loop.run_in_executor(None, _blocking_watch, stream_client, api_client)
        except Exception as e:
            logger.warning(f"Docker event stream interrupted: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)


def _blocking_watch(stream_client, api_client):
    for event in stream_client.events(decode=True, filters={"type": "container"}):
        action = event.get("Action", "")
        if action not in RELEVANT_EVENTS:
            continue

        container_id = event["Actor"]["ID"]
        try:
            container = api_client.containers.get(container_id)
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
