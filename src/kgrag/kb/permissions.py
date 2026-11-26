"""知识库权限：归属、授权、可见性判定。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from kgrag.auth.service import User
from kgrag.db import execute, query, query_one
from kgrag.logging import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class KBPermission:
    kb_id: str
    owner_id: str | None
    is_public: bool
    can_write: bool
    is_owner: bool
    is_granted: bool = False

    @property
    def can_read(self) -> bool:
        return self.is_owner or self.is_public or self.is_granted or self.can_write


def set_owner(kb_id: str, owner_id: str | None, *, is_public: bool = False) -> None:
    execute(
        """INSERT INTO kb_owner(kb_id, owner_id, is_public, created_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(kb_id) DO UPDATE SET owner_id=excluded.owner_id,
                                            is_public=excluded.is_public""",
        (kb_id, owner_id, int(is_public), _now()),
    )


def get_owner(kb_id: str):
    return query_one("SELECT * FROM kb_owner WHERE kb_id = ?", (kb_id,))


def grant(kb_id: str, user_id: str, *, can_write: bool = False) -> None:
    execute(
        """INSERT INTO kb_grant(kb_id, user_id, can_write, created_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(kb_id, user_id) DO UPDATE SET can_write=excluded.can_write""",
        (kb_id, user_id, int(can_write), _now()),
    )


def revoke(kb_id: str, user_id: str) -> None:
    execute("DELETE FROM kb_grant WHERE kb_id = ? AND user_id = ?", (kb_id, user_id))


def list_grants(kb_id: str) -> list[dict]:
    rows = query(
        """SELECT g.user_id, g.can_write, u.username, u.display_name
           FROM kb_grant g JOIN users u ON u.id = g.user_id
           WHERE g.kb_id = ?""",
        (kb_id,),
    )
    return [dict(r) for r in rows]


def delete_kb_permissions(kb_id: str) -> None:
    execute("DELETE FROM kb_owner WHERE kb_id = ?", (kb_id,))
    execute("DELETE FROM kb_grant WHERE kb_id = ?", (kb_id,))


def permission_of(kb_id: str, user: User) -> KBPermission:
    if user.is_admin:
        return KBPermission(kb_id, None, True, True, True)

    owner_row = query_one("SELECT * FROM kb_owner WHERE kb_id = ?", (kb_id,))
    grant_row = query_one(
        "SELECT * FROM kb_grant WHERE kb_id = ? AND user_id = ?", (kb_id, user.id)
    )

    owner_id = owner_row["owner_id"] if owner_row else None
    is_public = bool(owner_row["is_public"]) if owner_row else False
    is_owner = owner_id == user.id
    can_write = bool(grant_row["can_write"]) if grant_row else False

    return KBPermission(
        kb_id=kb_id,
        owner_id=owner_id,
        is_public=is_public,
        can_write=can_write,
        is_owner=is_owner,
        is_granted=grant_row is not None,
    )


def can_read(kb_id: str, user: User) -> bool:
    return permission_of(kb_id, user).can_read


def can_write(kb_id: str, user: User) -> bool:
    p = permission_of(kb_id, user)
    return p.is_owner or p.can_write


def visible_kb_ids(user: User) -> set[str] | None:
    """返回用户可见的知识库 ID 集合；``None`` 表示全部可见（管理员）。"""
    if user.is_admin:
        return None
    owned = {r["kb_id"] for r in query("SELECT kb_id FROM kb_owner WHERE owner_id = ?", (user.id,))}
    public = {r["kb_id"] for r in query("SELECT kb_id FROM kb_owner WHERE is_public = 1")}
    granted = {r["kb_id"] for r in query("SELECT kb_id FROM kb_grant WHERE user_id = ?", (user.id,))}
    return owned | public | granted


def new_kb_id() -> str:
    return uuid.uuid4().hex[:12]
