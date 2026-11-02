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
