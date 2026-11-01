"""Tests for the image-text-post orchestration module (M2-10, design.md §8.3).

Drives :mod:`deerflow.media_gateway.image_text_post` against
:class:`~deerflow.media_gateway.mock_server.MockMediaGateway` (VLM chat endpoint
from the INSERT-IMGTEXTPOST section) plus pure-function checks for copy
alignment and markdown delivery.
"""

from __future__ import annotations

import json

import pytest

from deerflow.media_gateway.image_text_post import (
    ImageTextPostError,
    build_post,
    build_vlm_question,
    default_copy,
    make_vlm_check,
    parse_vlm_verdict,
    run_image_text_post,
    to_markdown_card,
    validate_copy,
)
from deerflow.media_gateway.mock_server import MockMediaGateway


@pytest.fixture
async def mg():
    mock = MockMediaGateway()
    client = mock.client()
    try:
        yield mock, client
    finally:
        await client.aclose()


async def _completed_image_job(client, *, prompt: str = "海边日落") -> str:
    body = await client.post("/v1/images/generations", json={"prompt": prompt})
    job_id = body["jobs"][0]["id"] if "jobs" in body else body["id"]
    # polls_per_stage=1: queued → running → completed
    await client.get(f"/v1/images/jobs/{job_id}")
    done = await client.get(f"/v1/images/jobs/{job_id}")
    assert done["status"] == "completed"
    return job_id


def _copy_fn(captions: list[str] | None = None, *, title: str = "海边漫步指南", body: str = "海边漫步的三条建议。"):
    async def fn(topic, images, feedback=""):
        return {
            "title": title,
            "body": body,
            "captions": captions if captions is not None else [f"图{i}：{topic}" for i in range(1, len(images) + 1)],
        }

    return fn


class TestFullPipeline:
    @pytest.mark.anyio
    async def test_full_flow_media_ingest_copy_assemble_vlm_pass(self, mg):
        mock, client = mg
        result = await run_image_text_post(
            client,
            mock.admin_token,
            topic="城市夜跑",
            files=[b"\x89PNG-fake-a", b"\x89PNG-fake-b"],
            copy_fn=_copy_fn(),
            vlm_check=make_vlm_check(client, mock.admin_token),
        )
        assert result["status"] == "passed"
        # 素材经 MGW media API 入库（POST /v1/media）
        assert ("POST", "/v1/media") in mock.requests
        # VLM 抽检走 /v1/vlm/chat 且通过
        assert ("POST", "/v1/vlm/chat") in mock.requests
        assert result["qc"]["skipped"] is False
        assert result["qc"]["attempts"][0]["match"] is True
        post = result["post"]
        assert post["title"] == "海边漫步指南"
        assert len(post["images"]) == 2
        for position, image in enumerate(post["images"], start=1):
            assert image["position"] == position
            assert image["source_kind"] == "media"
            assert image["url"].startswith("/api/media-gateway/file/v1/media/")
            assert image["caption"].startswith(f"图{position}：")
        assert result["markdown"].startswith("# 海边漫步指南")
        assert "![图1：城市夜跑]" in result["markdown"]

    @pytest.mark.anyio
    async def test_job_source(self, mg):
        mock, client = mg
        job_id = await _completed_image_job(client)
        result = await run_image_text_post(
            client,
            mock.admin_token,
            topic="日落",
            image_sources=[{"kind": "job", "id": job_id}],
            copy_fn=_copy_fn(),
            vlm_check=None,
        )
        assert result["post"]["images"][0]["source_kind"] == "job"
        assert result["post"]["images"][0]["url"].endswith("/file?kind=image")

    @pytest.mark.anyio
    async def test_empty_sources_and_empty_topic_rejected(self, mg):
        mock, client = mg
        with pytest.raises(ImageTextPostError):
            await run_image_text_post(client, mock.admin_token, topic="t", copy_fn=_copy_fn(), vlm_check=None)
        media = await _uploaded_media(client, mock)
        with pytest.raises(ImageTextPostError):
            await run_image_text_post(client, mock.admin_token, topic="  ", image_sources=[{"kind": "media", "id": media["id"]}], copy_fn=_copy_fn(), vlm_check=None)

    @pytest.mark.anyio
    async def test_unknown_source_kind_rejected(self, mg):
        mock, client = mg
        with pytest.raises(ImageTextPostError) as exc_info:
            await run_image_text_post(client, mock.admin_token, topic="t", image_sources=[{"kind": "url", "id": "x"}], copy_fn=_copy_fn(), vlm_check=None)
        assert "未知组图来源类型" in str(exc_info.value)


class TestVlmRewriteLoop:
    @pytest.mark.anyio
    async def test_vlm_mismatch_rewrites_copy_once_then_passes(self, mg):
        mock, client = mg
        mock.script_vlm(['{"match": false, "reason": "图注说日出但图是夜景"}'])
        feedback_seen: list[str] = []

        def copy_fn(topic, images, feedback=""):
            feedback_seen.append(feedback)
            return default_copy(topic, images, feedback)

        result = await run_image_text_post(
            client,
            mock.admin_token,
            topic="夜航灯塔",
            image_sources=[{"kind": "media", "id": (await _uploaded_media(client, mock))["id"]}],
            copy_fn=copy_fn,
            vlm_check=make_vlm_check(client, mock.admin_token),
        )
        assert result["status"] == "passed"
        assert len(result["qc"]["attempts"]) == 2
        assert result["qc"]["attempts"][0]["match"] is False
        assert result["qc"]["attempts"][1]["match"] is True
        assert feedback_seen[0] == ""  # first draft, no feedback
        assert "夜景" in feedback_seen[1]  # rewrite carries the VLM reason
        assert "（修订：图注说日出但图是夜景）" in result["post"]["body"]

    @pytest.mark.anyio
    async def test_vlm_mismatch_after_rewrite_raises(self, mg):
        mock, client = mg
        mock.script_vlm(['{"match": false, "reason": "不符A"}', '{"match": false, "reason": "不符B"}'])
        media = await _uploaded_media(client, mock)
        with pytest.raises(ImageTextPostError) as exc_info:
            await run_image_text_post(
                client,
                mock.admin_token,
                topic="x",
                image_sources=[{"kind": "media", "id": media["id"]}],
                copy_fn=_copy_fn(),
                vlm_check=make_vlm_check(client, mock.admin_token),
            )
        assert "不符B" in str(exc_info.value)


class TestVlmSkipped:
    @pytest.mark.anyio
    async def test_no_vlm_configured_skips_check(self, mg):
        mock, client = mg
        media = await _uploaded_media(client, mock)
        result = await run_image_text_post(
            client,
            mock.admin_token,
            topic="早餐经济学",
            image_sources=[{"kind": "media", "id": media["id"]}],
            copy_fn=_copy_fn(),
            vlm_check=None,
        )
        assert result["status"] == "passed"
        assert result["qc"]["skipped"] is True
        assert ("POST", "/v1/vlm/chat") not in mock.requests


class TestProxyUrlsAndContract:
    @pytest.mark.anyio
    async def test_artifact_proxy_urls(self, mg):
        mock, client = mg
        job_id = await _completed_image_job(client)
        result = await run_image_text_post(
            client,
            mock.admin_token,
            topic="机械键盘",
            image_sources=[{"kind": "job", "id": job_id}],
            copy_fn=_copy_fn(),
            vlm_check=None,
        )
        (image,) = result["post"]["images"]
        assert image["url"] == f"/api/media-gateway/file/v1/images/jobs/{job_id}/file?kind=image"
        assert image["mgw_path"] == f"/v1/images/jobs/{job_id}/file?kind=image"

    @pytest.mark.anyio
    async def test_vlm_endpoint_contract(self, mg):
        mock, client = mg
        mock.script_vlm(['{"match": true, "reason": ""}'])
        checker = make_vlm_check(client, mock.admin_token, image_data={"/v1/media/x/file": b"\x89PNG"})
        verdict = await checker(build_vlm_question({"title": "t", "images": [{"position": 1, "caption": "c"}]}), ["/v1/media/x/file"])
        assert verdict["match"] is True
        assert ("POST", "/v1/vlm/chat") in mock.requests

    @pytest.mark.anyio
    async def test_vlm_chat_400_on_empty_prompt(self, mg):
        mock, client = mg
        from deerflow.media_gateway.client import MediaGatewayError

        with pytest.raises(MediaGatewayError) as exc_info:
            await client.post("/v1/vlm/chat", json={"prompt": "", "images": []})
        assert exc_info.value.status == 400
        assert exc_info.value.code == "invalid_request"

    @pytest.mark.anyio
    async def test_vlm_chat_requires_auth(self, mg):
        mock, _ = mg
        from deerflow.media_gateway.client import MediaGatewayError

        with pytest.raises(MediaGatewayError) as exc_info:
            await client_no_auth(mock).post("/v1/vlm/chat", json={"prompt": "q", "images": []})
        assert exc_info.value.status == 401


async def _uploaded_media(client, mock: MockMediaGateway) -> dict:
    """Upload one fake PNG into the mock media library (POST /v1/media)."""
    return await client.post_multipart(
        "/v1/media",
        token=mock.admin_token,
        fields={"kind": "images", "title": "material"},
        files=[("file", ("a.png", b"\x89PNG-mock", "application/octet-stream"))],
    )


def client_no_auth(mock: MockMediaGateway):
    from deerflow.media_gateway.client import MediaGatewayClient

    return MediaGatewayClient(mock.base_url, "", transport=mock.transport)


class TestPureFunctions:
    def test_validate_copy_rejects_caption_count_mismatch(self):
        with pytest.raises(ImageTextPostError) as exc_info:
            validate_copy({"title": "t", "body": "b", "captions": ["only"]}, image_count=2)
        assert any("图注数量" in issue for issue in exc_info.value.issues)

    def test_validate_copy_rejects_overlong_caption(self):
        with pytest.raises(ImageTextPostError):
            validate_copy({"title": "t", "body": "b", "captions": ["长" * 61]}, image_count=1)

    def test_build_post_and_markdown(self):
        images = [
            {"kind": "media", "ref": "med_1", "mgw_path": "/v1/media/med_1/file?kind=original", "proxy_url": "/api/media-gateway/file/v1/media/med_1/file?kind=original", "position": 1},
        ]
        copy = {"title": "标题", "body": "正文", "captions": ["图1：说明"]}
        post = build_post("选题", images, copy)
        assert post["layout"]["template"] == "image-text-card"
        markdown = to_markdown_card(post)
        assert "![图1：说明](/api/media-gateway/file/v1/media/med_1/file?kind=original)" in markdown
        assert "*图1：说明*" in markdown

    def test_parse_vlm_verdict_tolerates_code_fence(self):
        verdict = parse_vlm_verdict({"text": '```json\n{"match": false, "reason": "色调不符"}\n```'})
        assert verdict == {"match": False, "reason": "色调不符", "raw": '```json\n{"match": false, "reason": "色调不符"}\n```'}

    def test_parse_vlm_verdict_non_json_negation(self):
        assert parse_vlm_verdict({"text": "图注与画面不一致"})["match"] is False
        assert parse_vlm_verdict({"text": "画面与图注相符"})["match"] is True

    def test_default_copy_alignment(self):
        copy = default_copy("选题", [{"position": 1}, {"position": 2}])
        validated = validate_copy(copy, image_count=2)
        assert len(validated["captions"]) == 2
        assert json.dumps(validated, ensure_ascii=False)
