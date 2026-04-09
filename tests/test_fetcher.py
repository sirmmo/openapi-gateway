import pytest
import httpx
from unittest.mock import MagicMock, patch, AsyncMock

from gateway.settings import settings
from gateway.registry.route_registry import registry
from gateway.registry.filter import FilterSpec, FilterMode
from gateway.discovery.openapi_fetcher import (
    _resolve_service_name,
    _resolve_base_url,
    _resolve_prefix,
    _extract_routes,
    fetch_and_register,
)


SIMPLE_SPEC = {
    "paths": {
        "/health": {
            "get": {"operationId": "getHealth", "tags": ["public"], "summary": "Health"},
        },
        "/users": {
            "get": {"operationId": "listUsers", "tags": ["public"], "summary": "List"},
            "post": {"operationId": "createUser", "tags": ["public"], "summary": "Create"},
        },
    }
}


@pytest.fixture(autouse=True)
def no_namespace(monkeypatch):
    monkeypatch.setattr(settings, "namespace", None)


class TestResolveServiceName:
    def test_uses_name_label(self):
        assert _resolve_service_name({"gateway.name": "my-svc"}, None) == "my-svc"

    def test_falls_back_to_compose_label(self):
        labels = {"com.docker.compose.service": "api"}
        assert _resolve_service_name(labels, None) == "api"

    def test_falls_back_to_container_name(self):
        container = MagicMock()
        container.name = "/my-container"
        assert _resolve_service_name({}, container) == "my-container"

    def test_returns_unknown_without_container(self):
        assert _resolve_service_name({}, None) == "unknown"

    def test_name_label_takes_priority_over_compose(self):
        labels = {"gateway.name": "override", "com.docker.compose.service": "compose-name"}
        assert _resolve_service_name(labels, None) == "override"


class TestResolveBaseUrl:
    def test_uses_explicit_host(self):
        labels = {"gateway.host": "api.internal", "gateway.port": "9000"}
        assert _resolve_base_url(labels, None) == "http://api.internal:9000"

    def test_uses_service_name_as_host(self):
        labels = {"gateway.name": "my-api", "gateway.port": "8080"}
        assert _resolve_base_url(labels, None) == "http://my-api:8080"

    def test_default_port_8000(self):
        labels = {"gateway.name": "my-api"}
        assert _resolve_base_url(labels, None) == "http://my-api:8000"


class TestResolvePrefix:
    def test_no_prefix_returns_none(self):
        assert _resolve_prefix({}, "svc") is None

    def test_prefix_true_uses_service_name(self):
        labels = {"gateway.prefix": "true"}
        assert _resolve_prefix(labels, "my-api") == "/my-api"

    def test_explicit_path_prefix(self):
        labels = {"gateway.prefix": "/api/v1"}
        assert _resolve_prefix(labels, "svc") == "/api/v1"

    def test_adds_leading_slash(self):
        labels = {"gateway.prefix": "api/v1"}
        assert _resolve_prefix(labels, "svc") == "/api/v1"

    def test_empty_string_returns_none(self):
        labels = {"gateway.prefix": ""}
        assert _resolve_prefix(labels, "svc") is None


class TestExtractRoutes:
    def test_extracts_all_routes(self):
        spec = FilterSpec(mode=FilterMode.ALLOW_ALL)
        routes = _extract_routes(SIMPLE_SPEC, "http://svc:8080", None, spec)
        assert len(routes) == 3

    def test_attaches_base_url(self):
        spec = FilterSpec(mode=FilterMode.ALLOW_ALL)
        routes = _extract_routes(SIMPLE_SPEC, "http://svc:8080", None, spec)
        assert all(r["base_url"] == "http://svc:8080" for r in routes)

    def test_applies_prefix_to_exposed_path(self):
        spec = FilterSpec(mode=FilterMode.ALLOW_ALL)
        routes = _extract_routes(SIMPLE_SPEC, "http://svc:8080", "/api", spec)
        assert all(r["exposed_path"].startswith("/api") for r in routes)
        assert all(r["prefix"] == "/api" for r in routes)

    def test_no_prefix_exposed_path_equals_path(self):
        spec = FilterSpec(mode=FilterMode.ALLOW_ALL)
        routes = _extract_routes(
            {"paths": {"/users": {"get": {"operationId": "lu", "tags": [], "summary": ""}}}},
            "http://svc:8080", None, spec,
        )
        assert routes[0]["exposed_path"] == "/users"
        assert routes[0]["prefix"] is None

    def test_skips_non_http_methods(self):
        spec_with_trace = {
            "paths": {
                "/test": {
                    "get": {"operationId": "t", "tags": [], "summary": ""},
                    "trace": {"operationId": "tr", "tags": [], "summary": ""},
                }
            }
        }
        spec = FilterSpec(mode=FilterMode.ALLOW_ALL)
        routes = _extract_routes(spec_with_trace, "http://svc:8080", None, spec)
        assert {r["method"] for r in routes} == {"GET"}

    def test_applies_allowlist_filter(self):
        mixed = {
            "paths": {
                "/pub": {"get": {"operationId": "p", "tags": ["public"], "summary": ""}},
                "/int": {"get": {"operationId": "i", "tags": ["internal"], "summary": ""}},
            }
        }
        spec = FilterSpec(mode=FilterMode.ALLOWLIST, tags=["public"])
        routes = _extract_routes(mixed, "http://svc:8080", None, spec)
        assert len(routes) == 1
        assert routes[0]["path"] == "/pub"

    def test_applies_denylist_filter(self):
        mixed = {
            "paths": {
                "/pub": {"get": {"operationId": "p", "tags": ["public"], "summary": ""}},
                "/int": {"get": {"operationId": "i", "tags": ["internal"], "summary": ""}},
            }
        }
        spec = FilterSpec(mode=FilterMode.DENYLIST, tags=["internal"])
        routes = _extract_routes(mixed, "http://svc:8080", None, spec)
        assert len(routes) == 1
        assert routes[0]["path"] == "/pub"


class TestFetchAndRegister:
    async def test_success_registers_routes(self, monkeypatch, respx_mock):
        monkeypatch.setattr(settings, "discovery_retry_attempts", 1)
        labels = {"gateway.enable": "true", "gateway.name": "test-svc", "gateway.port": "8080"}
        respx_mock.get("http://test-svc:8080/openapi.json").mock(
            return_value=httpx.Response(200, json=SIMPLE_SPEC)
        )
        await fetch_and_register("svc-1", labels)
        assert len(registry.all_routes()) == 3

    async def test_filter_conflict_registers_error(self, monkeypatch):
        labels = {
            "gateway.enable": "true",
            "gateway.name": "bad-svc",
            "gateway.port": "8080",
            "gateway.filter.tags": "public",
            "gateway.exclude.paths": "/admin/*",
        }
        await fetch_and_register("svc-bad", labels)
        s = registry.status()
        assert s["bad-svc"]["error"] is True
        assert s["bad-svc"]["error_reason"] == "filter_conflict"

    async def test_retries_and_succeeds(self, monkeypatch, respx_mock):
        monkeypatch.setattr(settings, "discovery_retry_attempts", 3)
        monkeypatch.setattr(settings, "discovery_retry_backoff", 0.0)
        labels = {"gateway.enable": "true", "gateway.name": "flaky-svc", "gateway.port": "8080"}
        respx_mock.get("http://flaky-svc:8080/openapi.json").mock(
            side_effect=[
                httpx.ConnectError("timeout"),
                httpx.ConnectError("timeout"),
                httpx.Response(200, json=SIMPLE_SPEC),
            ]
        )
        with patch("gateway.discovery.openapi_fetcher.asyncio.sleep", new=AsyncMock()):
            await fetch_and_register("svc-1", labels)
        assert len(registry.all_routes()) == 3

    async def test_all_retries_exhausted_leaves_unregistered(self, monkeypatch, respx_mock):
        monkeypatch.setattr(settings, "discovery_retry_attempts", 2)
        monkeypatch.setattr(settings, "discovery_retry_backoff", 0.0)
        labels = {"gateway.enable": "true", "gateway.name": "dead-svc", "gateway.port": "8080"}
        respx_mock.get("http://dead-svc:8080/openapi.json").mock(
            side_effect=httpx.ConnectError("refused")
        )
        with patch("gateway.discovery.openapi_fetcher.asyncio.sleep", new=AsyncMock()):
            await fetch_and_register("svc-1", labels)
        assert registry.all_routes() == []
