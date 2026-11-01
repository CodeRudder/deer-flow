"""视频价目摘要与 providers 富化（feat-df-8 Q-14：选择器内联费率）。"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.admin.quota_service import QuotaService
from app.gateway.routers.models import (
    VideoGenerationModel,
    VideoGenerationProvider,
    VideoGenerationProvidersResponse,
    _attach_video_billing,
)
from deerflow.persistence.base import Base

_RULES = {
    "currency": "CNY",
    "point_to_yuan": 1,
    "models": {
        "MiniMax-H3": {
            "resolutions": {
                "768P": 2,
                "2K": {"price_yuan_per_second": 4, "min_duration": 5, "max_duration": 10},
            },
            "regeneration": 0.3,
        },
        "MiniMax-H3-Max": {
            "resolutions": {
                "480P": 0.33,
                "768P": 0.5,
            },
        },
        "Seedance": {"resolutions": {"1080p": {"durations": {"4": 3.5, "15": 3.0}}}},
    },
}


def _summary() -> dict:
    from app.gateway.admin.quota_service import _video_billing_summary

    return _video_billing_summary(_RULES)


def test_video_billing_summary_shapes():
    summary = _summary()
    minimax = summary["minimax-h3"]
    assert {r["resolution"] for r in minimax["resolutions"]} == {"768P", "2K"}
    by_resolution = {r["resolution"]: r for r in minimax["resolutions"]}
    assert by_resolution["768P"]["yuan_per_second_min"] == 2
    assert by_resolution["768P"]["yuan_per_second_max"] == 2
    # 按秒覆盖取区间
    assert by_resolution["2K"]["yuan_per_second_min"] == 4
    assert minimax["min_duration_seconds"] == 5
    assert minimax["max_duration_seconds"] == 10
    assert minimax["regeneration_yuan_per_second"] == 0.3
    # durations-only 配置取覆盖价区间，模型 key 小写
    seedance = summary["seedance"]
    assert seedance["resolutions"][0]["yuan_per_second_min"] == 3.0
    assert seedance["resolutions"][0]["yuan_per_second_max"] == 3.5
    # 无 regeneration 字段的模型不下发该键
    assert "regeneration_yuan_per_second" not in seedance


async def _service_with_scope(tmp_path, rules) -> QuotaService:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rates.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sf = async_sessionmaker(engine, expire_on_commit=False)
    service = QuotaService(sf)
    service._test_engine = engine  # type: ignore[attr-defined]
    if rules is not None:
        await service.create_scope(
            {
                "code": "video_generation",
                "name": "视频资源",
                "resource_type": "video_generation",
                "match_rules": {"exact": [], "prefix": []},
                "period_type": "weekly",
                "video_enforced": True,
                "video_limit": 100,
                "video_billing_rules": rules,
                "enabled": True,
            },
            updated_by="admin-1",
        )
    return service


@pytest.mark.asyncio
async def test_get_video_billing_summary_returns_rules_summary(tmp_path):
    service = await _service_with_scope(tmp_path, _RULES)
    try:
        summary = await service.get_video_billing_summary()
        assert summary is not None
        assert "minimax-h3" in summary
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_get_video_billing_summary_none_without_scope(tmp_path):
    service = await _service_with_scope(tmp_path, None)
    try:
        assert await service.get_video_billing_summary() is None
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


def _providers_response() -> VideoGenerationProvidersResponse:
    return VideoGenerationProvidersResponse(
        skill_enabled=True,
        providers=[
            VideoGenerationProvider(
                name="minimax_h3",
                display_name="MiniMax H3",
                configured=True,
                models=[
                    VideoGenerationModel(name="MiniMax-H3", display_name="MiniMax H3"),
                    VideoGenerationModel(name="unknown-model", display_name="Unknown"),
                ],
            )
        ],
    )


def test_attach_video_billing_merges_by_lowercased_model_name():
    enriched = _attach_video_billing(_providers_response(), _summary())
    models = enriched.providers[0].models
    assert models[0].billing is not None
    assert models[0].billing.min_duration_seconds == 5
    assert models[1].billing is None
    assert enriched.skill_enabled is True


def test_attach_video_billing_without_summary_keeps_none():
    enriched = _attach_video_billing(_providers_response(), None)
    for model in enriched.providers[0].models:
        assert model.billing is None
