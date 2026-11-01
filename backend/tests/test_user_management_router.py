"""Focused HTTP-contract tests for administrator user-management routes."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.gateway.admin.user_management_service import UserManagementError
from app.gateway.routers import admin as admin_router


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    application.include_router(admin_router.router)
    return application


@pytest.mark.asyncio
async def test_user_management_routes_require_admin(app, monkeypatch):
    denied = AsyncMock(side_effect=HTTPException(status_code=403, detail="admin required"))
    monkeypatch.setattr(admin_router, "require_admin_user", denied)

    requests = [
        ("GET", "/api/admin/users/summary", None),
        ("GET", "/api/admin/users", None),
        ("POST", "/api/admin/users/user-1/approve", None),
        ("POST", "/api/admin/users/user-1/disable", None),
        ("POST", "/api/admin/users/user-1/enable", None),
        ("POST", "/api/admin/users/user-1/approval-email/retry", None),
        ("PUT", "/api/admin/users/user-1/email", {"email": "new@sz-jlc.com"}),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = [await client.request(method, path, json=body) for method, path, body in requests]

    assert [response.status_code for response in responses] == [403] * len(requests)
    assert denied.await_count == len(requests)


@pytest.mark.asyncio
async def test_user_list_defaults_to_active_and_uses_server_pagination(app, monkeypatch):
    monkeypatch.setattr(admin_router, "require_admin_user", AsyncMock())
    service = MagicMock()
    service.list_users = AsyncMock(return_value={"items": [], "total": 0, "page": 1, "page_size": 15})
    monkeypatch.setattr(admin_router, "_user_management_service", lambda: service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/admin/users")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1, "page_size": 15}
    service.list_users.assert_awaited_once_with(status="active", keyword=None, page=1, page_size=15)


@pytest.mark.asyncio
async def test_user_action_returns_structured_domain_error(app, monkeypatch):
    monkeypatch.setattr(admin_router, "require_admin_user", AsyncMock())
    service = MagicMock()
    service.disable = AsyncMock(
        side_effect=UserManagementError(
            409,
            "invalid_account_status_transition",
            "Cannot disable an account in pending status",
        )
    )
    monkeypatch.setattr(admin_router, "_user_management_service", lambda: service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/admin/users/user-1/disable")

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "invalid_account_status_transition",
        "message": "Cannot disable an account in pending status",
    }
