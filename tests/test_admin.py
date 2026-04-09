import pytest
from unittest.mock import patch, AsyncMock

from gateway.settings import settings
from gateway.registry.route_registry import registry


@pytest.fixture
def admin_client(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_secret", "test-secret")
    return client


def auth_header(secret="test-secret"):
    return {"X-Gateway-Admin-Secret": secret}


class TestAdminSecurity:
    def test_503_when_admin_secret_not_configured(self, client, monkeypatch):
        monkeypatch.setattr(settings, "admin_secret", None)
        assert client.get("/_gateway/status").status_code == 503

    def test_403_with_wrong_secret(self, admin_client):
        assert admin_client.get("/_gateway/status", headers=auth_header("wrong")).status_code == 403

    def test_403_with_no_secret_header(self, admin_client):
        assert admin_client.get("/_gateway/status").status_code == 403

    def test_200_with_correct_secret(self, admin_client):
        assert admin_client.get("/_gateway/status", headers=auth_header()).status_code == 200


class TestStatusEndpoint:
    def test_includes_namespace(self, admin_client, monkeypatch):
        monkeypatch.setattr(settings, "namespace", "public")
        body = admin_client.get("/_gateway/status", headers=auth_header()).json()
        assert body["namespace"] == "public"

    def test_includes_services_key(self, admin_client):
        body = admin_client.get("/_gateway/status", headers=auth_header()).json()
        assert "services" in body

    def test_reflects_registered_service(self, admin_client):
        registry.register("svc-1", [], {"gateway.enable": "true"}, "my-svc")
        body = admin_client.get("/_gateway/status", headers=auth_header()).json()
        assert "my-svc" in body["services"]

    def test_none_namespace_when_unset(self, admin_client, monkeypatch):
        monkeypatch.setattr(settings, "namespace", None)
        body = admin_client.get("/_gateway/status", headers=auth_header()).json()
        assert body["namespace"] is None


class TestRoutesEndpoint:
    def test_returns_list(self, admin_client):
        result = admin_client.get("/_gateway/routes", headers=auth_header())
        assert result.status_code == 200
        assert isinstance(result.json(), list)

    def test_returns_empty_when_no_services(self, admin_client):
        assert admin_client.get("/_gateway/routes", headers=auth_header()).json() == []

    def test_reflects_registered_routes(self, admin_client):
        route = {
            "path": "/health", "exposed_path": "/health", "method": "GET",
            "operationId": "", "tags": [], "base_url": "http://svc:8080",
            "prefix": None, "summary": "",
        }
        registry.register("svc-1", [route], {}, "my-svc")
        routes = admin_client.get("/_gateway/routes", headers=auth_header()).json()
        assert len(routes) == 1
        assert routes[0]["path"] == "/health"


class TestReloadEndpoint:
    def test_returns_ok(self, admin_client):
        with patch("gateway.api.admin.reload_manual_config", new=AsyncMock()):
            r = admin_client.post("/_gateway/reload", headers=auth_header())
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_calls_reload_manual_config(self, admin_client):
        mock_reload = AsyncMock()
        with patch("gateway.api.admin.reload_manual_config", new=mock_reload):
            admin_client.post("/_gateway/reload", headers=auth_header())
        mock_reload.assert_called_once()
