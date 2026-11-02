"""Unit tests for Media Gateway settings (multi-candidate base URL parsing)."""

from __future__ import annotations

import pytest

from deerflow.media_gateway.settings import (
    MediaGatewaySettings,
    get_media_gateway_settings,
    parse_candidate_base_urls,
    set_media_gateway_settings,
)


@pytest.fixture(autouse=True)
def _reset_settings():
    set_media_gateway_settings(None)
    yield
    set_media_gateway_settings(None)


class TestParseCandidateBaseUrls:
    def test_single_url_unchanged(self):
        assert parse_candidate_base_urls("http://172.17.0.1:8000") == ["http://172.17.0.1:8000"]

    def test_single_url_trailing_slash_stripped(self):
        assert parse_candidate_base_urls("http://mgw:8000/") == ["http://mgw:8000"]

    def test_multiple_urls_ordered(self):
        raw = "http://172.17.0.1:8000, http://192.168.1.10:8000"
        assert parse_candidate_base_urls(raw) == ["http://172.17.0.1:8000", "http://192.168.1.10:8000"]

    def test_deduplicates_keeping_first(self):
        raw = "http://a:8000,http://b:8000,http://a:8000/"
        assert parse_candidate_base_urls(raw) == ["http://a:8000", "http://b:8000"]

    def test_empty_segments_dropped(self):
        assert parse_candidate_base_urls(", http://a:8000 ,,") == ["http://a:8000"]

    def test_dirty_input(self):
        assert parse_candidate_base_urls("  http://a:8000  ,, ,http://b ") == ["http://a:8000", "http://b"]

    @pytest.mark.parametrize("raw", [None, "", "   ", ",,"])
    def test_empty_yields_no_candidates(self, raw):
        assert parse_candidate_base_urls(raw) == []


class TestSettingsCandidates:
    def test_candidate_base_urls_from_env_value(self):
        settings = MediaGatewaySettings(base_url="http://a:8000/,http://b:8000")
        assert settings.candidate_base_urls() == ["http://a:8000", "http://b:8000"]
        assert settings.primary_base_url() == "http://a:8000"

    def test_unset_base_url(self):
        settings = MediaGatewaySettings()
        assert settings.candidate_base_urls() == []
        assert settings.primary_base_url() is None

    def test_get_media_gateway_settings_parses_comma_list(self, monkeypatch):
        monkeypatch.setenv("MEDIA_GATEWAY_BASE_URL", "http://a:8000, http://b:8000/")
        settings = get_media_gateway_settings()
        assert settings.candidate_base_urls() == ["http://a:8000", "http://b:8000"]


class TestDisplayConsoleUrl:
    """Console URL priority: external_console_url > external_base_url + /ui > console_url."""

    def test_unset_everything_is_none(self):
        assert MediaGatewaySettings().display_console_url() is None

    def test_internal_console_url_only_keeps_legacy_behaviour(self):
        settings = MediaGatewaySettings(console_url="http://127.0.0.1:8000/ui")
        assert settings.display_console_url() == "http://127.0.0.1:8000/ui"

    def test_external_base_url_builds_console_link(self):
        settings = MediaGatewaySettings(external_base_url="http://100.108.144.120:8000", console_url="http://127.0.0.1:8000/ui")
        assert settings.display_console_url() == "http://100.108.144.120:8000/ui"

    def test_external_base_url_trailing_slash_stripped(self):
        settings = MediaGatewaySettings(external_base_url="https://mgw.example.com/")
        assert settings.display_console_url() == "https://mgw.example.com/ui"

    def test_external_console_url_wins_over_external_base_url(self):
        settings = MediaGatewaySettings(external_base_url="http://100.108.144.120:8000", external_console_url="https://mgw.example.com/console")
        assert settings.display_console_url() == "https://mgw.example.com/console"

    def test_get_media_gateway_settings_reads_external_env(self, monkeypatch):
        monkeypatch.setenv("MEDIA_GATEWAY_EXTERNAL_BASE_URL", "http://100.108.144.120:8000")
        monkeypatch.setenv("MEDIA_GATEWAY_EXTERNAL_CONSOLE_URL", "https://mgw.example.com/console")
        settings = get_media_gateway_settings()
        assert settings.external_base_url == "http://100.108.144.120:8000"
        assert settings.external_console_url == "https://mgw.example.com/console"
        assert settings.display_console_url() == "https://mgw.example.com/console"
