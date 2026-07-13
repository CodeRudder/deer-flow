from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.admin.periods import PeriodWindow
from app.gateway.admin.usage_service import AdminUsageService, _session_title
from deerflow.persistence.base import Base
from deerflow.persistence.run.model import RunRow


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("普通用户输入", "普通用户输入"),
        ("请解释 --- BEGIN USER INPUT --- 的含义", "请解释 --- BEGIN USER INPUT --- 的含义"),
        ("  --- BEGIN USER INPUT ---\n真实输入\n--- END USER INPUT ---  ", "真实输入"),
        (None, "thread-fallback"),
    ],
)
def test_session_title_only_removes_complete_input_wrapper(stored, expected):
    assert _session_title(stored, "thread-fallback") == expected


@pytest.mark.asyncio
async def test_usage_includes_terminal_failure_states_and_excludes_end_boundary(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'usage.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    window = PeriodWindow(
        period="custom",
        period_start=datetime(2026, 1, 1, tzinfo=UTC),
        period_end=datetime(2026, 2, 1, tzinfo=UTC),
        label="2026-01-01~2026-01-31",
    )
    try:
        async with sf() as session:
            session.add_all(
                [
                    RunRow(
                        run_id="interrupted",
                        thread_id="thread-1",
                        status="interrupted",
                        created_at=datetime(2026, 1, 10, tzinfo=UTC),
                        total_tokens=40,
                        llm_call_count=1,
                        image_generation_count=1,
                        model_name="legacy-model",
                    ),
                    RunRow(
                        run_id="timeout",
                        thread_id="thread-2",
                        status="timeout",
                        created_at=datetime(2026, 1, 20, tzinfo=UTC),
                        total_tokens=60,
                        llm_call_count=2,
                        token_usage_by_model={"new-model": {"total_tokens": 60, "call_count": 2}},
                    ),
                    RunRow(
                        run_id="historical-model-usage",
                        thread_id="thread-4",
                        status="success",
                        created_at=datetime(2026, 1, 25, tzinfo=UTC),
                        total_tokens=40,
                        token_usage_by_model={"new-model": {"total_tokens": 40}},
                    ),
                    RunRow(
                        run_id="next-period",
                        thread_id="thread-3",
                        status="success",
                        created_at=window.period_end,
                        total_tokens=999,
                        llm_call_count=9,
                    ),
                ]
            )
            await session.commit()

        service = AdminUsageService(sf)
        summary = await service.usage_summary(window=window)
        models = await service.usage_models(window=window)

        assert summary["total_tokens"] == 140
        assert summary["model_requests"] == 3
        assert summary["image_generations"] == 1
        assert summary["run_count"] == 3
        by_model = {item["model"]: item for item in models["items"]}
        assert by_model["legacy-model"]["requests"] is None
        assert by_model["legacy-model"]["runs"] == 1
        assert by_model["new-model"]["requests"] == 2
        assert by_model["new-model"]["tokens"] == 100
        assert by_model["new-model"]["runs"] == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_usage_sessions_unwraps_sanitized_first_human_message(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    window = PeriodWindow(
        period="month",
        period_start=datetime(2026, 7, 1, tzinfo=UTC),
        period_end=datetime(2026, 8, 1, tzinfo=UTC),
        label="2026-07",
    )
    try:
        async with sf() as session:
            session.add(
                RunRow(
                    run_id="wrapped-input",
                    thread_id="thread-1",
                    status="success",
                    created_at=datetime(2026, 7, 10, tzinfo=UTC),
                    total_tokens=120,
                    first_human_message=("--- BEGIN USER INPUT ---\n生成一张图片，动漫新海诚风格，内容你来定\n--- END USER INPUT ---"),
                )
            )
            await session.commit()

        result = await AdminUsageService(sf).usage_sessions(window=window, metric="tokens", limit=20)

        assert result["items"][0]["title"] == "生成一张图片，动漫新海诚风格，内容你来定"
    finally:
        await engine.dispose()
