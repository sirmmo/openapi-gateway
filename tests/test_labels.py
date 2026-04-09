import pytest
import gateway.labels as lbl
from gateway.settings import settings


LABELS_WITH_NS = {
    "gateway.public.enable": "true",
    "gateway.public.port": "9000",
    "gateway.port": "8000",
    "gateway.name": "fallback-name",
}

LABELS_BARE = {
    "gateway.enable": "true",
    "gateway.port": "8080",
}


class TestGet:
    def test_namespaced_key_found(self, monkeypatch):
        monkeypatch.setattr(settings, "namespace", "public")
        assert lbl.get(LABELS_WITH_NS, "port") == "9000"

    def test_namespaced_fallback_to_bare(self, monkeypatch):
        monkeypatch.setattr(settings, "namespace", "public")
        assert lbl.get(LABELS_WITH_NS, "name") == "fallback-name"

    def test_namespaced_returns_none_when_absent(self, monkeypatch):
        monkeypatch.setattr(settings, "namespace", "public")
        assert lbl.get(LABELS_WITH_NS, "docs") is None

    def test_no_namespace_reads_bare(self, monkeypatch):
        monkeypatch.setattr(settings, "namespace", None)
        assert lbl.get(LABELS_BARE, "port") == "8080"

    def test_no_namespace_returns_none_when_absent(self, monkeypatch):
        monkeypatch.setattr(settings, "namespace", None)
        assert lbl.get(LABELS_BARE, "docs") is None


class TestGetDefault:
    def test_returns_value_when_present(self, monkeypatch):
        monkeypatch.setattr(settings, "namespace", None)
        assert lbl.get_default(LABELS_BARE, "port", "0") == "8080"

    def test_returns_default_when_absent(self, monkeypatch):
        monkeypatch.setattr(settings, "namespace", None)
        assert lbl.get_default(LABELS_BARE, "docs", "/openapi.json") == "/openapi.json"


class TestIsEnabled:
    def test_namespaced_enable_true(self, monkeypatch):
        monkeypatch.setattr(settings, "namespace", "public")
        assert lbl.is_enabled(LABELS_WITH_NS) is True

    def test_namespaced_enable_no_fallback(self, monkeypatch):
        # gateway.enable is set but not gateway.public.enable — must not fall back
        monkeypatch.setattr(settings, "namespace", "public")
        assert lbl.is_enabled(LABELS_BARE) is False

    def test_no_namespace_reads_bare_enable(self, monkeypatch):
        monkeypatch.setattr(settings, "namespace", None)
        assert lbl.is_enabled(LABELS_BARE) is True

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.setattr(settings, "namespace", None)
        assert lbl.is_enabled({}) is False

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setattr(settings, "namespace", None)
        assert lbl.is_enabled({"gateway.enable": "TRUE"}) is True


class TestHasPrefix:
    def test_finds_namespaced_prefix(self, monkeypatch):
        monkeypatch.setattr(settings, "namespace", "public")
        labels = {"gateway.public.filter.tags": "foo"}
        assert lbl.has_prefix(labels, "filter.") is True

    def test_finds_bare_prefix(self, monkeypatch):
        monkeypatch.setattr(settings, "namespace", None)
        labels = {"gateway.filter.tags": "foo"}
        assert lbl.has_prefix(labels, "filter.") is True

    def test_returns_false_when_absent(self, monkeypatch):
        monkeypatch.setattr(settings, "namespace", None)
        assert lbl.has_prefix({}, "filter.") is False

    def test_bare_prefix_found_even_with_namespace(self, monkeypatch):
        monkeypatch.setattr(settings, "namespace", "public")
        labels = {"gateway.filter.tags": "foo"}
        assert lbl.has_prefix(labels, "filter.") is True


class TestParseCsv:
    def test_splits_and_strips(self, monkeypatch):
        monkeypatch.setattr(settings, "namespace", None)
        labels = {"gateway.filter.tags": "public, v2 , internal"}
        assert lbl.parse_csv(labels, "filter.tags") == ["public", "v2", "internal"]

    def test_empty_list_when_absent(self, monkeypatch):
        monkeypatch.setattr(settings, "namespace", None)
        assert lbl.parse_csv({}, "filter.tags") == []

    def test_single_value(self, monkeypatch):
        monkeypatch.setattr(settings, "namespace", None)
        labels = {"gateway.filter.tags": "public"}
        assert lbl.parse_csv(labels, "filter.tags") == ["public"]
