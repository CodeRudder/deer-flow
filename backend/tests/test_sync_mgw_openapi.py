"""Unit tests for scripts/sync_mgw_openapi.py — fixture-only, no network."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "sync_mgw_openapi.py"

spec = importlib.util.spec_from_file_location("sync_mgw_openapi", SCRIPT)
sync_mod = importlib.util.module_from_spec(spec)
sys.modules.setdefault("sync_mgw_openapi", sync_mod)
spec.loader.exec_module(sync_mod)

FIXTURE_V1 = {
    "openapi": "3.1.0",
    "info": {"title": "H3 生图网关", "version": "0.1.0"},
    "paths": {
        "/v1/images/modes": {
            "get": {"summary": "List Modes", "operationId": "list_modes"},
        },
        "/v1/images/generations": {
            "post": {"summary": "Create Generation", "operationId": "create_gen"},
        },
    },
}

FIXTURE_V2 = {
    "openapi": "3.1.0",
    "info": {"title": "H3 生图网关", "version": "0.2.0"},
    "paths": {
        # summary changed
        "/v1/images/modes": {
            "get": {"summary": "List Image Modes", "operationId": "list_modes"},
        },
        "/v1/images/generations": {
            "post": {"summary": "Create Generation", "operationId": "create_gen"},
        },
        # new endpoint
        "/v1/videos/generations": {
            "post": {"summary": "Create Video Generation"},
        },
        # /v1/auth/login removed
    },
}


class TestSummarize:
    def test_groups_by_path_one_line_per_endpoint(self):
        md = sync_mod.summarize(FIXTURE_V1)
        assert "<!-- GENERATED" in md
        assert "### `/v1/images/modes`" in md
        assert "- `GET /v1/images/modes` — List Modes" in md
        assert "- `POST /v1/images/generations` — Create Generation" in md
        # paths in sorted order
        assert md.index("/v1/images/generations") < md.index("/v1/images/modes")

    def test_deterministic(self):
        assert sync_mod.summarize(FIXTURE_V1) == sync_mod.summarize(FIXTURE_V1)


class TestDiff:
    def test_no_changes(self):
        assert sync_mod.diff_specs(FIXTURE_V1, json.loads(json.dumps(FIXTURE_V1))) == []

    def test_detects_added_removed_changed(self):
        changes = sync_mod.diff_specs(FIXTURE_V1, FIXTURE_V2)
        assert any(c.startswith("ADDED    POST /v1/videos/generations") for c in changes)
        assert any("CHANGED  GET /v1/images/modes" in c for c in changes)
        # v1 -> v2: no removals in this fixture
        assert not any(c.startswith("REMOVED") for c in changes)

    def test_removal_detected(self):
        v1b = json.loads(json.dumps(FIXTURE_V1))
        v2b = json.loads(json.dumps(FIXTURE_V1))
        del v2b["paths"]["/v1/images/modes"]
        changes = sync_mod.diff_specs(v1b, v2b)
        assert changes == ["REMOVED  GET /v1/images/modes"]


class TestResolveBaseUrls:
    def test_env_single(self):
        assert sync_mod.resolve_base_urls(None, {"MEDIA_GATEWAY_BASE_URL": "http://gw:8000"}) == ["http://gw:8000"]

    def test_multiple_candidates_strips_trailing_slash(self):
        urls = sync_mod.resolve_base_urls("http://a:1/,http://b:2", {})
        assert urls == ["http://a:1", "http://b:2"]

    def test_fallback_defaults_when_missing(self):
        assert sync_mod.resolve_base_urls(None, {}) == sync_mod.DEFAULT_CANDIDATES

    def test_override_wins_over_env(self):
        assert sync_mod.resolve_base_urls("http://x", {"MEDIA_GATEWAY_BASE_URL": "http://y"}) == ["http://x"]


class TestMain:
    @pytest.fixture()
    def patched_paths(self, tmp_path, monkeypatch):
        skill_dir = tmp_path / "media-gateway-api"
        skill_dir.mkdir()
        monkeypatch.setattr(sync_mod, "SKILL_DIR", skill_dir)
        monkeypatch.setattr(sync_mod, "SNAPSHOT_PATH", skill_dir / "openapi.json")
        monkeypatch.setattr(sync_mod, "SUMMARY_PATH", skill_dir / "endpoints-summary.md")
        return skill_dir

    def test_sync_and_offline_diff(self, patched_paths, capsys):
        # fake "live" fetch
        class FakeResp:
            status = 200

            def read(self):
                return json.dumps(FIXTURE_V1).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        # patch urlopen on the module
        orig = sync_mod.urllib.request.urlopen
        sync_mod.urllib.request.urlopen = lambda *a, **k: FakeResp()
        try:
            rc = sync_mod.main(["--base-url", "http://fake:8000"])
            assert rc == 0
            snapshot = json.loads((patched_paths / "openapi.json").read_text())
            assert snapshot == FIXTURE_V1
            summary = (patched_paths / "endpoints-summary.md").read_text()
            assert "GET /v1/images/modes" in summary

            # offline check: consistent -> 0; corrupt summary -> 1
            assert sync_mod.main(["--diff", "--offline"]) == 0
            (patched_paths / "endpoints-summary.md").write_text(summary.replace("List Modes", "Drifted"))
            assert sync_mod.main(["--diff", "--offline"]) == 1
        finally:
            sync_mod.urllib.request.urlopen = orig

    def test_diff_detects_change_against_snapshot(self, patched_paths, monkeypatch, capsys):
        (patched_paths / "openapi.json").write_text(json.dumps(FIXTURE_V1))

        class FakeResp:
            status = 200

            def read(self):
                return json.dumps(FIXTURE_V2).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(sync_mod.urllib.request, "urlopen", lambda *a, **k: FakeResp())
        assert sync_mod.main(["--diff", "--base-url", "http://fake:8000"]) == 1
        out = capsys.readouterr().out
        assert "CHANGED  GET /v1/images/modes" in out
        assert "ADDED    POST /v1/videos/generations" in out

    def test_diff_no_change_exits_zero(self, patched_paths, monkeypatch):
        (patched_paths / "openapi.json").write_text(json.dumps(FIXTURE_V1))

        class FakeResp:
            status = 200

            def read(self):
                return json.dumps(FIXTURE_V1).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(sync_mod.urllib.request, "urlopen", lambda *a, **k: FakeResp())
        assert sync_mod.main(["--diff", "--base-url", "http://fake:8000"]) == 0
