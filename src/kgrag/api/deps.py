"""FastAPI 依赖：从 Cookie 取当前用户、角色校验。"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from kgrag.auth.service import User, get_user_by_token

SESSION_COOKIE = "kgrag_session"


def current_user_optional(request: Request) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return get_user_by_token(token)


def current_user(request: Request) -> User:
    user = current_user_optional(request)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录或会话已过期")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")
    return user
