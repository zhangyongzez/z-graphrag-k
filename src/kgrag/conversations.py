"""会话管理：用户可新建对话、选择知识库权重、指定大模型。

一个会话绑定：
- ``kb_weights``：``[{"kb_id": "...", "weight": 1.0}]``，权重用于多库融合检索
- ``llm_model``：本会话使用的模型；空表示跟随全局配置
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from kgrag.db import execute, query, query_one
from kgrag.logging import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_conv(row) -> dict:
    d = dict(row)
    try:
        d["kb_weights"] = json.loads(d.get("kb_weights") or "[]")
    except json.JSONDecodeError:
        d["kb_weights"] = []
    return d


def create_conversation(
    user_id: str,
    *,
    title: str = "新对话",
    kb_weights: list[dict] | None = None,
    llm_model: str = "",
) -> dict:
    cid = uuid.uuid4().hex
    now = _now()
    execute(
        """INSERT INTO conversations(id, user_id, title, llm_model, kb_weights,
                                     created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            cid,
            user_id,
            title,
            llm_model,
            json.dumps(kb_weights or [], ensure_ascii=False),
            now,
            now,
        ),
    )
    return get_conversation(cid)  # type: ignore[return-value]


def list_conversations(user_id: str) -> list[dict]:
    rows = query(
        "SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,),
    )
    return [_row_to_conv(r) for r in rows]


def get_conversation(conv_id: str) -> dict | None:
    row = query_one("SELECT * FROM conversations WHERE id = ?", (conv_id,))
    return _row_to_conv(row) if row else None


def update_conversation(
    conv_id: str,
    *,
    title: str | None = None,
    kb_weights: list[dict] | None = None,
    llm_model: str | None = None,
) -> None:
    sets, params = [], []
    if title is not None:
        sets.append("title = ?")
        params.append(title)
    if kb_weights is not None:
        sets.append("kb_weights = ?")
        params.append(json.dumps(kb_weights, ensure_ascii=False))
    if llm_model is not None:
        sets.append("llm_model = ?")
        params.append(llm_model)
    if not sets:
        return
    sets.append("updated_at = ?")
    params.append(_now())
    params.append(conv_id)
    execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id = ?", tuple(params))


def delete_conversation(conv_id: str) -> None:
    execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
    execute("DELETE FROM conversations WHERE id = ?", (conv_id,))


def add_message(
    conv_id: str,
    role: str,
    content: str,
    sources: list[dict] | None = None,
    reasoning: str = "",
) -> str:
    mid = uuid.uuid4().hex
    execute(
        """INSERT INTO messages(id, conversation_id, role, content, sources,
                               reasoning, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            mid,
            conv_id,
            role,
            content,
            json.dumps(sources or [], ensure_ascii=False),
            reasoning,
            _now(),
        ),
    )
    execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), conv_id))
    return mid


def list_messages(conv_id: str) -> list[dict]:
    rows = query(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY seq",
        (conv_id,),
    )
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["sources"] = json.loads(d.get("sources") or "[]")
        except json.JSONDecodeError:
            d["sources"] = []
        out.append(d)
    return out


def history_pairs(conv_id: str, limit: int = 10) -> list[tuple[str, str]]:
    """最近 N 轮对话，转成 ``(role, content)`` 供 LLM 使用。"""
    msgs = list_messages(conv_id)[-limit * 2 :]
    pairs = []
    for m in msgs:
        role = "human" if m["role"] == "user" else "ai"
        pairs.append((role, m["content"]))
    return pairs
