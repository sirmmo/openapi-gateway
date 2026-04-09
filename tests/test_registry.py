import pytest
from gateway.registry.route_registry import RouteRegistry


@pytest.fixture
def reg():
    return RouteRegistry()


def make_route(path="/users", method="GET", base_url="http://svc:8080", prefix=None):
    exposed = f"{prefix}{path}" if prefix else path
    return {
        "path": path,
        "exposed_path": exposed,
        "method": method,
        "operationId": "",
        "tags": [],
        "base_url": base_url,
        "prefix": prefix,
        "summary": "",
    }


class TestRegisterAndResolve:
    def test_resolves_registered_route(self, reg):
        reg.register("svc-1", [make_route("/health", "GET")], {}, "my-svc")
        result = reg.resolve("/health", "GET")
        assert result is not None
        route, _, service_name = result
        assert route["path"] == "/health"
        assert service_name == "my-svc"

    def test_returns_none_for_unknown_path(self, reg):
        reg.register("svc-1", [make_route("/health", "GET")], {}, "my-svc")
        assert reg.resolve("/unknown", "GET") is None

    def test_returns_none_for_wrong_method(self, reg):
        reg.register("svc-1", [make_route("/users", "GET")], {}, "svc")
        assert reg.resolve("/users", "POST") is None

    def test_resolves_with_prefix(self, reg):
        reg.register("svc-1", [make_route("/users", "GET", prefix="/api")], {}, "svc")
        assert reg.resolve("/api/users", "GET") is not None

    def test_resolves_wildcard_path_param(self, reg):
        # exposed_path uses * (converted from {id} by _path_to_glob in fetcher)
        route = make_route("/users/*", "GET")
        route["path"] = "/users/{id}"
        reg.register("svc-1", [route], {}, "svc")
        assert reg.resolve("/users/123", "GET") is not None
        assert reg.resolve("/users/abc", "GET") is not None

    def test_returns_labels_with_route(self, reg):
        labels = {"gateway.enable": "true"}
        reg.register("svc-1", [make_route("/health")], labels, "svc")
        _, returned_labels, _ = reg.resolve("/health", "GET")
        assert returned_labels == labels

    def test_skips_errored_services(self, reg):
        reg.register_error("svc-1", {}, "bad-svc", reason="filter_conflict")
        assert reg.resolve("/anything", "GET") is None


class TestDeregister:
    def test_removes_service(self, reg):
        reg.register("svc-1", [make_route("/health")], {}, "svc")
        reg.deregister("svc-1")
        assert reg.resolve("/health", "GET") is None

    def test_nonexistent_id_is_noop(self, reg):
        reg.deregister("does-not-exist")  # must not raise


class TestConflictDetection:
    def test_second_service_blocked_on_conflict(self, reg):
        route = make_route("/users", "GET")
        reg.register("svc-1", [route], {}, "first")
        reg.register("svc-2", [route], {}, "second")
        status = reg.status()
        assert status["second"]["error"] is True
        assert status["second"]["error_reason"] == "path_conflict"

    def test_existing_service_unaffected_by_conflict(self, reg):
        route = make_route("/users", "GET")
        reg.register("svc-1", [route], {}, "first")
        reg.register("svc-2", [route], {}, "second")
        result = reg.resolve("/users", "GET")
        assert result is not None
        assert result[2] == "first"

    def test_different_methods_no_conflict(self, reg):
        reg.register("svc-1", [make_route("/users", "GET")], {}, "svc-1")
        reg.register("svc-2", [make_route("/users", "POST")], {}, "svc-2")
        assert reg.status()["svc-1"]["error"] is False
        assert reg.status()["svc-2"]["error"] is False

    def test_reregistration_with_clean_routes_clears_conflict(self, reg):
        route_a = make_route("/users", "GET")
        route_b = make_route("/orders", "GET")
        reg.register("svc-1", [route_a], {}, "svc-1")
        reg.register("svc-2", [route_a], {}, "svc-2")  # conflict
        reg.register("svc-2", [route_b], {}, "svc-2")  # now clean
        assert reg.status()["svc-2"]["error"] is False


class TestStatusAndAllRoutes:
    def test_status_includes_registered_service(self, reg):
        reg.register("svc-1", [make_route("/health")], {"gateway.enable": "true"}, "my-svc")
        s = reg.status()
        assert "my-svc" in s
        assert s["my-svc"]["routes"] == 1
        assert s["my-svc"]["error"] is False

    def test_status_only_exposes_gateway_labels(self, reg):
        labels = {"gateway.enable": "true", "com.docker.compose.project": "proj"}
        reg.register("svc-1", [], labels, "svc")
        s = reg.status()
        assert "gateway.enable" in s["svc"]["labels"]
        assert "com.docker.compose.project" not in s["svc"]["labels"]

    def test_all_routes_returns_all_registered(self, reg):
        reg.register("svc-1", [make_route("/a", "GET"), make_route("/b", "POST")], {}, "svc")
        routes = reg.all_routes()
        assert len(routes) == 2
        assert {r["path"] for r in routes} == {"/a", "/b"}

    def test_all_routes_excludes_errored_service_routes(self, reg):
        reg.register_error("svc-1", {}, "bad-svc")
        assert reg.all_routes() == []
