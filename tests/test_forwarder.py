import pytest
import httpx
from unittest.mock import patch, AsyncMock

from gateway.settings import settings
from gateway.registry.route_registry import registry


def register_route(path="/users", method="GET", base_url="http://upstream:8080", prefix=None):
    exposed = f"{prefix}{path}" if prefix else path
    route = {
        "path": path,
        "exposed_path": exposed,
        "method": method,
        "operationId": "",
        "tags": [],
        "base_url": base_url,
        "prefix": prefix,
        "summary": "",
    }
    registry.register("svc-1", [route], {"gateway.enable": "true"}, "upstream-svc")
    return route


@pytest.fixture(autouse=True)
def base_settings(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", False)
    monkeypatch.setattr(settings, "namespace", None)
    monkeypatch.setattr(settings, "auth_mode", "validate")


class TestNotFound:
    def test_404_when_no_route_matches(self, client):
        assert client.get("/unknown").status_code == 404

    def test_404_when_registry_empty(self, client):
        assert client.get("/users").status_code == 404


class TestProxying:
    def test_proxies_get_request(self, client, respx_mock):
        register_route("/users", "GET")
        respx_mock.get("http://upstream:8080/users").mock(
            return_value=httpx.Response(200, json={"users": []})
        )
        r = client.get("/users")
        assert r.status_code == 200
        assert r.json() == {"users": []}

    def test_passes_query_params_to_upstream(self, client, respx_mock):
        register_route("/search", "GET")
        route_mock = respx_mock.get("http://upstream:8080/search").mock(
            return_value=httpx.Response(200, json={})
        )
        client.get("/search?q=hello&page=2")
        url = str(route_mock.calls.last.request.url)
        assert "q=hello" in url
        assert "page=2" in url

    def test_passes_post_body_to_upstream(self, client, respx_mock):
        register_route("/users", "POST")
        route_mock = respx_mock.post("http://upstream:8080/users").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        client.post("/users", json={"name": "Alice"})
        assert route_mock.called

    def test_upstream_status_code_preserved(self, client, respx_mock):
        register_route("/missing", "GET")
        respx_mock.get("http://upstream:8080/missing").mock(
            return_value=httpx.Response(404, text="not found")
        )
        assert client.get("/missing").status_code == 404

    def test_upstream_response_body_preserved(self, client, respx_mock):
        register_route("/data", "GET")
        respx_mock.get("http://upstream:8080/data").mock(
            return_value=httpx.Response(200, json={"key": "value"})
        )
        assert client.get("/data").json() == {"key": "value"}


class TestPrefixHandling:
    def test_strips_prefix_before_forwarding(self, client, respx_mock):
        register_route("/users", "GET", prefix="/api")
        route_mock = respx_mock.get("http://upstream:8080/users").mock(
            return_value=httpx.Response(200, json={})
        )
        r = client.get("/api/users")
        assert r.status_code == 200
        assert route_mock.called

    def test_injects_original_path_header(self, client, respx_mock):
        register_route("/users", "GET", prefix="/api")
        route_mock = respx_mock.get("http://upstream:8080/users").mock(
            return_value=httpx.Response(200, json={})
        )
        client.get("/api/users")
        req = route_mock.calls.last.request
        assert req.headers.get("x-gateway-original-path") == "/api/users"

    def test_injects_service_name_header(self, client, respx_mock):
        register_route("/users", "GET", prefix="/api")
        route_mock = respx_mock.get("http://upstream:8080/users").mock(
            return_value=httpx.Response(200, json={})
        )
        client.get("/api/users")
        req = route_mock.calls.last.request
        assert req.headers.get("x-gateway-service") == "upstream-svc"

    def test_no_prefix_headers_without_prefix(self, client, respx_mock):
        register_route("/users", "GET")
        route_mock = respx_mock.get("http://upstream:8080/users").mock(
            return_value=httpx.Response(200, json={})
        )
        client.get("/users")
        req = route_mock.calls.last.request
        assert "x-gateway-original-path" not in req.headers
        assert "x-gateway-service" not in req.headers


class TestAuthIntegration:
    def test_401_when_auth_required_and_no_token(self, client, monkeypatch):
        monkeypatch.setattr(settings, "auth_required", True)
        register_route("/secure", "GET")
        assert client.get("/secure").status_code == 401

    def test_no_auth_check_when_globally_disabled(self, client, respx_mock, monkeypatch):
        monkeypatch.setattr(settings, "auth_required", False)
        register_route("/open", "GET")
        respx_mock.get("http://upstream:8080/open").mock(
            return_value=httpx.Response(200, json={})
        )
        assert client.get("/open").status_code == 200

    def test_validate_mode_strips_authorization_header(self, client, respx_mock, monkeypatch):
        monkeypatch.setattr(settings, "auth_required", True)
        monkeypatch.setattr(settings, "auth_mode", "validate")
        monkeypatch.setattr(settings, "auth_claim_id", "sub")
        monkeypatch.setattr(settings, "auth_claim_email", "email")
        monkeypatch.setattr(settings, "auth_claim_roles", "roles")
        register_route("/secure", "GET")
        route_mock = respx_mock.get("http://upstream:8080/secure").mock(
            return_value=httpx.Response(200, json={})
        )
        fake_claims = {"sub": "u1", "email": "u@example.com", "roles": ["user"]}
        with patch("gateway.auth.middleware._get_jwks", new=AsyncMock(return_value={})):
            with patch("jose.jwt.decode", return_value=fake_claims):
                client.get("/secure", headers={"Authorization": "Bearer tok"})
        req = route_mock.calls.last.request
        assert "authorization" not in req.headers
        assert req.headers.get("x-user-id") == "u1"
        assert req.headers.get("x-user-email") == "u@example.com"
        assert req.headers.get("x-user-roles") == "user"

    def test_relay_mode_preserves_authorization_header(self, client, respx_mock, monkeypatch):
        monkeypatch.setattr(settings, "auth_required", True)
        monkeypatch.setattr(settings, "auth_mode", "relay")
        register_route("/secure", "GET")
        route_mock = respx_mock.get("http://upstream:8080/secure").mock(
            return_value=httpx.Response(200, json={})
        )
        fake_claims = {"sub": "u1", "email": "u@example.com", "roles": []}
        with patch("gateway.auth.middleware._get_jwks", new=AsyncMock(return_value={})):
            with patch("jose.jwt.decode", return_value=fake_claims):
                client.get("/secure", headers={"Authorization": "Bearer my-token"})
        req = route_mock.calls.last.request
        assert req.headers.get("authorization") == "Bearer my-token"
