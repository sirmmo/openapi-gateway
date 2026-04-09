import pytest
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from gateway.registry.route_registry import registry
from gateway.proxy.forwarder import forward_request
from gateway.api.admin import router as admin_router


@pytest.fixture(autouse=True)
def _clear_registry():
    registry._store.clear()
    yield
    registry._store.clear()


@pytest.fixture
def app():
    _app = FastAPI()
    _app.include_router(admin_router)

    @_app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def proxy(request: Request, path: str):
        return await forward_request(request, f"/{path}")

    return _app


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)
