"""操作审计：记录谁在什么时候做了什么。

写失败不影响主流程——审计是旁路能力。
"""

from __future__ import annotations

from datetime import UTC, datetime

from kgrag.db import execute, query
from kgrag.logging import get_logger

logger = get_logger(__name__)


def record(
    actor_id: str | None,
    actor_name: str | None,
    action: str,
    target: str = "",
    detail: str = "",
) -> None:
    try:
        execute(
            """INSERT INTO audit_log(actor_id, actor_name, action, target, detail, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                actor_id or "",
                actor_name or "",
                action,
                target,
                detail,
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
    except Exception as e:  # noqa: BLE001 - 审计不能影响主流程
        logger.warning(f"写审计日志失败: {e}")


def recent(limit: int = 100) -> list[dict]:
    rows = query(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]
