from fastapi import FastAPI, Request
from contextlib import asynccontextmanager
import asyncio
import logging

from gateway.discovery.docker_watcher import watch_docker_events
from gateway.discovery.manual_loader import load_manual_config
from gateway.proxy.forwarder import forward_request
from gateway.api.admin import router as admin_router
from gateway.api.docs import router as docs_router
from gateway.auth.api_keys import load_api_keys
from gateway.settings import settings

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


def _log_settings():
    def mask(value):
        return "***" if value else "not set"

    logger.info("=" * 60)
    logger.info("OpenAPI Gateway starting")
    logger.info("=" * 60)
    logger.info(f"  namespace          : {settings.namespace or '(legacy mode)'}")
    logger.info(f"  log_level          : {settings.log_level}")
    logger.info(f"  docs               : {settings.docs_default}")
    logger.info(f"  config_path        : {settings.config_path}")
    logger.info(f"  docker_socket      : {settings.docker_socket}")
    logger.info(f"  docker_networks    : {settings.docker_networks or '(auto-detect)'}")
    logger.info(f"  auth_required      : {settings.auth_required}")
    logger.info(f"  auth_mode          : {settings.auth_mode}")
    logger.info(f"  auth_jwks_url      : {settings.auth_jwks_url or 'not set'}")
    logger.info(f"  auth_jwks_ttl      : {settings.auth_jwks_ttl_seconds}s")
    logger.info(f"  auth_claims        : id={settings.auth_claim_id}  email={settings.auth_claim_email}  roles={settings.auth_claim_roles}")
    logger.info(f"  admin_secret       : {mask(settings.admin_secret)}")
    logger.info(f"  api_key_required   : {settings.api_key_required}")
    logger.info(f"  api_key_header     : {settings.api_key_header}")
    logger.info(f"  api_keys_path      : {settings.api_keys_path}")
    logger.info(f"  retry_attempts     : {settings.discovery_retry_attempts}")
    logger.info(f"  retry_backoff      : {settings.discovery_retry_backoff}s")
    logger.info("=" * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _log_settings()
    load_api_keys()
    await load_manual_config()
    asyncio.create_task(watch_docker_events())
    yield


app = FastAPI(
    title=f"OpenAPI Gateway{' [' + settings.namespace + ']' if settings.namespace else ''}",
    openapi_url=None,   # we serve our own merged spec via docs_router
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.include_router(admin_router)
app.include_router(docs_router)   # must be before the catch-all proxy route


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
)
async def proxy(request: Request, path: str):
    return await forward_request(request, f"/{path}")
