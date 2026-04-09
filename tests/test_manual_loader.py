import json
import pytest
import httpx

from gateway.settings import settings
from gateway.registry.route_registry import registry
from gateway.config.schema import ServiceConfig, FilterConfig, ExcludeConfig
from gateway.discovery.manual_loader import _service_to_labels, load_manual_config


SIMPLE_SPEC = {
    "paths": {
        "/health": {"get": {"operationId": "getHealth", "tags": [], "summary": ""}},
    }
}


@pytest.fixture(autouse=True)
def no_namespace(monkeypatch):
    monkeypatch.setattr(settings, "namespace", None)


class TestServiceToLabels:
    def test_basic_service(self):
        svc = ServiceConfig(name="my-api", port=8080, docs_path="/openapi.json")
        labels = _service_to_labels(svc)
        assert labels["gateway.enable"] == "true"
        assert labels["gateway.name"] == "my-api"
        assert labels["gateway.port"] == "8080"
        assert labels["gateway.docs"] == "/openapi.json"

    def test_with_host(self):
        svc = ServiceConfig(name="api", host="api.internal", port=9000, docs_path="/openapi.json")
        assert _service_to_labels(svc)["gateway.host"] == "api.internal"

    def test_no_host_key_when_absent(self):
        svc = ServiceConfig(name="api", port=8080, docs_path="/openapi.json")
        assert "gateway.host" not in _service_to_labels(svc)

    def test_with_prefix(self):
        svc = ServiceConfig(name="api", port=8080, docs_path="/openapi.json", prefix="/v1")
        assert _service_to_labels(svc)["gateway.prefix"] == "/v1"

    def test_auth_required_false(self):
        svc = ServiceConfig(name="api", port=8080, docs_path="/openapi.json", auth_required=False)
        assert _service_to_labels(svc)["gateway.auth.required"] == "false"

    def test_auth_required_true(self):
        svc = ServiceConfig(name="api", port=8080, docs_path="/openapi.json", auth_required=True)
        assert _service_to_labels(svc)["gateway.auth.required"] == "true"

    def test_no_auth_key_when_none(self):
        svc = ServiceConfig(name="api", port=8080, docs_path="/openapi.json", auth_required=None)
        assert "gateway.auth.required" not in _service_to_labels(svc)

    def test_auth_override_paths(self):
        svc = ServiceConfig(
            name="api", port=8080, docs_path="/openapi.json",
            auth_override_paths=["/health", "GET:/items/*"],
        )
        assert _service_to_labels(svc)["gateway.auth.override.paths"] == "/health,GET:/items/*"

    def test_filter_config(self):
        svc = ServiceConfig(
            name="api", port=8080, docs_path="/openapi.json",
            filter=FilterConfig(tags=["public"], operations=["getUser"], paths=["/api/*"]),
        )
        labels = _service_to_labels(svc)
        assert labels["gateway.filter.tags"] == "public"
        assert labels["gateway.filter.operations"] == "getUser"
        assert labels["gateway.filter.paths"] == "/api/*"

    def test_exclude_config(self):
        svc = ServiceConfig(
            name="api", port=8080, docs_path="/openapi.json",
            exclude=ExcludeConfig(paths=["/admin/*", "/internal/*"]),
        )
        assert _service_to_labels(svc)["gateway.exclude.paths"] == "/admin/*,/internal/*"

    def test_no_filter_keys_when_absent(self):
        svc = ServiceConfig(name="api", port=8080, docs_path="/openapi.json")
        labels = _service_to_labels(svc)
        assert not any(k.startswith("gateway.filter") for k in labels)
        assert not any(k.startswith("gateway.exclude") for k in labels)

    def test_uses_bare_gateway_prefix_not_namespaced(self):
        svc = ServiceConfig(name="api", port=8080, docs_path="/openapi.json")
        labels = _service_to_labels(svc)
        assert all(not k.startswith("gateway.public.") for k in labels)


class TestLoadManualConfig:
    async def test_loads_and_registers_services(self, tmp_path, monkeypatch, respx_mock):
        config = {
            "services": [
                {"name": "test-api", "host": "test-api.internal", "port": 8080, "docs_path": "/openapi.json"}
            ]
        }
        config_file = tmp_path / "services.json"
        config_file.write_text(json.dumps(config))
        monkeypatch.setattr(settings, "config_path", str(config_file))
        monkeypatch.setattr(settings, "discovery_retry_attempts", 1)
        respx_mock.get("http://test-api.internal:8080/openapi.json").mock(
            return_value=httpx.Response(200, json=SIMPLE_SPEC)
        )
        await load_manual_config()
        assert len(registry.all_routes()) == 1

    async def test_service_id_prefixed_with_manual(self, tmp_path, monkeypatch, respx_mock):
        config = {
            "services": [
                {"name": "my-svc", "host": "my-svc.internal", "port": 8080, "docs_path": "/openapi.json"}
            ]
        }
        config_file = tmp_path / "services.json"
        config_file.write_text(json.dumps(config))
        monkeypatch.setattr(settings, "config_path", str(config_file))
        monkeypatch.setattr(settings, "discovery_retry_attempts", 1)
        respx_mock.get("http://my-svc.internal:8080/openapi.json").mock(
            return_value=httpx.Response(200, json=SIMPLE_SPEC)
        )
        await load_manual_config()
        assert any(sid.startswith("manual:") for sid in registry._store)

    async def test_skips_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "config_path", str(tmp_path / "nonexistent.json"))
        await load_manual_config()
        assert registry.all_routes() == []

    async def test_skips_invalid_json(self, tmp_path, monkeypatch):
        config_file = tmp_path / "services.json"
        config_file.write_text("not valid json {{{")
        monkeypatch.setattr(settings, "config_path", str(config_file))
        await load_manual_config()
        assert registry.all_routes() == []
