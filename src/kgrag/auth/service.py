"""用户与会话服务。"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from kgrag.auth.ad import ADConfig, ADError
from kgrag.auth.ad import authenticate as ad_authenticate
from kgrag.auth.security import hash_password, new_session_token, verify_password
from kgrag.db import execute, query, query_one
from kgrag.logging import get_logger

logger = get_logger(__name__)

SESSION_DAYS = 7


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


@dataclass
class User:
    id: str
    username: str
    display_name: str
    role: str
    source: str
    email: str
    is_active: bool
    created_at: str
    last_login_at: str | None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @classmethod
    def from_row(cls, row) -> User:
        return cls(
            id=row["id"],
            username=row["username"],
            display_name=row["display_name"],
            role=row["role"],
            source=row["source"],
            email=row["email"] or "",
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            last_login_at=row["last_login_at"],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "source": self.source,
            "email": self.email,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "last_login_at": self.last_login_at,
        }


class AuthError(Exception):
    pass


# --------------------------------------------------------------------------- #
# 管理员初始化
# --------------------------------------------------------------------------- #
def ensure_admin() -> None:
    """管理员账号只来自环境变量，且唯一。

    ``ADMIN_USERNAME`` / ``ADMIN_PASSWORD``；密码未设置时生成随机值并打到
    日志里（仅首次）。

    已存在管理员时，若环境变量里的密码与库中不一致，会**同步更新密码**——
    这样用户在 ``.env`` 里改了 ``ADMIN_PASSWORD`` 后重启即可生效，
    不必删库重建（管理员密码的唯一权威来源就是环境变量）。
    """
    from kgrag.db import query as q

    username = os.getenv("ADMIN_USERNAME", "admin").strip() or "admin"
    password = os.getenv("ADMIN_PASSWORD", "").strip()

    existing = q("SELECT * FROM users WHERE role = 'admin'")
    if existing:
        admin = existing[0]
        # 环境变量改了用户名时同步
        if len(existing) == 1 and admin["username"] != username:
            execute(
                "UPDATE users SET username = ? WHERE id = ?", (username, admin["id"])
            )
            logger.info(f"管理员用户名已同步为 {username}")

        # 环境变量里显式设置了密码 -> 以它为准（管理员密码由环境变量唯一决定）
        if password and not verify_password(password, admin["password_hash"] or ""):
            execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(password), admin["id"]),
            )
            logger.info("管理员密码已按 ADMIN_PASSWORD 同步更新")
        return

    if not password:
        import secrets

        password = secrets.token_urlsafe(12)
        logger.warning(
            f"未设置 ADMIN_PASSWORD，已生成管理员密码: {password} "
            f"（用户名 {username}，请登录后修改或写入环境变量）"
        )

    execute(
        """INSERT INTO users(id, username, display_name, password_hash, role, source, created_at)
           VALUES (?, ?, ?, ?, 'admin', 'local', ?)""",
        (uuid.uuid4().hex, username, "管理员", hash_password(password), _iso(_now())),
    )
    logger.info(f"已创建管理员账号: {username}")


# --------------------------------------------------------------------------- #
# 登录 / 会话
# --------------------------------------------------------------------------- #
def login(
    username: str,
    password: str,
    ad_config: ADConfig | None = None,
) -> tuple[User, str]:
    """返回 ``(用户, 会话令牌)``。"""
    username = username.strip()
    if not username:
        raise AuthError("用户名不能为空")

    row = query_one("SELECT * FROM users WHERE username = ?", (username,))

    if row is not None:
        user = User.from_row(row)
        if not user.is_active:
            raise AuthError("账号已停用")
        if user.source == "local":
            if not verify_password(password, row["password_hash"] or ""):
                raise AuthError("用户名或密码错误")
            return user, _issue_session(user)

    # AD 认证
    if ad_config is not None and ad_config.enabled:
        try:
            ad_user = ad_authenticate(ad_config, username, password)
        except ADError as e:
            raise AuthError(str(e)) from e

        if row is None:
            execute(
                """INSERT INTO users(id, username, display_name, password_hash, role, source,
                                     email, created_at)
                   VALUES (?, ?, ?, NULL, 'user', 'ad', ?, ?)""",
                (
                    uuid.uuid4().hex,
                    ad_user.username,
                    ad_user.display_name,
                    ad_user.email,
                    _iso(_now()),
                ),
            )
            row = query_one("SELECT * FROM users WHERE username = ?", (username,))
        else:
            execute(
                "UPDATE users SET display_name = ?, email = ? WHERE id = ?",
                (ad_user.display_name, ad_user.email, row["id"]),
            )
            row = query_one("SELECT * FROM users WHERE username = ?", (username,))

        user = User.from_row(row)
        if not user.is_active:
            raise AuthError("账号已停用")
        return user, _issue_session(user)

    raise AuthError("用户名或密码错误")


def _issue_session(user: User) -> str:
    token = new_session_token()
    now = _now()
    execute(
        "INSERT INTO sessions(token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user.id, _iso(now), _iso(now + timedelta(days=SESSION_DAYS))),
    )
    execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_iso(now), user.id))
    return token


def get_user_by_token(token: str) -> User | None:
    if not token:
        return None
    row = query_one(
        """SELECT u.*, s.expires_at FROM sessions s
           JOIN users u ON u.id = s.user_id
           WHERE s.token = ?""",
        (token,),
    )
    if row is None:
        return None
    if row["expires_at"] < _iso(_now()):
        execute("DELETE FROM sessions WHERE token = ?", (token,))
        return None
    return User.from_row(row)


def logout(token: str) -> None:
    execute("DELETE FROM sessions WHERE token = ?", (token,))


def cleanup_sessions() -> None:
    execute("DELETE FROM sessions WHERE expires_at < ?", (_iso(_now()),))


# --------------------------------------------------------------------------- #
# 用户管理
# --------------------------------------------------------------------------- #
def list_users() -> list[User]:
    rows = query("SELECT * FROM users ORDER BY created_at")
    return [User.from_row(r) for r in rows]


def get_user(user_id: str) -> User | None:
    row = query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    return User.from_row(row) if row else None


def create_user(
    username: str,
    password: str,
    *,
    display_name: str = "",
    role: str = "user",
    email: str = "",
) -> User:
    username = username.strip()
    if not username:
        raise AuthError("用户名不能为空")
    if len(password) < 6:
        raise AuthError("密码至少 6 位")
    if query_one("SELECT id FROM users WHERE username = ?", (username,)):
        raise AuthError("用户名已存在")

    uid = uuid.uuid4().hex
    execute(
        """INSERT INTO users(id, username, display_name, password_hash, role, source,
                             email, created_at)
           VALUES (?, ?, ?, ?, ?, 'local', ?, ?)""",
        (
            uid,
            username,
            display_name or username,
            hash_password(password),
            role,
            email,
            _iso(_now()),
        ),
    )
    return get_user(uid)  # type: ignore[return-value]


def update_user(user_id: str, **fields) -> None:
    allowed = {"display_name", "email", "is_active", "role", "password"}
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "password":
            sets.append("password_hash = ?")
            params.append(hash_password(v))
        else:
            sets.append(f"{k} = ?")
            params.append(int(v) if k == "is_active" else v)
    if not sets:
        return
    params.append(user_id)
    execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", tuple(params))


def delete_user(user_id: str) -> None:
    row = query_one("SELECT role FROM users WHERE id = ?", (user_id,))
    if row and row["role"] == "admin":
        raise AuthError("不能删除管理员账号")
    execute("DELETE FROM users WHERE id = ?", (user_id,))
