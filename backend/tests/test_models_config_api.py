"""Admin REST endpoints for the UI-managed ``models:`` region of ``config.yaml``.

Task 4 of the model-config UI plan. Every test here runs against a temp copy of
``config.example.yaml`` plus a temp ``.env``, injected through
``DEER_FLOW_CONFIG_PATH`` — nothing depends on the developer's gitignored
``config.yaml``.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from uuid import uuid4

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import models as models_router
from deerflow.config.app_config import AppConfig, get_app_config, reset_app_config
from deerflow.config.models_section import MANAGED_BEGIN, MANAGED_END, load_managed_models

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_EXAMPLE = REPO_ROOT / "config.example.yaml"

# Every ``$VAR`` the example references is pointed at a dummy value so the
# example parses in a bare CI environment.
_ENV_REF_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")

_LITERAL_SECRET = "sk-live-abcdefgh1234"
_MASKED_SECRET = "sk-****1234"

MODEL_ENTRY: dict = {
    "name": "tmp-model",
    "display_name": "Temp Model",
    "use": "deerflow.models.patched_deepseek:PatchedChatDeepSeek",
    "model": "tmp-model-v1",
    "api_key": _LITERAL_SECRET,
    "supports_thinking": False,
}


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _restore_environ():
    """``upsert_env_var`` writes ``os.environ`` directly — keep that contained."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    example = CONFIG_EXAMPLE.read_text(encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(example, encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    for key in set(_ENV_REF_RE.findall(example)):
        monkeypatch.setenv(key, "dummy-from-test")
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(path))
    reset_app_config()
    yield path
    reset_app_config()


#: The entry a deployment has before the Web UI ever wrote to it. Real models
#: written by hand directly under ``models:``, with no managed-region markers —
#: the shape that made the admin list read empty while the models ran fine.
HAND_WRITTEN_ENTRY_LINES = [
    "- name: glm-5.1\n",
    "  display_name: GLM-5.1\n",
    "  use: deerflow.models.patched_deepseek:PatchedChatDeepSeek\n",
    "  model: glm-5.1\n",
    f"  api_key: {_LITERAL_SECRET}\n",
]


def _hand_written(examples: str = "") -> str:
    """``config.example.yaml`` with real entries under ``models:`` and no markers."""
    text = examples or CONFIG_EXAMPLE.read_text(encoding="utf-8")
    return text.replace("models:\n", "models:\n" + "".join(HAND_WRITTEN_ENTRY_LINES), 1)


@pytest.fixture
def hand_written_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    text = _hand_written()
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    for key in set(_ENV_REF_RE.findall(text)):
        monkeypatch.setenv(key, "dummy-from-test")
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(path))
    reset_app_config()
    yield path
    reset_app_config()


def _client(system_role: str) -> TestClient:
    def factory() -> User:
        return User(email="model-config@example.com", password_hash="x", system_role=system_role, id=uuid4())

    app = make_authed_test_app(user_factory=factory)
    app.include_router(models_router.router)
    return TestClient(app)


@pytest.fixture
def admin() -> TestClient:
    return _client("admin")


@pytest.fixture
def user() -> TestClient:
    return _client("user")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _outside_managed_region(text: str) -> list[str]:
    """Every line of *text* except the marker-delimited managed region.

    Before the first write the example carries no markers yet, so every line
    counts as "outside" — which is exactly the invariant under test: no line of
    the original may be reordered or edited.
    """
    lines = text.splitlines(keepends=True)
    markers = [i for i, line in enumerate(lines) if line.strip() in (MANAGED_BEGIN, MANAGED_END)]
    if not markers:
        return lines
    begin, end = markers[0], markers[1]
    return lines[:begin] + lines[end + 1 :]


def _comment_line_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.lstrip().startswith("#"))


def _backups(config_path: Path) -> list[Path]:
    return sorted(config_path.parent.glob(config_path.name + ".bak.*"))


# --------------------------------------------------------------------------- #
# 1-3. Admin gate
# --------------------------------------------------------------------------- #


def test_get_config_masks_the_literal_api_key(admin: TestClient, config_path: Path):
    created = admin.post("/api/models", json=MODEL_ENTRY)
    assert created.status_code == 200
    # No response echoes the secret back, write responses included.
    assert _LITERAL_SECRET not in created.text
    assert created.json()["models"][0]["api_key"] == _MASKED_SECRET

    response = admin.get("/api/models/config")

    assert response.status_code == 200
    models = response.json()["models"]
    assert [model["name"] for model in models] == ["tmp-model"]
    assert models[0]["index"] == 0
    assert models[0]["api_key"] == _MASKED_SECRET
    assert models[0]["api_key_masked"] is True
    # The whole point of the mask: the secret never rides the wire.
    assert _LITERAL_SECRET not in response.text
    assert _LITERAL_SECRET in config_path.read_text(encoding="utf-8")


def test_get_config_requires_admin(user: TestClient, config_path: Path):
    assert user.get("/api/models/config").status_code == 403


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/models/config", None),
        ("PUT", "/api/models/config", {"models": []}),
        ("POST", "/api/models", MODEL_ENTRY),
        ("PUT", "/api/models/tmp-model", MODEL_ENTRY),
        ("DELETE", "/api/models/tmp-model", None),
    ],
)
def test_write_endpoints_require_admin(user: TestClient, config_path: Path, method: str, path: str, body):
    original = config_path.read_bytes()

    response = user.request(method, path, json=body)

    assert response.status_code == 403
    assert config_path.read_bytes() == original
    assert _backups(config_path) == []


# --------------------------------------------------------------------------- #
# 4-6. CRUD
# --------------------------------------------------------------------------- #


def test_post_appends_and_a_duplicate_name_conflicts(admin: TestClient, config_path: Path):
    first = admin.post("/api/models", json=MODEL_ENTRY)
    assert first.status_code == 200
    assert [m["name"] for m in first.json()["models"]] == ["tmp-model"]

    second = admin.post("/api/models", json={**MODEL_ENTRY, "name": "second", "model": "second-v1"})
    assert second.status_code == 200
    assert [m["name"] for m in second.json()["models"]] == ["tmp-model", "second"]

    duplicate = admin.post("/api/models", json=MODEL_ENTRY)
    assert duplicate.status_code == 409
    assert [m["name"] for m in load_managed_models(config_path)] == ["tmp-model", "second"]


def test_put_by_name_updates_in_place_and_unknown_name_is_404(admin: TestClient, config_path: Path):
    admin.post("/api/models", json=MODEL_ENTRY)
    admin.post("/api/models", json={**MODEL_ENTRY, "name": "second", "model": "second-v1"})

    updated = admin.put("/api/models/tmp-model", json={**MODEL_ENTRY, "display_name": "Edited"})

    assert updated.status_code == 200
    models = updated.json()["models"]
    assert [m["name"] for m in models] == ["tmp-model", "second"]
    assert models[0]["display_name"] == "Edited"
    assert models[1]["model"] == "second-v1"

    missing = admin.put("/api/models/nope", json=MODEL_ENTRY)
    assert missing.status_code == 404


def test_delete_by_name_removes_the_entry_and_unknown_name_is_404(admin: TestClient, config_path: Path):
    admin.post("/api/models", json=MODEL_ENTRY)
    admin.post("/api/models", json={**MODEL_ENTRY, "name": "second", "model": "second-v1"})

    deleted = admin.delete("/api/models/tmp-model")

    assert deleted.status_code == 200
    assert [m["name"] for m in deleted.json()["models"]] == ["second"]
    assert [m["name"] for m in load_managed_models(config_path)] == ["second"]

    assert admin.delete("/api/models/tmp-model").status_code == 404


def test_put_config_replaces_the_whole_region(admin: TestClient, config_path: Path):
    admin.post("/api/models", json=MODEL_ENTRY)

    replaced = admin.put("/api/models/config", json={"models": [{**MODEL_ENTRY, "name": "replacement", "model": "replacement-v1"}]})

    assert replaced.status_code == 200
    assert [m["name"] for m in replaced.json()["models"]] == ["replacement"]

    cleared = admin.put("/api/models/config", json={"models": []})
    assert cleared.status_code == 200
    assert cleared.json()["models"] == []
    assert load_managed_models(config_path) == []


# --------------------------------------------------------------------------- #
# 7. Byte-identity outside the managed region
# --------------------------------------------------------------------------- #


def test_outside_managed_region_is_byte_identical_after_writes(admin: TestClient, config_path: Path):
    original = config_path.read_text(encoding="utf-8")
    original_outside = _outside_managed_region(original)
    original_comments = _comment_line_count(original)
    assert original_comments == 1471, "config.example.yaml is the comment-heavy reference fixture"

    assert admin.post("/api/models", json=MODEL_ENTRY).status_code == 200
    assert admin.put("/api/models/tmp-model", json={**MODEL_ENTRY, "model": "tmp-model-v2"}).status_code == 200
    assert admin.put("/api/models/config", json={"models": [{**MODEL_ENTRY, "name": "third"}]}).status_code == 200
    assert admin.delete("/api/models/third").status_code == 200

    updated = config_path.read_text(encoding="utf-8")

    assert _outside_managed_region(updated) == original_outside
    # Only the two marker comments are new; every documented comment survives.
    assert _comment_line_count(updated) == original_comments + 2


def test_managed_region_anchor_does_not_disturb_the_commented_examples(admin: TestClient, config_path: Path):
    admin.post("/api/models", json=MODEL_ENTRY)

    text = config_path.read_text(encoding="utf-8")

    # The commented templates under `models:` are documentation, not config.
    assert "# - name: doubao-seed-1.8" in text
    assert "#   api_key: $VOLCENGINE_API_KEY" in text
    # ...and they stay *below* the region, still commented.
    assert text.index(MANAGED_END) < text.index("# - name: doubao-seed-1.8")


# --------------------------------------------------------------------------- #
# 7b. A config the Web UI has never written to (hand-written, no markers yet)
# --------------------------------------------------------------------------- #


def test_get_config_reports_a_hand_written_configs_entries(admin: TestClient, hand_written_path: Path):
    """No markers must not read as "no models" — that is what the admin list showed.

    Not a cosmetic bug: a save writes back only the entries the UI submitted, so
    a model missing from this list is a model the next save deletes.
    """
    response = admin.get("/api/models/config")

    assert response.status_code == 200
    assert [model["name"] for model in response.json()["models"]] == ["glm-5.1"]


def test_first_write_migrates_a_hand_written_config_without_losing_the_entry(admin: TestClient, hand_written_path: Path):
    original = hand_written_path.read_text(encoding="utf-8")
    # The entry lines ARE the section's value, so the migration may take them
    # over. Every other line — the ~1471 comments, every other section — may not
    # move by a byte, and that is what "outside the region" compares.
    outside_before = [line for line in original.splitlines(keepends=True) if line not in HAND_WRITTEN_ENTRY_LINES]

    added = admin.post("/api/models", json={**MODEL_ENTRY, "name": "second", "model": "second-v1"})

    assert added.status_code == 200
    assert [model["name"] for model in added.json()["models"]] == ["glm-5.1", "second"]

    updated = hand_written_path.read_text(encoding="utf-8")
    assert _outside_managed_region(updated) == outside_before

    # Both entries sit between the markers: the hand-written one was migrated
    # into the region rather than left behind below it.
    begin, end = updated.index(MANAGED_BEGIN), updated.index(MANAGED_END)
    assert begin < updated.index("- name: glm-5.1") < end
    assert begin < updated.index("- name: second") < end
    # The two markers are the only new comment lines.
    assert _comment_line_count(updated) == _comment_line_count(original) + 2


def test_every_write_after_the_migration_is_confined_to_the_region(admin: TestClient, hand_written_path: Path):
    """Repeated add / update / delete rounds may not drift anything outside."""
    assert admin.post("/api/models", json=MODEL_ENTRY).status_code == 200  # the migration
    after_migration = _outside_managed_region(hand_written_path.read_text(encoding="utf-8"))

    edited = {**MODEL_ENTRY, "name": "glm-5.1", "model": "glm-5.1", "display_name": "GLM-5.1"}
    for round_index in range(3):
        tagged = {**MODEL_ENTRY, "name": f"extra-{round_index}", "model": f"extra-{round_index}-v1"}
        assert admin.post("/api/models", json=tagged).status_code == 200
        assert admin.put("/api/models/glm-5.1", json={**edited, "display_name": f"GLM-5.1 r{round_index}"}).status_code == 200
        assert admin.delete(f"/api/models/extra-{round_index}").status_code == 200

    updated = hand_written_path.read_text(encoding="utf-8")

    assert _outside_managed_region(updated) == after_migration
    # The hand-written entry survived the churn, still edit-able and still holding
    # its literal credential.
    stored = load_managed_models(hand_written_path)
    assert [model["name"] for model in stored] == ["glm-5.1", "tmp-model"]
    assert stored[0]["display_name"] == "GLM-5.1 r2"
    assert stored[0]["api_key"] == _LITERAL_SECRET


def test_saving_the_list_back_unchanged_keeps_every_entry(admin: TestClient, hand_written_path: Path):
    """The shape of "open settings, save nothing, close" must not lose a model.

    Getting this wrong is how a hand-written entry disappears: the list reads
    empty, so the round-tripped save writes an empty region over it.
    """
    assert admin.post("/api/models", json=MODEL_ENTRY).status_code == 200  # migrate first
    before_entries = load_managed_models(hand_written_path)
    outside_before = _outside_managed_region(hand_written_path.read_text(encoding="utf-8"))

    listed = admin.get("/api/models/config").json()["models"]
    submitted = [{key: value for key, value in entry.items() if key not in ("index", "api_key_masked")} for entry in listed]
    assert admin.put("/api/models/config", json={"models": submitted}).status_code == 200

    assert load_managed_models(hand_written_path) == before_entries
    assert _outside_managed_region(hand_written_path.read_text(encoding="utf-8")) == outside_before


def test_the_region_is_byte_stable_once_it_has_been_written(admin: TestClient, hand_written_path: Path):
    """Writes converge; they do not drift a little on every save.

    Bytes are only comparable from the second save onwards: the first one
    normalises key order inside the region, because ``ManagedModelEntry`` dumps
    its declared fields before the provider-specific extras. That is a rewrite
    confined to the region the operator is told the UI owns — outside it, nothing
    moves at all, which is the guarantee the byte-identity tests above pin.
    """
    assert admin.post("/api/models", json=MODEL_ENTRY).status_code == 200
    listed = admin.get("/api/models/config").json()["models"]
    submitted = [{key: value for key, value in entry.items() if key not in ("index", "api_key_masked")} for entry in listed]

    assert admin.put("/api/models/config", json={"models": submitted}).status_code == 200
    settled = hand_written_path.read_bytes()

    assert admin.put("/api/models/config", json={"models": submitted}).status_code == 200

    assert hand_written_path.read_bytes() == settled
    # The mask the UI was shown round-tripped back to the stored credential.
    assert load_managed_models(hand_written_path)[0]["api_key"] == _LITERAL_SECRET


# --------------------------------------------------------------------------- #
# 8-9. Rollback on a bad candidate / backup on a good one
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("endpoint", ["post", "put"])
def test_invalid_candidate_is_rejected_without_touching_config_or_writing_a_backup(admin: TestClient, config_path: Path, endpoint: str):
    """An unresolvable ``$VAR`` passes ModelConfig but fails AppConfig.from_file.

    Nothing may be written: validation runs before the backup/replace steps, so
    a rejected candidate must leave neither a modified file nor a stray backup.
    """
    original = config_path.read_bytes()
    bad_entry = {**MODEL_ENTRY, "api_key": "$DEFINITELY_NOT_SET_ANYWHERE"}

    if endpoint == "post":
        response = admin.post("/api/models", json=bad_entry)
    else:
        response = admin.put("/api/models/config", json={"models": [bad_entry]})

    assert response.status_code == 400
    assert "DEFINITELY_NOT_SET_ANYWHERE" in response.json()["detail"]
    assert config_path.read_bytes() == original
    assert _backups(config_path) == []


def test_malformed_entry_is_rejected_without_touching_config(admin: TestClient, config_path: Path):
    original = config_path.read_bytes()

    missing_use = admin.post("/api/models", json={"name": "broken", "model": "m"})
    assert missing_use.status_code == 400

    # A shape the request model itself rejects is a plain 422; either way it is
    # a client error and nothing is written.
    not_an_object = admin.put("/api/models/config", json={"models": ["not-an-object"]})
    assert not_an_object.status_code in (400, 422)

    assert config_path.read_bytes() == original
    assert _backups(config_path) == []


def test_successful_write_backs_up_the_pre_write_original(admin: TestClient, config_path: Path):
    original = config_path.read_bytes()

    assert admin.post("/api/models", json=MODEL_ENTRY).status_code == 200

    backups = _backups(config_path)
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert config_path.read_bytes() != original


# --------------------------------------------------------------------------- #
# 10. Masked-key round trip
# --------------------------------------------------------------------------- #


def test_masked_key_round_trip_keeps_the_stored_secret(admin: TestClient, config_path: Path):
    admin.post("/api/models", json=MODEL_ENTRY)
    fetched = admin.get("/api/models/config").json()["models"]
    assert fetched[0]["api_key"] == _MASKED_SECRET

    # The UI posts back exactly what it was shown, metadata keys included.
    replaced = admin.put("/api/models/config", json={"models": fetched})
    assert replaced.status_code == 200

    assert load_managed_models(config_path)[0]["api_key"] == _LITERAL_SECRET
    assert _MASKED_SECRET not in config_path.read_text(encoding="utf-8")


def test_masked_key_round_trip_through_the_single_entry_put(admin: TestClient, config_path: Path):
    admin.post("/api/models", json=MODEL_ENTRY)
    fetched = admin.get("/api/models/config").json()["models"][0]

    response = admin.put("/api/models/tmp-model", json={**fetched, "display_name": "Edited"})

    assert response.status_code == 200
    stored = load_managed_models(config_path)[0]
    assert stored["api_key"] == _LITERAL_SECRET
    assert stored["display_name"] == "Edited"
    # The response-only metadata keys must not leak into the file.
    assert "index" not in stored
    assert "api_key_masked" not in stored


def test_empty_api_key_keeps_the_stored_secret(admin: TestClient, config_path: Path):
    admin.post("/api/models", json=MODEL_ENTRY)

    response = admin.put("/api/models/tmp-model", json={**MODEL_ENTRY, "api_key": ""})

    assert response.status_code == 200
    assert load_managed_models(config_path)[0]["api_key"] == _LITERAL_SECRET


def test_empty_api_key_with_nothing_stored_is_not_persisted(admin: TestClient, config_path: Path):
    """Nothing to preserve must not become a `api_key: ''` in the file."""
    response = admin.post("/api/models", json={**MODEL_ENTRY, "api_key": ""})

    assert response.status_code == 200
    stored = load_managed_models(config_path)[0]
    assert "api_key" not in stored
    assert "api_key" not in response.json()["models"][0]


# --------------------------------------------------------------------------- #
# 11. Hot reload
# --------------------------------------------------------------------------- #


def test_successful_write_is_visible_to_get_app_config_without_a_reset(admin: TestClient, config_path: Path):
    # Warm the singleton against this test's temp config first, so the assertion
    # below really proves the content-signature reload fired.
    assert [m.name for m in get_app_config().models] == []

    assert admin.post("/api/models", json=MODEL_ENTRY).status_code == 200

    assert [m.name for m in get_app_config().models] == ["tmp-model"]


def test_delete_is_visible_to_get_app_config(admin: TestClient, config_path: Path):
    admin.post("/api/models", json=MODEL_ENTRY)
    assert [m.name for m in get_app_config().models] == ["tmp-model"]

    assert admin.delete("/api/models/tmp-model").status_code == 200

    assert [m.name for m in get_app_config().models] == []


# --------------------------------------------------------------------------- #
# 12. `$NEW_KEY` ordering regression
# --------------------------------------------------------------------------- #


def test_new_env_reference_is_written_before_validation(admin: TestClient, config_path: Path):
    """Without the ``upsert_env_var`` step the ``$VAR`` resolves to None and every
    save of a brand-new credential fails validation. This pins the fixed order.
    """
    env_file = config_path.parent / ".env"

    response = admin.post("/api/models", json={**MODEL_ENTRY, "api_key": "$BRAND_NEW_KEY", "api_key_value": "sk-brand-new-9999"})

    assert response.status_code == 200
    assert "BRAND_NEW_KEY=sk-brand-new-9999" in env_file.read_text(encoding="utf-8")
    assert os.environ["BRAND_NEW_KEY"] == "sk-brand-new-9999"
    assert load_managed_models(config_path)[0]["api_key"] == "$BRAND_NEW_KEY"
    # A `$VAR` is a pointer, not a credential — it is never masked.
    assert response.json()["models"][0]["api_key"] == "$BRAND_NEW_KEY"
    assert response.json()["models"][0]["api_key_masked"] is False


def test_new_env_reference_without_a_value_fails_cleanly(admin: TestClient, config_path: Path):
    original = config_path.read_bytes()

    response = admin.post("/api/models", json={**MODEL_ENTRY, "api_key": "$STILL_MISSING_KEY"})

    assert response.status_code == 400
    assert config_path.read_bytes() == original
    assert _backups(config_path) == []
    assert "STILL_MISSING_KEY" not in os.environ


def test_new_env_reference_works_through_the_whole_region_replace(admin: TestClient, config_path: Path):
    response = admin.put("/api/models/config", json={"models": [{**MODEL_ENTRY, "api_key": "$ANOTHER_NEW_KEY", "api_key_value": "sk-other-1234"}]})

    assert response.status_code == 200
    assert "ANOTHER_NEW_KEY=sk-other-1234" in (config_path.parent / ".env").read_text(encoding="utf-8")
    assert load_managed_models(config_path)[0]["api_key"] == "$ANOTHER_NEW_KEY"


# --------------------------------------------------------------------------- #
# 13. `use` must resolve — otherwise the save "succeeds" but the first chat fails
# --------------------------------------------------------------------------- #


def test_nonexistent_provider_module_is_rejected(admin: TestClient, config_path: Path):
    """``ModelConfig.use`` is a bare string, so nothing else checks the class path.

    Without this gate the save returns 200, config.yaml is rewritten, and the
    breakage only surfaces on the first chat turn — by which point the pre-write
    backup is the only way back.
    """
    original = config_path.read_bytes()

    response = admin.post("/api/models", json={**MODEL_ENTRY, "use": "nonexistent.module:Nope"})

    assert response.status_code == 400
    assert "nonexistent.module:Nope" in response.json()["detail"]
    assert config_path.read_bytes() == original
    assert _backups(config_path) == []


def test_provider_module_with_a_missing_attribute_is_rejected(admin: TestClient, config_path: Path):
    """The module exists but the class does not — same 400, same clean rollback."""
    original = config_path.read_bytes()

    response = admin.post("/api/models", json={**MODEL_ENTRY, "use": "langchain_openai:NoSuchClass"})

    assert response.status_code == 400
    assert "langchain_openai:NoSuchClass" in response.json()["detail"]
    assert config_path.read_bytes() == original
    assert _backups(config_path) == []


def test_unresolvable_use_is_rejected_through_the_whole_region_replace(admin: TestClient, config_path: Path):
    """The full-region PUT validates every entry it would write, not just one."""
    admin.post("/api/models", json=MODEL_ENTRY)
    original = config_path.read_bytes()
    backups_before = _backups(config_path)

    response = admin.put("/api/models/config", json={"models": [MODEL_ENTRY, {**MODEL_ENTRY, "name": "bad", "use": "nonexistent.module:Nope"}]})

    assert response.status_code == 400
    assert "nonexistent.module:Nope" in response.json()["detail"]
    assert config_path.read_bytes() == original
    # The rejected PUT added no backup of its own.
    assert _backups(config_path) == backups_before


def test_a_valid_provider_class_still_saves(admin: TestClient, config_path: Path):
    """The happy path must survive the new check — ChatOpenAI is the documented default."""
    response = admin.post("/api/models", json={**MODEL_ENTRY, "use": "langchain_openai:ChatOpenAI", "api_key": "$OPENAI_API_KEY"})

    assert response.status_code == 200
    assert load_managed_models(config_path)[0]["use"] == "langchain_openai:ChatOpenAI"
    assert [m.name for m in get_app_config().models] == ["tmp-model"]


def test_unresolvable_use_and_a_new_env_reference_compose(admin: TestClient, config_path: Path):
    """A payload can be wrong in both ways at once; both must be caught, and the
    ``.env`` residue from the fixed ordering stays the accepted outcome."""
    original = config_path.read_bytes()

    response = admin.post("/api/models", json={**MODEL_ENTRY, "use": "nonexistent.module:Nope", "api_key": "$COMPOSED_NEW_KEY", "api_key_value": "sk-composed-1234"})

    assert response.status_code == 400
    assert "nonexistent.module:Nope" in response.json()["detail"]
    assert config_path.read_bytes() == original
    assert _backups(config_path) == []
    # Intentional residue, documented in models_section: the key stays in .env
    # while config.yaml is untouched. Never rolled back.
    assert "COMPOSED_NEW_KEY=sk-composed-1234" in (config_path.parent / ".env").read_text(encoding="utf-8")


def test_unresolvable_use_is_rejected_by_validate_candidate_text(config_path: Path):
    """The gate lives in validation itself, not just in the router's payload path."""
    from deerflow.config.models_section import replace_managed_section, validate_candidate_text

    candidate = replace_managed_section(config_path.read_text(encoding="utf-8"), [{**MODEL_ENTRY, "use": "nonexistent.module:Nope"}])

    with pytest.raises(ValueError, match="nonexistent.module:Nope"):
        validate_candidate_text(candidate, dir_path=config_path.parent)


# --------------------------------------------------------------------------- #
# 12-18. Connectivity probe (`POST /api/models/test`)
#
# A probe is a business result, not an HTTP error: the only non-200 outcomes are
# a malformed body (422) and a non-admin caller (403). No test here reaches the
# network — the provider seam (`deerflow.models.factory.create_chat_model`) is
# monkeypatched, or the candidate's `use` fails to resolve before any client is
# constructed.
# --------------------------------------------------------------------------- #


class _FakeChatModel:
    """Stand-in for a LangChain chat model: the only surface the probe uses."""

    def __init__(self, *, delay: float = 0.0, error: Exception | None = None):
        self._delay = delay
        self._error = error
        self.calls: list[object] = []

    async def ainvoke(self, prompt: object, *args, **kwargs) -> str:
        self.calls.append(prompt)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return "pong"


def _patch_factory(monkeypatch: pytest.MonkeyPatch, model: _FakeChatModel) -> dict:
    """Replace the model factory and capture how the probe called it."""
    captured: dict = {}

    def fake_create(name=None, thinking_enabled=False, *, app_config=None, attach_tracing=True, **kwargs):
        captured.update(name=name, thinking_enabled=thinking_enabled, app_config=app_config, attach_tracing=attach_tracing)
        return model

    monkeypatch.setattr(models_router.model_factory, "create_chat_model", fake_create)
    return captured


def test_test_endpoint_reports_success(admin: TestClient, config_path: Path, monkeypatch: pytest.MonkeyPatch):
    model = _FakeChatModel()
    captured = _patch_factory(monkeypatch, model)

    response = admin.post("/api/models/test", json=MODEL_ENTRY)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert isinstance(body["latency_ms"], int)
    assert body["latency_ms"] >= 0
    assert body["error"] is None
    # The candidate — not a copy from disk — is what got built and invoked.
    assert captured["name"] == "tmp-model"
    assert captured["app_config"].get_model_config("tmp-model").model == "tmp-model-v1"
    assert captured["attach_tracing"] is False
    assert len(model.calls) == 1


# The probe must send the thinking mode the entry configures, because
# `create_chat_model` turns `thinking_enabled=False` into the model's
# `when_thinking_disabled` block (factory.py: `if not thinking_enabled: ...
# update(when_thinking_disabled)`). Omitting the argument defaults it to False,
# so a model whose endpoint rejects `thinking.type: disabled` failed the probe
# even though every real chat turn would work — measured against a live
# Anthropic-compatible endpoint: "400 Upstream bad request: thinking.type
# `disabled` is not supported by this model".


def test_test_probe_builds_a_thinking_model_with_thinking_on(admin: TestClient, config_path: Path, monkeypatch: pytest.MonkeyPatch):
    captured = _patch_factory(monkeypatch, _FakeChatModel())
    entry = {**MODEL_ENTRY, "supports_thinking": True, "when_thinking_enabled": {"thinking": {"type": "enabled"}}, "when_thinking_disabled": {"thinking": {"type": "disabled"}}}

    assert admin.post("/api/models/test", json=entry).status_code == 200

    assert captured["thinking_enabled"] is True


def test_test_probe_builds_a_plain_model_with_thinking_off(admin: TestClient, config_path: Path, monkeypatch: pytest.MonkeyPatch):
    captured = _patch_factory(monkeypatch, _FakeChatModel())

    assert admin.post("/api/models/test", json=MODEL_ENTRY).status_code == 200

    assert captured["thinking_enabled"] is False


def _built_by_the_real_factory(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Keep the real factory; hand back the model the probe actually built.

    ``thinking_enabled`` is only interesting because of what the factory does
    with it, so these assertions are made against the constructed model rather
    than against the argument.
    """
    from deerflow.models import factory as model_factory_module

    captured: dict = {}
    real_create = model_factory_module.create_chat_model

    def recording_create(name=None, thinking_enabled=False, **kwargs):
        model = real_create(name, thinking_enabled=thinking_enabled, **kwargs)
        captured["model"] = model
        return model

    monkeypatch.setattr(models_router.model_factory, "create_chat_model", recording_create)
    return captured


def _thinking_payload(model) -> object:
    """Wherever the provider keeps it: a declared field, or ``model_kwargs``.

    ``ChatAnthropic`` declares ``thinking``, so the block lands on the attribute;
    ``langchain_openai:ChatOpenAI`` does not, so LangChain parks it in
    ``model_kwargs``. Both are the same value on the wire, and a test that only
    looked at one would pass on the provider that hides the bug.
    """
    return getattr(model, "thinking", None) or getattr(model, "model_kwargs", {}).get("thinking")


_DISABLED_ENTRY = {
    "name": "tmp-model",
    "model": "tmp-model-v1",
    "use": "langchain_anthropic:ChatAnthropic",
    "api_key": "sk-live-abcdefgh1234",
    "when_thinking_enabled": {"thinking": {"type": "enabled", "budget_tokens": 4096}},
    "when_thinking_disabled": {"thinking": {"type": "disabled"}},
}


def test_test_probe_probes_a_thinking_model_in_its_enabled_mode(admin: TestClient, config_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The regression: a thinking entry must not be probed in a mode it rejects.

    Before the fix this asserted the opposite outcome — the probe always reached
    for `when_thinking_disabled`, and endpoints that refuse
    `thinking.type: disabled` answered 400 for an entry that works.
    """
    captured = _built_by_the_real_factory(monkeypatch)

    assert admin.post("/api/models/test", json={**_DISABLED_ENTRY, "supports_thinking": True}).status_code == 200

    assert _thinking_payload(captured["model"]) == {"type": "enabled", "budget_tokens": 4096}


def test_test_probe_still_sends_the_disabled_block_when_nothing_declares_thinking(admin: TestClient, config_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The fix must not become a blanket suppression of the entry's own settings.

    An entry that configures a disable block without declaring thinking support
    is self-contradictory, but the app will send that block the moment the chat
    toggle goes off — so the probe has to send it too and report what happens.
    """
    captured = _built_by_the_real_factory(monkeypatch)

    assert admin.post("/api/models/test", json=_DISABLED_ENTRY).status_code == 200

    assert _thinking_payload(captured["model"]) == {"type": "disabled"}


def test_test_endpoint_turns_provider_errors_into_a_business_result(admin: TestClient, config_path: Path, monkeypatch: pytest.MonkeyPatch):
    _patch_factory(monkeypatch, _FakeChatModel(error=RuntimeError("401 Incorrect API key provided")))

    response = admin.post("/api/models/test", json=MODEL_ENTRY)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "401" in body["error"]
    assert isinstance(body["latency_ms"], int)
    assert body["latency_ms"] >= 0


def test_test_endpoint_bounds_a_hung_provider(admin: TestClient, config_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(models_router, "_PROBE_TIMEOUT_SECONDS", 0.05)
    _patch_factory(monkeypatch, _FakeChatModel(delay=30.0))

    response = admin.post("/api/models/test", json=MODEL_ENTRY)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "Timed out" in body["error"]


@pytest.mark.parametrize("use", ["nonexistent.module:Nope", "deerflow.models.patched_deepseek:NotAThing"])
def test_test_endpoint_reports_an_unresolvable_use(admin: TestClient, config_path: Path, use: str):
    """No factory patch here: the real `create_chat_model` must fail at resolution."""
    response = admin.post("/api/models/test", json={**MODEL_ENTRY, "use": use})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"]


@pytest.mark.parametrize("payload", [{**MODEL_ENTRY, "use": None}, {"api_key_value": "sk-orphan"}])
def test_test_endpoint_never_500s_on_a_body_it_cannot_build(admin: TestClient, config_path: Path, payload: dict):
    response = admin.post("/api/models/test", json=payload)

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["error"]


def test_test_endpoint_requires_admin(user: TestClient, config_path: Path):
    original = config_path.read_bytes()

    response = user.post("/api/models/test", json=MODEL_ENTRY)

    assert response.status_code == 403
    assert config_path.read_bytes() == original
    assert _backups(config_path) == []


def test_test_endpoint_writes_nothing(admin: TestClient, config_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A probe is read-only: config.yaml, .env, and the directory all stay put."""
    _patch_factory(monkeypatch, _FakeChatModel())
    config_before = config_path.read_bytes()
    env_path = config_path.parent / ".env"
    env_before = env_path.read_bytes()
    listing_before = sorted(entry.name for entry in config_path.parent.iterdir())

    response = admin.post("/api/models/test", json=MODEL_ENTRY)

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert config_path.read_bytes() == config_before
    assert env_path.read_bytes() == env_before
    assert sorted(entry.name for entry in config_path.parent.iterdir()) == listing_before
    assert _backups(config_path) == []


def test_test_endpoint_resolves_a_new_env_reference_without_persisting(admin: TestClient, config_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`api_key_value` carries the cleartext for a `$VAR` that is not in .env yet;
    the probe must use it and must not create the variable on disk."""
    captured = _patch_factory(monkeypatch, _FakeChatModel())
    env_path = config_path.parent / ".env"

    response = admin.post("/api/models/test", json={**MODEL_ENTRY, "api_key": "$BRAND_NEW_PROBE_KEY", "api_key_value": "sk-probe-9876"})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert captured["app_config"].get_model_config("tmp-model").api_key == "sk-probe-9876"
    assert "BRAND_NEW_PROBE_KEY" not in env_path.read_text(encoding="utf-8")
    assert "BRAND_NEW_PROBE_KEY" not in os.environ


def test_test_endpoint_stays_200_when_the_config_file_is_missing(admin: TestClient, config_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The write endpoints answer a missing config.yaml with a 500; the probe must
    not. Its contract is that a well-formed admin request only ever gets a 200."""
    _patch_factory(monkeypatch, _FakeChatModel())
    config_path.unlink()

    response = admin.post("/api/models/test", json=MODEL_ENTRY)

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["error"]


# --------------------------------------------------------------------------- #
# 19. Provider presets (`GET /api/models/providers`)
#
# The form asks an operator to pick a provider by label, never to type a class
# path. The preset table is static, but its *availability* is not: each `use` is
# resolved through the same `deerflow.reflection.resolve_class` the save path
# validates with, so a provider whose package is not installed is offered as
# `available: false` + an install hint instead of being silently selectable and
# then rejected at save time.
# --------------------------------------------------------------------------- #

#: The full key set every entry must carry.
_PROVIDER_KEYS = {
    "key",
    "label",
    "use",
    "default_api_base",
    "api_base_field",
    "supports_thinking",
    "thinking_enabled",
    "thinking_disabled",
    "thinking_needs_budget",
    "default_budget_tokens",
    "available",
    "reason",
}


def _providers_by_key(client: TestClient) -> dict[str, dict]:
    response = client.get("/api/models/providers")
    assert response.status_code == 200
    return {entry["key"]: entry for entry in response.json()["providers"]}


def test_providers_requires_admin(user: TestClient, config_path: Path):
    response = user.get("/api/models/providers")

    assert response.status_code == 403


def test_providers_lists_the_preset_table(admin: TestClient, config_path: Path):
    response = admin.get("/api/models/providers")

    assert response.status_code == 200
    providers = response.json()["providers"]
    assert providers, "the preset table must not be empty"
    keys = [entry["key"] for entry in providers]
    assert len(keys) == len(set(keys)), "provider keys must be unique"
    for entry in providers:
        assert set(entry) == _PROVIDER_KEYS, f"incomplete entry for {entry.get('key')}"
        assert entry["use"], f"{entry['key']} must carry a class path"
        assert entry["label"], f"{entry['key']} must carry a label"

    by_key = _providers_by_key(admin)
    assert {"openai", "openai-compatible", "doubao", "deepseek", "kimi", "minimax", "anthropic", "google", "ollama"} <= set(by_key)
    # Doubao's endpoint is a real, non-invented value from config.example.yaml.
    assert by_key["doubao"]["use"] == "deerflow.models.patched_deepseek:PatchedChatDeepSeek"
    assert by_key["doubao"]["default_api_base"] == "https://ark.cn-beijing.volces.com/api/v3"


def test_providers_marks_a_resolvable_use_available(admin: TestClient, config_path: Path):
    entry = _providers_by_key(admin)["openai"]

    assert entry["use"] == "langchain_openai:ChatOpenAI"
    assert entry["available"] is True
    assert entry["reason"] is None


def test_providers_marks_an_uninstalled_provider_unavailable(admin: TestClient, config_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The probe is a real `resolve_class` attempt, not a hardcoded flag.

    A preset whose module cannot be imported comes back `available: false` with
    the resolver's install hint, so the dropdown can explain itself instead of
    letting the operator pick something the save path will reject.
    """
    monkeypatch.setattr(
        models_router,
        "_PROVIDER_PRESETS",
        (*models_router._PROVIDER_PRESETS, models_router._ProviderPreset(key="nope", label="Nope", use="deerflow.no_such_module:NoSuchModel")),
    )

    entry = _providers_by_key(admin)["nope"]

    assert entry["available"] is False
    assert entry["reason"] and "deerflow.no_such_module" in entry["reason"]


def test_ollama_is_listed_with_its_install_hint(admin: TestClient, config_path: Path):
    """`langchain_ollama` is an optional extra (see the harness `[ollama]` group).

    When it is not installed `ChatOllama` cannot be resolved, so the preset must
    say so — this is the empirical case the availability probe exists for. On a
    machine that does have the package the positive assertion holds instead.
    """
    entry = _providers_by_key(admin)["ollama"]

    assert entry["use"] == "langchain_ollama:ChatOllama"
    if entry["available"]:
        assert entry["reason"] is None
    else:
        assert entry["reason"] and "langchain_ollama" in entry["reason"]


def test_provider_presets_declare_the_field_their_class_actually_accepts(admin: TestClient):
    """每个预设的 `api_base_field` 必须是该 provider 真接受的构造参数。

    这是本表最易错的一点：`ModelConfig` 是 extra="allow"，写错键名不会在保存时
    报错，而是被塞进 model_kwargs，直到第一次真正调用才炸
    （实测：AsyncMessages.create() got an unexpected keyword argument 'api_base'）。
    所以逐个拿 provider 类的 model_fields 核对。
    """
    from deerflow.reflection import resolve_class

    for entry in _providers_by_key(admin).values():
        field = entry["api_base_field"]
        if field is None:
            continue
        # 依赖未安装的 provider 无法取到类；它本来就被标为 available=false，
        # 这里跳过即可（可用性本身由另一个用例覆盖）。
        if not entry["available"]:
            continue
        cls = resolve_class(entry["use"])
        assert field in cls.model_fields, f"provider {entry['key']} declares api_base_field={field!r}, but {entry['use']} has no such field"


def test_anthropic_preset_uses_anthropic_api_url_not_api_base(admin: TestClient):
    """回归：用户给 ChatAnthropic 填 base URL 时踩的坑。

    共享一个 `api_base` 键曾让 Anthropic 条目把该值透传给 SDK 而失败。
    """
    entry = _providers_by_key(admin)["anthropic"]
    assert entry["api_base_field"] == "anthropic_api_url"
    assert entry["api_base_field"] != "api_base"


def test_rejects_a_base_url_key_the_provider_does_not_accept(admin: TestClient, config_path):
    """写错 base URL 键名应在保存时被拒，而不是留到第一次对话。"""
    before = config_path.read_bytes()
    response = admin.post(
        "/api/models",
        json={
            "name": "bad-base-url",
            "use": "langchain_anthropic:ChatAnthropic",
            "model": "claude-x",
            "api_base": "https://open.bigmodel.cn/api/anthropic",
        },
    )
    assert response.status_code == 400, response.text
    assert config_path.read_bytes() == before, "被拒绝的候选不得改动 config.yaml"


def test_accepts_the_correct_base_url_key_for_anthropic(admin: TestClient, config_path):
    """用对了键名就应当保存成功。"""
    response = admin.post(
        "/api/models",
        json={
            "name": "e2e-anthropic-key",
            "use": "langchain_anthropic:ChatAnthropic",
            "model": "claude-x",
            "anthropic_api_url": "https://open.bigmodel.cn/api/anthropic",
        },
    )
    assert response.status_code == 200, response.text
    admin.delete("/api/models/e2e-anthropic-key")


# --------------------------------------------------------------------------- #
# 思考模板与思考探测
# --------------------------------------------------------------------------- #


def test_every_thinking_capable_preset_ships_an_enabled_and_disabled_template(admin: TestClient):
    """勾了「支持思考」就要能写出完整的开/关两个块。

    形态按 provider 分叉（Anthropic `thinking`、OpenAI 兼容 `extra_body.thinking`、
    Google `thinking_budget`、vLLM `chat_template_kwargs`），所以这里不校验形状，
    只校验"两个块都在、且都非空"。
    """
    for entry in _providers_by_key(admin).values():
        if not entry["supports_thinking"]:
            assert entry["thinking_enabled"] is None and entry["thinking_disabled"] is None
            continue
        assert entry["thinking_enabled"], f"{entry['key']} 缺 thinking_enabled"
        assert entry["thinking_disabled"], f"{entry['key']} 缺 thinking_disabled"
        assert entry["thinking_enabled"] != entry["thinking_disabled"]


def test_anthropic_preset_declares_a_budget_and_a_default(admin: TestClient):
    """Anthropic 必须暴露预算输入，且默认值可用（4096）。"""
    entry = _providers_by_key(admin)["anthropic"]
    assert entry["thinking_needs_budget"] is True
    assert entry["default_budget_tokens"] == 4096
    # 预算写在模板里，不是让用户自己拼。
    assert entry["thinking_enabled"]["thinking"]["budget_tokens"] == 4096


def test_openai_compatible_preset_does_not_ask_for_a_budget(admin: TestClient):
    """OpenAI 兼容类用 extra_body.thinking，不需要预算输入。"""
    entry = _providers_by_key(admin)["deepseek"]
    assert entry["thinking_needs_budget"] is False
    assert entry["thinking_enabled"] == {"extra_body": {"thinking": {"type": "enabled"}}}


def test_thinking_probe_requires_admin(user: TestClient):
    response = user.post("/api/models/probe-thinking", json=_MINIMAL_ENTRY)
    assert response.status_code == 403


def test_thinking_probe_writes_nothing(admin: TestClient, config_path: Path, monkeypatch: pytest.MonkeyPatch):
    """探测是只读的：不动 config.yaml，不产生备份，不写 .env。"""
    monkeypatch.setattr(models_router, "_ask_once", _fake_ask(True))
    before = config_path.read_bytes()
    env_path = config_path.parent / ".env"
    env_before = env_path.read_bytes() if env_path.exists() else None

    response = admin.post("/api/models/probe-thinking", json=_MINIMAL_ENTRY)

    assert response.status_code == 200
    assert config_path.read_bytes() == before
    assert list(config_path.parent.glob("config.yaml.bak.*")) == []
    assert (env_path.read_bytes() if env_path.exists() else None) == env_before


def test_thinking_probe_reports_all_three_observations(admin: TestClient, monkeypatch: pytest.MonkeyPatch):
    """三项独立观察都要如实上报。

    `_fake_ask` 依次模拟三次调用：默认思考、开启思考、关闭思考后仍思考。
    最后一项是关键 —— 它对应实测到的兼容端点行为（disabled 不生效）。
    """
    monkeypatch.setattr(models_router, "_ask_once", _fake_ask(True, True, True))

    body = admin.post("/api/models/probe-thinking", json=_MINIMAL_ENTRY).json()

    assert body["ok"] is True
    assert body["thinks_by_default"] is True
    assert body["respects_enabled"] is True
    assert body["respects_disabled"] is False, "关闭后仍思考必须如实报告"
    assert body["error"] is None


def test_thinking_probe_never_sends_more_than_three_calls(admin: TestClient, monkeypatch: pytest.MonkeyPatch):
    """探测最多发 3 次请求 —— 每次都是真实推理调用，操作员在等着。

    回归护栏：这里曾为「关闭不生效」追加 2 次复测（共 5 次），最坏情况下
    单次点击要等 25s 以上。用户明确要求 2-3 次即可。单次取样的代价是
    「关闭无效」可能是误报（该端点偶发泄漏，实测 1/8 ~ 2/8），所以界面上
    这条结论措辞为「观察到的现象」而非判定，点第二次即可复核。
    """
    calls = _fake_ask(True, True, True, True, True)
    monkeypatch.setattr(models_router, "_ask_once", calls)

    body = admin.post("/api/models/probe-thinking", json=_MINIMAL_ENTRY).json()

    assert body["ok"] is True
    assert len(calls.calls) == 3, f"最多 3 次调用，实际 {len(calls.calls)}"
    # 单次取样：这一次说「仍在思考」就直接上报，不再复测。
    assert body["respects_disabled"] is False


def test_thinking_probe_reports_a_well_behaved_endpoint(admin: TestClient, monkeypatch: pytest.MonkeyPatch):
    """正常的端点：默认不思考、开启才思考、关闭就停。"""
    monkeypatch.setattr(models_router, "_ask_once", _fake_ask(False, True, False))

    body = admin.post("/api/models/probe-thinking", json=_MINIMAL_ENTRY).json()

    assert body["ok"] is True
    assert (body["thinks_by_default"], body["respects_enabled"], body["respects_disabled"]) == (False, True, True)


def test_thinking_probe_builds_the_disabled_call_with_thinking_off(admin: TestClient, monkeypatch: pytest.MonkeyPatch):
    """第三次调用必须真的在测「关闭」，不能又测一遍「开启」。

    回归护栏：`create_chat_model` 只在 `thinking_enabled=False` 时才去看
    `when_thinking_disabled`。曾经这里传的是 `supports_thinking: True`，
    于是第三次调用带着 payload 里的「开启」模板再次运行，
    `when_thinking_disabled` 从未生效 —— 实测中它把一条**关闭有效**的端点
    误报成 `respects_disabled: false`，正好是本功能要避免的假阴性。
    """
    seen: list[bool] = []

    def fake_create(name, thinking_enabled=False, **kwargs):
        seen.append(thinking_enabled)
        return object()

    monkeypatch.setattr(models_router.model_factory, "create_chat_model", fake_create)
    monkeypatch.setattr(models_router, "_ask_once", _fake_ask(True, True, True))

    body = admin.post("/api/models/probe-thinking", json=_MINIMAL_ENTRY).json()

    assert body["ok"] is True
    # 前三次依次是：默认、开启、关闭。第三次报「仍在思考」时会追加两次确认
    # （见下），所以断言前缀而不是整体长度。
    assert seen[:3] == [False, True, False], f"三次调用依次应为：不带思考参数、开启思考、关闭思考；实际 thinking_enabled 序列 {seen}"
    assert all(flag is False for flag in seen[3:]), "确认调用同样必须是「关闭」"


def test_thinking_probe_measures_the_default_without_any_thinking_keys(admin: TestClient, monkeypatch: pytest.MonkeyPatch):
    """第一次调用必须剥掉全部思考键，测的是端点的**默认**行为。

    回归护栏：曾经 `build({})` 让 payload 自带的 `when_thinking_disabled`
    一起流下去，于是这次调用实际测的是「关闭思考」，却把结果记作
    `thinks_by_default` —— 实测把一个不传参数就会思考的端点报成了
    `thinks_by_default: false`。
    """
    entries: list[dict] = []

    def fake_create(name, thinking_enabled=False, **kwargs):
        return object()

    async def capture(model):
        return False

    # Capture the entry each call is built from.
    real_build_cfg = models_router._probe_candidate_config

    def spy(payload: dict):
        entries.append({k: payload.get(k) for k in ("supports_thinking", "when_thinking_enabled", "when_thinking_disabled", "thinking")})
        return real_build_cfg(payload)

    monkeypatch.setattr(models_router, "_probe_candidate_config", spy)
    monkeypatch.setattr(models_router.model_factory, "create_chat_model", fake_create)
    monkeypatch.setattr(models_router, "_ask_once", capture)

    body = admin.post(
        "/api/models/probe-thinking",
        json={
            **_MINIMAL_ENTRY,
            "supports_thinking": True,
            "when_thinking_enabled": {"extra_body": {"thinking": {"type": "enabled"}}},
            "when_thinking_disabled": {"extra_body": {"thinking": {"type": "disabled"}}},
        },
    ).json()

    assert body["ok"] is True
    # entries[0] is the initial validation of the posted payload; the probe's
    # own calls start at entries[1].
    first = entries[1]
    assert first == {
        "supports_thinking": None,
        "when_thinking_enabled": None,
        "when_thinking_disabled": None,
        "thinking": None,
    }, f"第一次调用不得携带任何思考键，实际 {first}"


def test_thinking_probe_skips_the_disabled_call_when_it_never_reasons(admin: TestClient, monkeypatch: pytest.MonkeyPatch):
    """从不思考的端点不必再花第三次调用。"""
    calls: list[object] = []

    async def counting(model):
        calls.append(model)
        return False

    monkeypatch.setattr(models_router, "_ask_once", counting)
    body = admin.post("/api/models/probe-thinking", json=_MINIMAL_ENTRY).json()

    assert body["ok"] is True
    assert len(calls) == 2, f"应当只调用两次，实际 {len(calls)}"
    assert body["respects_disabled"] is True  # 没思考 = 关闭是"生效"的


def test_thinking_probe_reports_a_bad_entry_as_a_business_result(admin: TestClient):
    """use 路径不可解析 → ok:false，HTTP 仍 200（业务结果，非服务端故障）。"""
    response = admin.post(
        "/api/models/probe-thinking",
        json={"name": "bad", "use": "nonexistent.module:Nope", "model": "x"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"]
    assert body["thinks_by_default"] is False


def test_thinking_probe_bounds_a_hung_provider(admin: TestClient, monkeypatch: pytest.MonkeyPatch):
    # Pins the thinking probe's OWN budget, not `/models/test`'s — the two are
    # deliberately different constants (one one-token prompt vs. up to three
    # reasoning prompts), and sharing one is the bug this guards.
    monkeypatch.setattr(models_router, "_THINKING_PROBE_TIMEOUT_SECONDS", 0.05)

    async def hangs(model):
        await asyncio.sleep(5)
        return False

    monkeypatch.setattr(models_router, "_ask_once", hangs)
    body = admin.post("/api/models/probe-thinking", json=_MINIMAL_ENTRY).json()

    assert body["ok"] is False
    assert "Timed out" in body["error"]


def test_thinking_probe_budget_fits_a_real_reasoning_endpoint():
    """The budget must fit a real reasoning endpoint, not just a fast one.

    Regression guard: on the shared 15s budget this probe reported "timed out"
    against a live endpoint whose three calls measured 9.0s + 8.1s + 1.9s = 19s
    — a false negative on exactly the behaviour the feature exists to measure.
    A reasoning model spends most of its latency inside the thinking block, so
    the budget has to be sized for reasoning, not for `/models/test`'s one-token
    `ping`.
    """
    assert models_router._THINKING_PROBE_TIMEOUT_SECONDS > models_router._PROBE_TIMEOUT_SECONDS
    # 19s measured end to end on glm-5.3-flash; the budget needs real headroom.
    assert models_router._THINKING_PROBE_TIMEOUT_SECONDS >= 45.0


@pytest.mark.parametrize(
    ("content", "additional", "metadata", "expected"),
    [
        # Anthropic 路径：content 是结构化块列表，含 type=thinking
        ([{"type": "thinking", "thinking": "..."}, {"type": "text", "text": "391"}], {}, {}, True),
        # 无 type 判别键，但有 thinking 键
        ([{"thinking": "..."}], {}, {}, True),
        # 纯文本回答 = 没思考
        ("391", {}, {}, False),
        ([{"type": "text", "text": "391"}], {}, {}, False),
        # OpenAI 兼容路径
        ("391", {"reasoning_content": "..."}, {}, True),
        ("391", {"thinking": "..."}, {}, True),
        # 空串不算
        ("391", {"reasoning_content": "   "}, {}, False),
        # response_metadata 路径
        ("391", {}, {"reasoning_content": "..."}, True),
    ],
)
def test_has_thinking_content_recognises_every_carrier(content, additional, metadata, expected):
    """各 provider 承载思考的位置不同，判据必须全都认。

    判据是"响应里真的出现思考内容"，不是"请求没报错" —— 实测有兼容端点
    不传思考参数也思考、传 disabled 也照样思考，只看有没有异常会误判。
    """

    class Msg:
        pass

    msg = Msg()
    msg.content = content
    msg.additional_kwargs = additional
    msg.response_metadata = metadata
    assert models_router._has_thinking_content(msg) is expected


def test_literal_model_subroutes_are_not_shadowed_by_the_model_name_route(admin: TestClient):
    """回归：`/models/{model_name}` 曾把 `/models/config` 吞掉过（返回 405）。

    FastAPI 按注册顺序匹配，字面量子路径必须排在贪婪参数路径之前。
    GET 与 POST 分别校验 —— 跨方法不会冲突，但同类方法之间会。
    """
    assert admin.get("/api/models/providers").status_code == 200
    assert admin.get("/api/models/config").status_code == 200
    # POST 类：一个无效条目也应返回 400（说明到达了处理器），而不是被吞成 404/405。
    response = admin.post("/api/models/probe-thinking", json={"name": "x"})
    assert response.status_code in (200, 400, 422), f"疑似被 /models/{{model_name}} 路由吞掉: {response.status_code}"


#: 一个最小可用条目，供探测用例复用。
_MINIMAL_ENTRY = {"name": "probe-target", "use": "langchain_openai:ChatOpenAI", "model": "gpt-4o"}


def _fake_ask(*results: bool):
    """返回一个按调用次序吐出 *results* 的 `_ask_once` 替身。

    探测会按需发 2~3 次调用，用序列而不是固定值，
    才能分别模拟「默认思考」「开启思考」「关闭后仍思考」。
    """
    calls: list[int] = []

    async def fake(model):
        index = len(calls)
        calls.append(index)
        return results[index] if index < len(results) else results[-1]

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


def test_probe_and_save_agree_about_a_mismatched_endpoint_key(admin: TestClient, config_path: Path):
    """探测与保存对同一个条目的判断必须一致。

    回归护栏：探测曾经跳过保存路径的「端点键必须是该服务商接受的键」校验，
    于是同一个条目——保存会被 400 拒绝、并明确告知该用 anthropic_api_url——
    探测却照样拿去调用，把 SDK 的原始报错甩给用户。实测在真实界面上编辑
    glm-5.3-flash 点检测，看到的就是
    "AsyncMessages.create() got an unexpected keyword argument 'openai_api_base'"。

    用户视角：同一个条目，一个按钮给你人话，另一个按钮给你 SDK 栈——这不该发生。
    """
    bad_entry = {
        "name": "mismatched",
        "use": "langchain_anthropic:ChatAnthropic",
        "model": "claude-sonnet-5",
        # Anthropic 类不接受 openai_api_base；保存会被拒。
        "openai_api_base": "https://example.test/v1",
        # 用字面量密钥：本用例要走到端点键校验那一步，不该先被 $VAR 解析绊住。
        "api_key": "sk-literal-for-this-test",
    }

    saved = admin.post("/api/models", json=bad_entry)
    assert saved.status_code == 400, "保存应当拒绝错配的端点键"
    save_detail = saved.json()["detail"]
    assert "anthropic_api_url" in save_detail, f"保存应点名正确的键，实际：{save_detail}"

    probed = admin.post("/api/models/probe-thinking", json=bad_entry)
    # 业务结果，不是 500；且给出的理由与保存一致。
    assert probed.status_code == 200
    body = probed.json()
    assert body["ok"] is False
    assert "anthropic_api_url" in (body["error"] or ""), f"探测应当给出与保存同一条可读原因，实际：{body['error']!r}"


def test_probe_carries_a_credential_for_an_edit(admin: TestClient, config_path: Path, monkeypatch: pytest.MonkeyPatch):
    """编辑已有模型点「检测」必须带上凭据，不能空手去连。

    回归护栏：编辑表单只回显 `$VAR` 引用或掩码，从不回显明文，于是探测载荷
    里没有可用密钥 —— 实测在真实界面上编辑 glm-5.3-flash 点检测，直接报
    "Could not resolve authentication method"。保存路径早就有「掩码/空 =
    沿用已存密钥」的规则（`_to_stored_entry`），探测是同一份保存的只读预演，
    必须遵守同一条规则。

    断言的是**契约**（送到模型构造器里的 api_key 是什么），不是某一层的实现
    细节：`$VAR` → 明文这一步由 `AppConfig.resolve_env_variables` 完成，所以
    这里只比较最终等价的值。
    """
    admin.post(
        "/api/models",
        json={
            "name": "edit-me",
            "use": "langchain_openai:ChatOpenAI",
            "model": "gpt-4o",
            "api_key": "$EDIT_ME_KEY",
            "api_key_value": "sk-stored-secret",
        },
    )
    stored_ref = next(m["api_key"] for m in admin.get("/api/models/config").json()["models"] if m["name"] == "edit-me")
    assert stored_ref == "$EDIT_ME_KEY"

    # Capture the credential the provider would truly receive.
    built: list[object] = []
    real_create = models_router.model_factory.create_chat_model

    def spy_create(name, **kwargs):
        model = real_create(name, **kwargs)
        built.append(model)
        return model

    monkeypatch.setattr(models_router.model_factory, "create_chat_model", spy_create)
    # Never actually call a provider.
    monkeypatch.setattr(models_router, "_ask_once", _fake_ask(False))

    def sent_key(model) -> str:
        # ChatOpenAI keeps it in `openai_api_key` (a SecretStr); it has no plain
        # `api_key` attribute.
        secret = model.openai_api_key
        return secret.get_secret_value() if secret else ""

    # 1. The UI's edit payload: the form shows only the mask, plus the stored
    #    reference under the private key.
    body = admin.post(
        "/api/models/probe-thinking",
        json={
            "name": "edit-me",
            "use": "langchain_openai:ChatOpenAI",
            "model": "gpt-4o",
            "api_key": "sk-****cret",
            "_stored_api_key": stored_ref,
        },
    ).json()
    assert body["ok"] is True
    assert built, "探测应当真的构建出模型"
    assert sent_key(built[0]) == os.environ.get("EDIT_ME_KEY"), f"provider 拿到的应是已存密钥，实际 {sent_key(built[0])!r}"
    assert "****" not in sent_key(built[0]), "掩码不是凭据，绝不能被当作密钥使用"

    # 2. No reference sent, but the entry exists on disk → fall back to it.
    built.clear()
    admin.post(
        "/api/models/probe-thinking",
        json={
            "name": "edit-me",
            "use": "langchain_openai:ChatOpenAI",
            "model": "gpt-4o",
            "api_key": "sk-****cret",
        },
    )
    assert sent_key(built[0]) == os.environ.get("EDIT_ME_KEY")

    # 3. Nothing stored and no reference → no credential at all. The probe then
    #    reports "could not resolve authentication method", which is the honest
    #    answer for an entry that genuinely has no key.
    built.clear()
    admin.post(
        "/api/models/probe-thinking",
        json={
            "name": "never-stored",
            "use": "langchain_openai:ChatOpenAI",
            "model": "gpt-4o",
            "api_key": "sk-****cret",
        },
    )
    assert "****" not in sent_key(built[0]), "掩码不得被当成密钥"


def test_probe_candidate_config_replaces_an_entry_of_the_same_name(config_path: Path, monkeypatch: pytest.MonkeyPatch):
    """候选条目必须**替换**同名条目，不能追加在它后面。

    回归护栏：这里曾经是 append。`create_chat_model(name)` 走
    `get_model_config(name)`，取的是**第一个**同名条目 —— 于是只要候选名与
    已存模型重名（编辑已有模型并点「检测」，正是最常见的用法），查到的就是
    **磁盘上那份旧配置**，探测把旧行为当成了新配置的结论。

    实测（真实端点，glm-5.3-flash）：候选明确发送
    `thinking: {type: disabled}` 且实测不返回思考块，仍被报成
    `respects_disabled: false` —— 因为被调用的其实是已存条目。
    """
    from deerflow.config.model_config import ModelConfig
    from deerflow.models.factory import create_chat_model

    name = "collision-model"
    # 磁盘上先有一条同名条目：无思考配置，模型是 gpt-4o。
    stored = ModelConfig.model_validate(
        {
            "name": name,
            "use": "langchain_openai:ChatOpenAI",
            "model": "gpt-4o",
            "api_key": "sk-stored-not-real",
        }
    )
    # Compose a valid base config (the example carries the sandbox section)
    # that already holds the colliding entry.
    live = get_app_config().model_dump()
    base = AppConfig.model_validate(
        {**live, "models": [stored.model_dump()]},
    )
    assert base.get_model_config(name).model == "gpt-4o"

    monkeypatch.setattr(models_router, "get_app_config", lambda: base)

    # 候选同名，但换成了 Anthropic + 关闭思考的配置。
    cfg, resolved = models_router._probe_candidate_config(
        {
            "name": name,
            "use": "langchain_anthropic:ChatAnthropic",
            "model": "claude-sonnet-5",
            "api_key": "sk-candidate-not-real",
            "supports_thinking": True,
            "when_thinking_disabled": {"thinking": {"type": "disabled"}},
        }
    )

    assert resolved == name
    matched = [m for m in cfg.model_dump()["models"] if m["name"] == name]
    assert len(matched) == 1, f"同名条目应只剩一条，实际 {len(matched)} 条"

    # 解析出来的必须是候选，而不是磁盘上那条 —— 这正是本函数存在的意义。
    model = create_chat_model(name, thinking_enabled=False, app_config=cfg, attach_tracing=False)
    assert model.model == "claude-sonnet-5"
    assert getattr(model, "thinking", None) == {"type": "disabled"}
