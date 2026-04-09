import pytest
from gateway.registry.filter import parse_labels, apply_filter, FilterMode, FilterSpec
from gateway.settings import settings


@pytest.fixture(autouse=True)
def no_namespace(monkeypatch):
    monkeypatch.setattr(settings, "namespace", None)


class TestParseLabels:
    def test_allow_all_when_no_filter_labels(self):
        assert parse_labels({}).mode == FilterMode.ALLOW_ALL

    def test_allowlist_from_filter_labels(self):
        spec = parse_labels({"gateway.filter.tags": "public"})
        assert spec.mode == FilterMode.ALLOWLIST
        assert spec.tags == ["public"]

    def test_allowlist_multiple_filter_criteria(self):
        spec = parse_labels({
            "gateway.filter.tags": "public,v2",
            "gateway.filter.operations": "getUser",
            "gateway.filter.paths": "/api/*",
        })
        assert spec.mode == FilterMode.ALLOWLIST
        assert spec.tags == ["public", "v2"]
        assert spec.operations == ["getUser"]
        assert spec.paths == ["/api/*"]

    def test_denylist_from_exclude_labels(self):
        spec = parse_labels({"gateway.exclude.paths": "/admin/*"})
        assert spec.mode == FilterMode.DENYLIST
        assert spec.paths == ["/admin/*"]

    def test_error_when_both_filter_and_exclude_set(self):
        spec = parse_labels({
            "gateway.filter.tags": "public",
            "gateway.exclude.paths": "/admin/*",
        })
        assert spec.mode == FilterMode.ERROR


class TestApplyFilter:
    def test_allow_all_passes_everything(self):
        spec = FilterSpec(mode=FilterMode.ALLOW_ALL)
        assert apply_filter(spec, {"path": "/any", "tags": [], "operationId": ""}) is True

    def test_error_blocks_everything(self):
        spec = FilterSpec(mode=FilterMode.ERROR)
        assert apply_filter(spec, {"path": "/any", "tags": [], "operationId": ""}) is False

    def test_allowlist_passes_matching_tag(self):
        spec = FilterSpec(mode=FilterMode.ALLOWLIST, tags=["public"])
        assert apply_filter(spec, {"path": "/u", "tags": ["public"], "operationId": ""}) is True

    def test_allowlist_blocks_non_matching_tag(self):
        spec = FilterSpec(mode=FilterMode.ALLOWLIST, tags=["public"])
        assert apply_filter(spec, {"path": "/u", "tags": ["internal"], "operationId": ""}) is False

    def test_allowlist_passes_matching_path_glob(self):
        spec = FilterSpec(mode=FilterMode.ALLOWLIST, paths=["/api/*"])
        assert apply_filter(spec, {"path": "/api/users", "tags": [], "operationId": ""}) is True

    def test_allowlist_blocks_non_matching_path(self):
        spec = FilterSpec(mode=FilterMode.ALLOWLIST, paths=["/api/*"])
        assert apply_filter(spec, {"path": "/internal/secret", "tags": [], "operationId": ""}) is False

    def test_allowlist_passes_matching_operation(self):
        spec = FilterSpec(mode=FilterMode.ALLOWLIST, operations=["getUser"])
        assert apply_filter(spec, {"path": "/u", "tags": [], "operationId": "getUser"}) is True

    def test_allowlist_or_logic_operation_wins(self):
        spec = FilterSpec(mode=FilterMode.ALLOWLIST, tags=["public"], operations=["getInternal"])
        route = {"path": "/internal", "tags": ["internal"], "operationId": "getInternal"}
        assert apply_filter(spec, route) is True

    def test_denylist_blocks_matching_tag(self):
        spec = FilterSpec(mode=FilterMode.DENYLIST, tags=["internal"])
        assert apply_filter(spec, {"path": "/s", "tags": ["internal"], "operationId": ""}) is False

    def test_denylist_passes_non_matching_tag(self):
        spec = FilterSpec(mode=FilterMode.DENYLIST, tags=["internal"])
        assert apply_filter(spec, {"path": "/u", "tags": ["public"], "operationId": ""}) is True

    def test_denylist_blocks_matching_path_glob(self):
        spec = FilterSpec(mode=FilterMode.DENYLIST, paths=["/admin/*"])
        assert apply_filter(spec, {"path": "/admin/users", "tags": [], "operationId": ""}) is False

    def test_denylist_passes_non_matching_path(self):
        spec = FilterSpec(mode=FilterMode.DENYLIST, paths=["/admin/*"])
        assert apply_filter(spec, {"path": "/users", "tags": [], "operationId": ""}) is True

    def test_denylist_blocks_matching_operation(self):
        spec = FilterSpec(mode=FilterMode.DENYLIST, operations=["deleteAll"])
        assert apply_filter(spec, {"path": "/nuke", "tags": [], "operationId": "deleteAll"}) is False
