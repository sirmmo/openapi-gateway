from fastapi import FastAPI, Request
from contextlib import asynccontextmanager
import asyncio

from gateway.discovery.docker_watcher import watch_docker_events
from gateway.discovery.manual_loader import load_manual_config
from gateway.proxy.forwarder import forward_request
from gateway.api.admin import router as admin_router
from gateway.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    await load_manual_config()
    asyncio.create_task(watch_docker_events())
    yield


app = FastAPI(
    title=f"OpenAPI Gateway{' [' + settings.namespace + ']' if settings.namespace else ''}",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.include_router(admin_router)


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
)
async def proxy(request: Request, path: str):
    return await forward_request(request, f"/{path}")
