"""Unit tests for the Media Gateway provisioner (mapping table + JIT account)."""

from __future__ import annotations

import unicodedata
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from deerflow.media_gateway.client import MediaGatewayError
from deerflow.media_gateway.crypto import MediaGatewayCipher
from deerflow.media_gateway.provisioner import MediaGatewayProvisioner, resolve_mgw_role, slugify_username
from deerflow.persistence.media_gateway import MediaGatewayUserRow


@pytest.fixture
async def session_factory(tmp_path):
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

    url = f"sqlite+aiosqlite:///{tmp_path / 'mgw.db'}"
    await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))
    try:
        yield get_session_factory()
    finally:
        await close_engine()


@pytest.fixture
def cipher():
    return MediaGatewayCipher.from_key("test-mgw-encryption-key")


class FakeClient:
    """Records calls and replays canned responses (mocks MediaGatewayClient)."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.responses: dict[str, object] = {}
        self.next_user_seq = 1

    async def post(self, path, json=None, **kwargs):
        self.calls.append(("POST", path, json))
        if path == "/v1/users":
            if self.responses.get("create_user_409"):
                raise MediaGatewayError(status=409, code="username_exists", message="username already exists")
            uid = f"mgw-u{self.next_user_seq}"
            self.next_user_seq += 1
            self.last_user = {"id": uid, **(json or {})}
            return {"id": uid}
        if path.endswith("/tokens"):
            token = f"h3t_token{len(self.calls)}"
            return {"token": token, "token_info": {"label": "deerflow", "sha8": token[-8:]}}
        raise AssertionError(f"unexpected POST {path}")

    async def get(self, path, params=None, **kwargs):
        self.calls.append(("GET", path, params))
        if path == "/v1/users":
            username = (params or {}).get("username")
            if getattr(self, "last_user", {}).get("username") == username:
                return {"items": [self.last_user]}
            return {"items": []}
        raise AssertionError(f"unexpected GET {path}")

    async def patch(self, path, json=None, **kwargs):
        self.calls.append(("PATCH", path, json))
        return {}

    async def delete(self, path, **kwargs):
        self.calls.append(("DELETE", path, None))
        return {}


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def provisioner(client, session_factory, cipher):
    return MediaGatewayProvisioner(client, session_factory, cipher)


def _user(user_id="11111111-1111-1111-1111-111111111111", username="alice", system_role="user"):
    return SimpleNamespace(id=user_id, username=username, email=f"{username}@example.com", system_role=system_role)


class TestSlugify:
    def test_plain_username_passes_through(self):
        assert slugify_username("alice") == "alice"

    def test_email_is_sanitized(self):
        assert slugify_username("alice@example.com") == "alice-example.com"

    def test_whitespace_and_special_chars_collapse(self):
        assert slugify_username("a b/c:d") == "a-b-c-d"

    def test_control_characters_stripped(self):
        assert slugify_username("ali\x00ce") == "alice"
        assert unicodedata.category("\x00") == "Cc"  # guard the helper assumption

    def test_no_whitespace_or_control_chars_in_output(self):
        slug = slugify_username("ali \x07 ce")
        assert not any(ch.isspace() or unicodedata.category(ch) == "Cc" for ch in slug)

    def test_long_username_truncated_to_32(self):
        assert len(slugify_username("a" * 100)) <= 32

    def test_cjk_username_falls_back_to_random_name(self):
        slug = slugify_username("王小明")
        assert slug.startswith("deerflow-user-")
        assert len(slug) <= 32

    def test_empty_falls_back(self):
        assert slugify_username("///").startswith("deerflow-user-")

    def test_mgw_constraints_hold(self):
        for raw in ["a b:c/d", "x" * 99, "王小明", "", "...\t\n"]:
            slug = slugify_username(raw)
            assert len(slug) <= 32
            assert not any(ch.isspace() or unicodedata.category(ch) == "Cc" or ch in "/:" for ch in slug)


class TestRoleMapping:
    def test_admin_maps_to_admin(self):
        assert resolve_mgw_role("admin") == "admin"

    def test_user_maps_to_user(self):
        assert resolve_mgw_role("user") == "user"
        assert resolve_mgw_role(None) == "user"


class TestEnsureUser:
    @pytest.mark.anyio
    async def test_first_call_creates_user_and_token(self, provisioner, client):
        account = await provisioner.ensure_user(_user())

        assert account.mgw_user_id == "mgw-u1"
        assert account.token.startswith("h3t_")
        assert account.token_sha8
        # POST /v1/users then POST /v1/users/{uid}/tokens with label deerflow
        assert client.calls[0][0] == "POST" and client.calls[0][1] == "/v1/users"
        create_payload = client.calls[0][2]
        assert create_payload["username"] == "alice"
        assert create_payload["role"] == "user"
        assert create_payload["password"]  # random password generated
        assert client.calls[1] == ("POST", "/v1/users/mgw-u1/tokens", {"label": "deerflow"})
        assert client.calls[1] == ("POST", "/v1/users/mgw-u1/tokens", {"label": "deerflow"})

    @pytest.mark.anyio
    async def test_admin_role_is_forwarded(self, provisioner, client):
        await provisioner.ensure_user(_user(system_role="admin"))
        assert client.calls[0][2]["role"] == "admin"

    @pytest.mark.anyio
    async def test_second_call_is_idempotent(self, provisioner, client):
        first = await provisioner.ensure_user(_user())
        second = await provisioner.ensure_user(_user())

        assert first == second
        # Only one user creation + one token issuance ever happened
        assert sum(1 for method, path, _ in client.calls if method == "POST" and path == "/v1/users") == 1

    @pytest.mark.anyio
    async def test_row_is_persisted_encrypted(self, provisioner, session_factory, cipher):
        account = await provisioner.ensure_user(_user())

        async with session_factory() as session:
            row = await session.get(MediaGatewayUserRow, account.deerflow_user_id)
        assert row is not None
        assert row.mgw_username == "alice"
        assert account.token not in row.mgw_token_enc
        assert cipher.decrypt_text(row.mgw_token_enc) == account.token
        assert row.mgw_token_sha8 == account.token_sha8
        assert cipher.decrypt_text(row.mgw_password_enc)  # password stored encrypted

    @pytest.mark.anyio
    async def test_409_claims_existing_user_by_username(self, provisioner, client):
        client.responses["create_user_409"] = True
        # Pretend the user already exists on MGW with this username
        client.last_user = {"id": "mgw-existing", "username": "alice"}

        account = await provisioner.ensure_user(_user())

        assert account.mgw_user_id == "mgw-existing"
        assert client.calls[1] == ("GET", "/v1/users", {"username": "alice"})
        assert client.calls[2] == ("POST", "/v1/users/mgw-existing/tokens", {"label": "deerflow"})

    @pytest.mark.anyio
    async def test_409_unclaimable_raises(self, provisioner, client):
        client.responses["create_user_409"] = True
        client.last_user = {"id": "mgw-someone-else", "username": "bob"}

        with pytest.raises(MediaGatewayError) as exc_info:
            await provisioner.ensure_user(_user())
        assert exc_info.value.code == "username_conflict"

    @pytest.mark.anyio
    async def test_non_409_create_error_propagates(self, provisioner, client):
        async def boom(path, json=None, **kwargs):
            raise MediaGatewayError(status=503, code="unavailable", message="down", retryable=True)

        client.post = boom
        with pytest.raises(MediaGatewayError) as exc_info:
            await provisioner.ensure_user(_user())
        assert exc_info.value.status == 503

    @pytest.mark.anyio
    async def test_cjk_username_provisions_with_fallback_slug(self, provisioner, client):
        account = await provisioner.ensure_user(_user(username="王小明"))
        assert account.mgw_username.startswith("deerflow-user-")
        assert client.calls[0][2]["username"] == account.mgw_username


class TestDisableUser:
    @pytest.mark.anyio
    async def test_disable_patches_mgw_and_marks_row(self, provisioner, client, session_factory):
        account = await provisioner.ensure_user(_user())

        assert await provisioner.disable_user(account.deerflow_user_id) is True
        assert client.calls[-1] == ("PATCH", "/v1/users/mgw-u1", {"disabled": True})

        async with session_factory() as session:
            row = await session.get(MediaGatewayUserRow, account.deerflow_user_id)
        assert row.mgw_disabled is True

    @pytest.mark.anyio
    async def test_disable_unmapped_user_is_noop(self, provisioner, client):
        assert await provisioner.disable_user("no-such-user") is False
        assert client.calls == []


class TestResetToken:
    @pytest.mark.anyio
    async def test_rotation_issues_new_token_and_revokes_old(self, provisioner, client):
        account = await provisioner.ensure_user(_user())
        old_sha8 = account.token_sha8
        client.calls.clear()

        rotated = await provisioner.reset_token(account.deerflow_user_id)

        assert rotated.token != account.token
        assert rotated.token_sha8 != old_sha8
        assert ("POST", "/v1/users/mgw-u1/tokens", {"label": "deerflow"}) in client.calls
        assert ("DELETE", f"/v1/users/mgw-u1/tokens/{old_sha8}", None) in client.calls

        # The stored token now decrypts to the new value
        assert await provisioner.get_token(account.deerflow_user_id) == rotated.token

    @pytest.mark.anyio
    async def test_reset_unmapped_user_raises(self, provisioner):
        with pytest.raises(MediaGatewayError) as exc_info:
            await provisioner.reset_token("no-such-user")
        assert exc_info.value.code == "not_provisioned"

    @pytest.mark.anyio
    async def test_revoke_failure_does_not_fail_rotation(self, provisioner, client):
        account = await provisioner.ensure_user(_user())

        async def failing_delete(path, **kwargs):
            raise MediaGatewayError(status=404, code="not_found", message="token gone")

        client.delete = failing_delete
        rotated = await provisioner.reset_token(account.deerflow_user_id)
        assert rotated.token.startswith("h3t_")


class TestConcurrency:
    @pytest.mark.anyio
    async def test_concurrent_first_calls_converge_on_one_mapping(self, client, session_factory, cipher):
        import asyncio

        provisioner = MediaGatewayProvisioner(client, session_factory, cipher)
        user = _user()

        first, second = await asyncio.gather(provisioner.ensure_user(user), provisioner.ensure_user(user))

        # Both callers end up with the same committed mapping row (the
        # IntegrityError loser re-reads the winner's row).
        assert first.deerflow_user_id == second.deerflow_user_id
        assert first.mgw_user_id == second.mgw_user_id
        assert first.token == second.token

        async with session_factory() as session:
            rows = (await session.execute(select(MediaGatewayUserRow))).scalars().all()
        assert len(rows) == 1
