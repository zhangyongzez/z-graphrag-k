"""SQLite 数据层：用户、会话、知识库归属、运行时配置。

用标准库 sqlite3，不引入 ORM。所有写操作走同一个连接 + 事务，
配合 WAL 模式支持多线程读。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kgrag.config import get_settings
from kgrag.logging import get_logger

logger = get_logger(__name__)

_local = threading.local()
_write_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL DEFAULT '',
    password_hash TEXT,                      -- AD 用户为 NULL
    role          TEXT NOT NULL DEFAULT 'user',  -- admin | user
    source        TEXT NOT NULL DEFAULT 'local', -- local | ad
    email         TEXT DEFAULT '',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- 知识库归属：owner_id 为空表示「管理员创建、未分配」
CREATE TABLE IF NOT EXISTS kb_owner (
    kb_id       TEXT PRIMARY KEY,
    owner_id    TEXT,
    is_public   INTEGER NOT NULL DEFAULT 0,  -- 管理员设为公共，所有人可见
    created_at  TEXT NOT NULL
);

-- 用户与知识库的授权关系（管理员分配）
CREATE TABLE IF NOT EXISTS kb_grant (
    kb_id       TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    can_write   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (kb_id, user_id)
);

-- 运行时配置（管理员在配置页改的，覆盖 .env）
CREATE TABLE IF NOT EXISTS app_config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  TEXT
);

-- 用户自定义对话
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '新对话',
    llm_model   TEXT DEFAULT '',
    kb_weights  TEXT NOT NULL DEFAULT '[]',  -- [{"kb_id": "...", "weight": 1.0}]
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,   -- user | assistant
    content         TEXT NOT NULL,
    sources         TEXT DEFAULT '[]',
    reasoning       TEXT DEFAULT '',  -- 模型的推理过程（thinking 模型才有）
    created_at      TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);

-- 统一模型注册表：LLM 与 Embedding 都在这里维护
-- 配置页只引用模型 id，不直接维护连接信息
CREATE TABLE IF NOT EXISTS models (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,              -- llm | embedding
    provider    TEXT NOT NULL,              -- anthropic | openai | ollama | local | hash | mock
    model       TEXT NOT NULL DEFAULT '',   -- 模型标识，如 gpt-4o / claude-sonnet-4-5
    base_url    TEXT NOT NULL DEFAULT '',   -- API 地址（本地模型为空）
    api_key     TEXT NOT NULL DEFAULT '',   -- 密钥（本地模型为空）
    path        TEXT NOT NULL DEFAULT '',   -- 本地模型目录（远程模型为空）
    dim         INTEGER,                    -- embedding 维度
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_models_kind ON models(kind);

-- 旧版本地表（保留用于迁移，迁移后可忽略）
CREATE TABLE IF NOT EXISTS local_models (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    path        TEXT NOT NULL UNIQUE,
    dim         INTEGER,
    size_bytes  INTEGER DEFAULT 0,
    note        TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);

-- 操作审计
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id    TEXT,
    actor_name  TEXT,
    action      TEXT NOT NULL,
    target      TEXT DEFAULT '',
    detail      TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def db_path() -> Path:
    s = get_settings()
    p = Path(s.data_dir) / "app.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _conn() -> sqlite3.Connection:
    """每个线程一个连接，建连时确保 schema 存在。

    这样即使调用方（CLI、测试、工厂）没有显式调用 :func:`init_db`，
    读运行时配置也不会因为缺表而炸掉。
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(
            str(db_path()),
            check_same_thread=False,
            timeout=15.0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
        _local.conn = conn
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = _conn()
    with _write_lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def init_db() -> None:
    conn = _conn()
    with _write_lock:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    logger.info(f"数据库就绪: {db_path()}")


def _migrate(conn: sqlite3.Connection) -> None:
    """为已存在的库补齐新增列（SQLite 的 ADD COLUMN 是幂等的）。"""
    migrations = [
        ("messages", "reasoning", "TEXT DEFAULT ''"),
    ]
    for table, column, ddl in migrations:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            logger.info(f"数据库迁移: {table}.{column} 已添加")


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return _conn().execute(sql, params).fetchall()


def query_one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return _conn().execute(sql, params).fetchone()


def execute(sql: str, params: tuple = ()) -> None:
    with transaction() as conn:
        conn.execute(sql, params)


# --------------------------------------------------------------------------- #
# 运行时配置
# --------------------------------------------------------------------------- #
CONFIG_KEYS = {
    # 选中的模型（模型本身在 models 表维护）
    "llm_model_id",
    "embedding_model_id",
    # 向量库
    "vectorstore_provider",
    "milvus_uri",
    "milvus_token",
    "milvus_db_name",
    "vectorstore_lite_path",
    # LLM 运行时参数
    "llm_temperature",
    "llm_max_tokens",
    "llm_timeout",
    # Embedding 运行时参数
    "embedding_device",
    "embedding_batch_size",
    "embedding_max_length",
    "embedding_normalize",
    # 切分 / 检索
    "retrieval_top_k",
    "retrieval_score_threshold",
    "chunk_size",
    "chunk_overlap",
    # AD 域
    "ad_enabled",
    "ad_server_uri",
    "ad_use_ssl",
    "ad_base_dn",
    "ad_user_filter",
    "ad_bind_dn",
    "ad_bind_password",
    "ad_display_name_attr",
    "ad_email_attr",
}

# 只在服务端环境变量里生效、不允许配置页修改的项
ENV_ONLY_KEYS = {"app_host", "app_port", "data_dir", "upload_dir", "app_env"}


def load_runtime_config() -> dict[str, Any]:
    rows = query("SELECT key, value FROM app_config")
    out: dict[str, Any] = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except json.JSONDecodeError:
            out[r["key"]] = r["value"]
    return out


def save_runtime_config(values: dict[str, Any], updated_by: str = "") -> list[str]:
    """保存配置，返回变更的 key 列表。"""
    changed: list[str] = []
    current = load_runtime_config()
    with transaction() as conn:
        for k, v in values.items():
            if k not in CONFIG_KEYS:
                continue
            if current.get(k) == v:
                continue
            conn.execute(
                """
                INSERT INTO app_config(key, value, updated_at, updated_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at,
                    updated_by=excluded.updated_by
                """,
                (k, json.dumps(v, ensure_ascii=False), _now(), updated_by),
            )
            changed.append(k)
    return changed
