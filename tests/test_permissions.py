"""权限、配置、模型管理、会话的接口测试。"""

import pytest
from fastapi.testclient import TestClient

from kgrag.api.app import app
from kgrag.auth.service import User, _issue_session
from kgrag.db import query_one


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _login_as(client, username: str) -> str:
    """用已有用户建会话，返回 token。"""
    row = query_one("SELECT * FROM users WHERE username = ?", (username,))
    assert row is not None, f"用户不存在: {username}"
    return _issue_session(User.from_row(row))


@pytest.fixture(scope="module")
def admin_client(client):
    from kgrag.auth.service import ensure_admin

    ensure_admin()
    row = query_one("SELECT * FROM users WHERE role = 'admin'")
    token = _issue_session(User.from_row(row))
    client.cookies.set("kgrag_session", token)
    return client


@pytest.fixture(scope="module")
def alice(client, admin_client):
    """普通用户 alice。"""
    import contextlib

    from kgrag.auth.service import create_user

    with contextlib.suppress(Exception):
        create_user("alice", "alice123")
    return "alice"


def _as(client, username: str):
    """切换当前会话到指定用户。"""
    client.cookies.set("kgrag_session", _login_as(client, username))
    return client


# --------------------------------------------------------------------------- #
# 权限
# --------------------------------------------------------------------------- #
def test_user_cannot_see_others_kb(admin_client, alice):
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    admin_kb = admin_client.post("/api/kb", json={"name": "管理员私有库"}).json()

    c = _as(admin_client, alice)
    ids = [k["id"] for k in c.get("/api/kb").json()]
    assert admin_kb["id"] not in ids


def test_user_cannot_read_others_kb(admin_client, alice):
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    admin_kb = admin_client.post("/api/kb", json={"name": "私有库2"}).json()

    c = _as(admin_client, alice)
    assert c.get(f"/api/kb/{admin_kb['id']}").status_code == 403


def test_public_kb_visible_to_all(admin_client, alice):
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    kb = admin_client.post("/api/kb", json={"name": "公共库"}).json()
    admin_client.put(f"/api/kb/{kb['id']}/public", json={"is_public": True})

    c = _as(admin_client, alice)
    ids = [k["id"] for k in c.get("/api/kb").json()]
    assert kb["id"] in ids


def test_grant_read_access(admin_client, alice):
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    kb = admin_client.post("/api/kb", json={"name": "授权库"}).json()
    alice_row = query_one("SELECT id FROM users WHERE username = 'alice'")
    admin_client.post(
        f"/api/kb/{kb['id']}/permissions",
        json={"user_id": alice_row["id"], "can_write": False},
    )

    c = _as(admin_client, alice)
    assert c.get(f"/api/kb/{kb['id']}").status_code == 200
    # 只读授权不能写
    assert c.patch(f"/api/kb/{kb['id']}", json={"name": "x"}).status_code == 403


def test_owner_can_delete_own_kb(admin_client, alice):
    c = _as(admin_client, alice)
    kb = c.post("/api/kb", json={"name": "alice 的库"}).json()
    assert kb["is_owner"] is True
    assert c.delete(f"/api/kb/{kb['id']}").status_code == 204


# --------------------------------------------------------------------------- #
# 管理员接口
# --------------------------------------------------------------------------- #
def test_non_admin_cannot_access_config(admin_client, alice):
    c = _as(admin_client, alice)
    assert c.get("/api/admin/config").status_code == 403


def test_config_roundtrip(admin_client):
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    r = admin_client.put("/api/admin/config", json={"values": {"retrieval_top_k": 7}})
    assert r.status_code == 200
    assert "retrieval_top_k" in r.json()["changed"]

    cfg = admin_client.get("/api/admin/config").json()
    assert cfg["current"]["retrieval_top_k"] == 7
    # 恢复
    admin_client.put("/api/admin/config", json={"values": {"retrieval_top_k": 5}})


def test_config_masks_secret(admin_client):
    """密钥类配置返回时脱敏。"""
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    admin_client.put(
        "/api/admin/config", json={"values": {"milvus_token": "sk-secret-1234567890"}}
    )
    cfg = admin_client.get("/api/admin/config").json()
    assert cfg["current"]["milvus_token"]["is_set"] is True
    assert "secret" not in cfg["current"]["milvus_token"]["value"]
    admin_client.put("/api/admin/config", json={"values": {"milvus_token": ""}})


def test_config_has_defaults(admin_client):
    """配置页应提供一份完整默认配置。"""
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    cfg = admin_client.get("/api/admin/config").json()
    assert cfg["defaults"]
    assert cfg["defaults"]["retrieval_top_k"] == 5
    assert "llm_model_id" in cfg["defaults"]


def test_config_reset(admin_client):
    """恢复默认配置。"""
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    admin_client.put("/api/admin/config", json={"values": {"retrieval_top_k": 9}})
    assert admin_client.get("/api/admin/config").json()["current"]["retrieval_top_k"] == 9

    r = admin_client.post("/api/admin/config/reset")
    assert r.status_code == 200
    assert admin_client.get("/api/admin/config").json()["current"]["retrieval_top_k"] == 5


def test_dashboard_returns_services(admin_client):
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    d = admin_client.get("/api/admin/dashboard").json()
    assert d["totals"]["kb_count"] >= 1
    assert any(s["name"] == "API 服务" for s in d["services"])
    assert "embedding" in d["models"]


def test_register_model_invalid_path(admin_client, tmp_path):
    """本地模型目录不合法时应拒绝。"""
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    empty = tmp_path / "empty"
    empty.mkdir()
    r = admin_client.post(
        "/api/admin/models",
        json={
            "name": "bad-local",
            "kind": "embedding",
            "provider": "local",
            "path": str(empty),
        },
    )
    assert r.status_code == 400


def test_register_local_embedding_model(admin_client, tmp_path):
    """登记本地 Embedding 模型，自动读取维度。"""
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    m = tmp_path / "bge-m3"
    m.mkdir()
    (m / "config.json").write_text('{"hidden_size": 1024}')
    (m / "model.safetensors").write_bytes(b"x" * 100)

    r = admin_client.post(
        "/api/admin/models",
        json={
            "name": "bge-m3",
            "kind": "embedding",
            "provider": "local",
            "path": str(m),
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["dim"] == 1024
    assert data["kind"] == "embedding"
    assert data["provider"] == "local"
    # 密钥字段永不返回
    assert "api_key" not in data


def test_register_llm_model(admin_client):
    """登记 LLM 模型（OpenAI 兼容）。"""
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    r = admin_client.post(
        "/api/admin/models",
        json={
            "name": "deepseek-chat",
            "kind": "llm",
            "provider": "openai",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "sk-test-123",
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["kind"] == "llm"
    assert data["has_api_key"] is True
    assert "api_key" not in data


def test_new_model_appears_in_config_dropdown(admin_client):
    """需求：新增模型后配置页下拉框出现该选项。"""
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    cfg = admin_client.get("/api/admin/config").json()
    llm_group = next(g for g in cfg["groups"] if g["key"] == "llm")
    field = next(f for f in llm_group["fields"] if f["key"] == "llm_model_id")
    names = [o["name"] for o in field["options"]]
    assert "deepseek-chat" in names


def test_models_filtered_by_kind(admin_client):
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    llms = admin_client.get("/api/admin/models", params={"kind": "llm"}).json()
    assert llms and all(m["kind"] == "llm" for m in llms)
    embs = admin_client.get("/api/admin/models", params={"kind": "embedding"}).json()
    assert embs and all(m["kind"] == "embedding" for m in embs)


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #
def test_conversation_crud(admin_client):
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    c = admin_client.post("/api/chat/conversations", json={"title": "测试会话"})
    assert c.status_code == 201
    cid = c.json()["id"]

    r = admin_client.patch(
        f"/api/chat/conversations/{cid}",
        json={"kb_weights": [{"kb_id": "x", "weight": 1.5}]},
    )
    assert r.status_code == 200
    assert r.json()["kb_weights"][0]["weight"] == 1.5

    assert admin_client.delete(f"/api/chat/conversations/{cid}").status_code == 204
    assert admin_client.get(f"/api/chat/conversations/{cid}").status_code == 404


def test_conversation_is_private(admin_client, alice):
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    cid = admin_client.post("/api/chat/conversations", json={"title": "admin 的"}).json()["id"]

    c = _as(admin_client, alice)
    assert c.get(f"/api/chat/conversations/{cid}").status_code == 403


# --------------------------------------------------------------------------- #
# 管理员密码同步
# --------------------------------------------------------------------------- #
def test_admin_password_syncs_from_env(client, monkeypatch):
    """改了 ADMIN_PASSWORD 后重启，密码应同步生效。"""
    import os

    from kgrag.auth.security import verify_password
    from kgrag.auth.service import ensure_admin, get_user_by_token
    from kgrag.db import query_one

    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "brand-new-password")
    ensure_admin()

    row = query_one("SELECT * FROM users WHERE role = 'admin'")
    assert verify_password("brand-new-password", row["password_hash"])
    assert get_user_by_token is not None
    assert os.getenv("ADMIN_PASSWORD") == "brand-new-password"


def test_admin_password_not_changed_when_env_empty(client, monkeypatch):
    """环境变量未设密码时，不应清空已有密码。"""
    from kgrag.auth.service import ensure_admin
    from kgrag.db import query_one

    before = query_one("SELECT password_hash FROM users WHERE role = 'admin'")["password_hash"]

    monkeypatch.setenv("ADMIN_PASSWORD", "")
    ensure_admin()

    after = query_one("SELECT password_hash FROM users WHERE role = 'admin'")["password_hash"]
    assert after == before


# --------------------------------------------------------------------------- #
# 未配置 LLM 时的纯检索降级
# --------------------------------------------------------------------------- #
def test_chat_retrieval_only_without_llm(admin_client, tmp_path):
    """未配置 LLM 时，对话应降级为返回检索片段，而不是报错。"""
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))

    # 确保没有选中 LLM
    admin_client.put("/api/admin/config", json={"values": {"llm_model_id": ""}})

    kb = admin_client.post("/api/kb", json={"name": "检索降级测试库"}).json()
    f = tmp_path / "doc.md"
    f.write_text("# 退货政策\n\n七天无理由退货。\n", encoding="utf-8")
    with open(f, "rb") as fh:
        admin_client.post(
            f"/api/kb/{kb['id']}/upload",
            files=[("files", ("doc.md", fh, "text/markdown"))],
        )

    r = admin_client.post("/api/chat", json={"question": "退货", "kb_id": kb["id"]})
    assert r.status_code == 200
    data = r.json()
    assert data["retrieval_only"] is True
    assert data["sources"]
    assert "未配置大模型" in data["answer"]
    assert "七天无理由退货" in data["answer"]


def test_chat_uses_llm_when_configured(admin_client):
    """配置了 LLM 后，retrieval_only 应为 False。"""
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    m = admin_client.post(
        "/api/admin/models",
        json={
            "name": "mock-llm-for-test",
            "kind": "llm",
            "provider": "mock",
        },
    ).json()
    admin_client.put("/api/admin/config", json={"values": {"llm_model_id": m["id"]}})

    r = admin_client.post("/api/chat", json={"question": "测试"})
    assert r.status_code == 200
    assert r.json()["retrieval_only"] is False

    # 清理，避免影响后续测试
    admin_client.put("/api/admin/config", json={"values": {"llm_model_id": ""}})


# --------------------------------------------------------------------------- #
# 模型名与提供方匹配检查
# --------------------------------------------------------------------------- #
def test_provider_mismatch_warning(admin_client):
    """用 Anthropic 协议配 DeepSeek 模型名时应给出警告。"""
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    r = admin_client.post(
        "/api/admin/models",
        json={
            "name": "mismatch-test",
            "kind": "llm",
            "provider": "anthropic",
            "model": "deepseek-chat",
            "base_url": "http://example.com",
        },
    )
    assert r.status_code == 201
    assert "warning" in r.json()
    assert "openai" in r.json()["warning"]


def test_no_warning_when_provider_matches(admin_client):
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    r = admin_client.post(
        "/api/admin/models",
        json={
            "name": "match-test",
            "kind": "llm",
            "provider": "openai",
            "model": "deepseek-chat",
            "base_url": "http://example.com",
        },
    )
    assert r.status_code == 201
    assert "warning" not in r.json()


def test_dashboard_reflects_selected_model(admin_client):
    """看板应显示选中的模型，而不是 .env 默认值。"""
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    m = admin_client.post(
        "/api/admin/models",
        json={"name": "dash-model", "kind": "llm", "provider": "mock"},
    ).json()
    admin_client.put("/api/admin/config", json={"values": {"llm_model_id": m["id"]}})

    stats = admin_client.get("/api/kb/stats/dashboard").json()
    assert stats["llm_provider"] == "mock"

    admin_client.put("/api/admin/config", json={"values": {"llm_model_id": ""}})


def test_api_key_must_not_be_url(admin_client):
    """API Key 填成网址时应拒绝保存。"""
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    r = admin_client.post(
        "/api/admin/models",
        json={
            "name": "bad-key-test",
            "kind": "llm",
            "provider": "openai",
            "model": "gpt-4o",
            "base_url": "https://api.example.com/v1",
            "api_key": "http://aihub-api.example.com:30080",
        },
    )
    assert r.status_code == 400
    assert "网址" in r.json()["detail"]


def test_api_key_must_not_equal_base_url(admin_client):
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    url = "https://api.example.com/v1"
    r = admin_client.post(
        "/api/admin/models",
        json={
            "name": "same-key-test",
            "kind": "llm",
            "provider": "openai",
            "model": "gpt-4o",
            "base_url": url,
            "api_key": url,
        },
    )
    assert r.status_code == 400


def test_api_key_with_space_rejected(admin_client):
    admin_client.cookies.set("kgrag_session", _login_as(admin_client, "admin"))
    r = admin_client.post(
        "/api/admin/models",
        json={
            "name": "space-key-test",
            "kind": "llm",
            "provider": "openai",
            "model": "gpt-4o",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-abc def",
        },
    )
    assert r.status_code == 400
