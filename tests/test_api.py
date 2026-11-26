import pytest
from fastapi.testclient import TestClient

from kgrag.api.app import app


@pytest.fixture(scope="module")
def client():
    # 用上下文管理器触发 lifespan（初始化数据库 + 管理员）
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth_client(client):
    """已登录的管理员客户端。"""
    from kgrag.auth.service import ensure_admin

    ensure_admin()
    # 管理员密码来自环境变量；测试里直接建会话
    from kgrag.db import query_one

    row = query_one("SELECT * FROM users WHERE role = 'admin'")
    assert row is not None
    from kgrag.auth.service import User, _issue_session

    token = _issue_session(User.from_row(row))
    client.cookies.set("kgrag_session", token)
    return client


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["embedding"] == "hash"


def test_config_masks_secrets(client):
    r = client.get("/config")
    assert r.status_code == 200
    data = r.json()
    for k, v in data.items():
        if "key" in k or "password" in k or "token" in k:
            assert v in ("", "***")


def test_requires_login(client):
    assert client.get("/api/kb").status_code == 401


def test_ingest_missing_path(auth_client):
    r = auth_client.post("/ingest", json={"path": "/definitely/not/here"})
    assert r.status_code == 404


def test_ingest_and_search(auth_client, sample_md):
    r = auth_client.post("/ingest", json={"path": str(sample_md)})
    assert r.status_code == 200
    assert r.json()["chunks"] > 0

    r = auth_client.post("/search", json={"query": "安装", "top_k": 2})
    assert r.status_code == 200
    assert "results" in r.json()


def test_ask(auth_client, sample_md):
    auth_client.post("/ingest", json={"path": str(sample_md)})
    r = auth_client.post("/ask", json={"question": "怎么安装？", "top_k": 2})
    assert r.status_code == 200
    data = r.json()
    assert data["answer"]
    assert isinstance(data["sources"], list)


def test_list_files(auth_client, tmp_path):
    (tmp_path / "x.txt").write_text("x")
    r = auth_client.get("/files", params={"root": str(tmp_path)})
    assert r.status_code == 200
    assert any("x.txt" in f for f in r.json()["files"])
