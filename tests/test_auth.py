import pytest
from unittest.mock import patch, AsyncMock
from fastapi import HTTPException
from starlette.requests import Request

from gateway.settings import settings
from gateway.auth.middleware import (
    _requires_auth,
    _is_override_path,
    inject_claims_headers,
    check_auth,
)


def make_request(headers=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "query_string": b"",
        "headers": raw,
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def no_namespace(monkeypatch):
    monkeypatch.setattr(settings, "namespace", None)


class TestRequiresAuth:
    def test_false_when_global_auth_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "auth_required", False)
        assert _requires_auth({"path": "/users", "method": "GET"}, {}) is False

    def test_false_when_service_label_disables_auth(self, monkeypatch):
        monkeypatch.setattr(settings, "auth_required", True)
        labels = {"gateway.auth.required": "false"}
        assert _requires_auth({"path": "/users", "method": "GET"}, labels) is False

    def test_false_for_matching_override_path(self, monkeypatch):
        monkeypatch.setattr(settings, "auth_required", True)
        labels = {"gateway.auth.override.paths": "/health"}
        assert _requires_auth({"path": "/health", "method": "GET"}, labels) is False

    def test_true_by_default(self, monkeypatch):
        monkeypatch.setattr(settings, "auth_required", True)
        assert _requires_auth({"path": "/users", "method": "GET"}, {}) is True

    def test_service_label_cannot_enable_when_global_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "auth_required", False)
        labels = {"gateway.auth.required": "true"}
        # Global off takes priority
        assert _requires_auth({"path": "/users", "method": "GET"}, labels) is False


class TestIsOverridePath:
    def test_exact_path_match(self):
        overrides = [{"method": "*", "path": "/health"}]
        assert _is_override_path({"path": "/health", "method": "GET"}, overrides) is True

    def test_glob_path_match(self):
        overrides = [{"method": "*", "path": "/public/*"}]
        assert _is_override_path({"path": "/public/items", "method": "GET"}, overrides) is True

    def test_method_specific_match(self):
        overrides = [{"method": "GET", "path": "/items/*"}]
        assert _is_override_path({"path": "/items/1", "method": "GET"}, overrides) is True

    def test_method_mismatch_no_override(self):
        overrides = [{"method": "GET", "path": "/items/*"}]
        assert _is_override_path({"path": "/items/1", "method": "POST"}, overrides) is False

    def test_path_mismatch_no_override(self):
        overrides = [{"method": "*", "path": "/health"}]
        assert _is_override_path({"path": "/secret", "method": "GET"}, overrides) is False

    def test_empty_overrides_no_match(self):
        assert _is_override_path({"path": "/any", "method": "GET"}, []) is False


class TestInjectClaimsHeaders:
    def test_injects_all_user_headers(self, monkeypatch):
        monkeypatch.setattr(settings, "auth_claim_id", "sub")
        monkeypatch.setattr(settings, "auth_claim_email", "email")
        monkeypatch.setattr(settings, "auth_claim_roles", "roles")
        claims = {"sub": "user-123", "email": "user@example.com", "roles": ["admin", "user"]}
        headers = inject_claims_headers({}, claims)
        assert headers["X-User-Id"] == "user-123"
        assert headers["X-User-Email"] == "user@example.com"
        assert headers["X-User-Roles"] == "admin,user"

    def test_roles_as_plain_string(self, monkeypatch):
        monkeypatch.setattr(settings, "auth_claim_roles", "roles")
        headers = inject_claims_headers({}, {"sub": "", "email": "", "roles": "admin"})
        assert headers["X-User-Roles"] == "admin"

    def test_missing_claims_produce_empty_strings(self, monkeypatch):
        monkeypatch.setattr(settings, "auth_claim_id", "sub")
        monkeypatch.setattr(settings, "auth_claim_email", "email")
        monkeypatch.setattr(settings, "auth_claim_roles", "roles")
        headers = inject_claims_headers({}, {})
        assert headers["X-User-Id"] == ""
        assert headers["X-User-Email"] == ""
        assert headers["X-User-Roles"] == ""

    def test_custom_claim_names(self, monkeypatch):
        monkeypatch.setattr(settings, "auth_claim_id", "uid")
        monkeypatch.setattr(settings, "auth_claim_email", "mail")
        monkeypatch.setattr(settings, "auth_claim_roles", "groups")
        headers = inject_claims_headers({}, {"uid": "u1", "mail": "m@x.com", "groups": ["g1"]})
        assert headers["X-User-Id"] == "u1"
        assert headers["X-User-Email"] == "m@x.com"
        assert headers["X-User-Roles"] == "g1"


class TestCheckAuth:
    async def test_returns_none_when_auth_not_required(self, monkeypatch):
        monkeypatch.setattr(settings, "auth_required", False)
        result = await check_auth(make_request(), {"path": "/u", "method": "GET"}, {})
        assert result is None

    async def test_raises_401_when_token_missing(self, monkeypatch):
        monkeypatch.setattr(settings, "auth_required", True)
        with pytest.raises(HTTPException) as exc:
            await check_auth(make_request(), {"path": "/u", "method": "GET"}, {})
        assert exc.value.status_code == 401

    async def test_raises_401_for_non_bearer_scheme(self, monkeypatch):
        monkeypatch.setattr(settings, "auth_required", True)
        request = make_request({"Authorization": "Basic dXNlcjpwYXNz"})
        with pytest.raises(HTTPException) as exc:
            await check_auth(request, {"path": "/u", "method": "GET"}, {})
        assert exc.value.status_code == 401

    async def test_returns_claims_for_valid_token(self, monkeypatch):
        monkeypatch.setattr(settings, "auth_required", True)
        request = make_request({"Authorization": "Bearer fake.token"})
        fake_claims = {"sub": "user-1", "email": "a@b.com", "roles": []}
        with patch("gateway.auth.middleware._get_jwks", new=AsyncMock(return_value={"keys": []})):
            with patch("jose.jwt.decode", return_value=fake_claims):
                result = await check_auth(request, {"path": "/u", "method": "GET"}, {})
        assert result == fake_claims

    async def test_raises_401_for_invalid_token(self, monkeypatch):
        from jose import JWTError
        monkeypatch.setattr(settings, "auth_required", True)
        request = make_request({"Authorization": "Bearer bad.token"})
        with patch("gateway.auth.middleware._get_jwks", new=AsyncMock(return_value={"keys": []})):
            with patch("jose.jwt.decode", side_effect=JWTError("bad token")):
                with pytest.raises(HTTPException) as exc:
                    await check_auth(request, {"path": "/u", "method": "GET"}, {})
        assert exc.value.status_code == 401
