"""认证接口：登录 / 登出 / 当前用户 / 改密 / 用户管理（管理员）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from kgrag.api.deps import SESSION_COOKIE, current_user, require_admin
from kgrag.audit import record as audit
from kgrag.auth.ad import ADConfig, test_connection
from kgrag.auth.security import verify_password
from kgrag.auth.service import (
    AuthError,
    User,
    create_user,
    delete_user,
    get_user,
    list_users,
    login,
    logout,
    update_user,
)
from kgrag.db import query_one
from kgrag.logging import get_logger
from kgrag.settings import effective

logger = get_logger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

SESSION_MAX_AGE = 7 * 24 * 3600


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6)


class UserCreate(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=6)
    display_name: str = ""
    email: str = ""
    role: str = "user"


class UserUpdate(BaseModel):
    display_name: str | None = None
    email: str | None = None
    is_active: bool | None = None
    role: str | None = None
    password: str | None = Field(None, min_length=6)


def ad_config() -> ADConfig:
    return ADConfig(
        enabled=bool(effective("ad_enabled", False)),
        server_uri=str(effective("ad_server_uri", "") or ""),
        use_ssl=bool(effective("ad_use_ssl", False)),
        base_dn=str(effective("ad_base_dn", "") or ""),
        user_filter=str(effective("ad_user_filter", "(sAMAccountName={username})")),
        bind_dn=str(effective("ad_bind_dn", "") or ""),
        bind_password=str(effective("ad_bind_password", "") or ""),
        display_name_attr=str(effective("ad_display_name_attr", "displayName")),
        email_attr=str(effective("ad_email_attr", "mail")),
    )


@router.post("/login")
def do_login(req: LoginRequest, response: Response) -> dict:
    try:
        user, token = login(req.username, req.password, ad_config())
    except AuthError as e:
        raise HTTPException(401, str(e)) from e

    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )
    logger.info(f"登录成功: {user.username}")
    audit(user.id, user.username, "login", detail=f"source={user.source}")
    return {"user": user.to_dict()}


@router.post("/logout")
def do_logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        logout(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict:
    return user.to_dict()


@router.get("/auth-mode")
def auth_mode() -> dict:
    """登录页用：是否需要显示「AD 域登录」提示。"""
    cfg = ad_config()
    return {"ad_enabled": cfg.enabled, "ad_server": cfg.server_uri if cfg.enabled else ""}


@router.post("/change-password")
def change_password(
    req: ChangePasswordRequest,
    user: User = Depends(current_user),
) -> dict:
    row = query_one("SELECT * FROM users WHERE id = ?", (user.id,))
    if row is None:
        raise HTTPException(404, "用户不存在")
    if row["source"] != "local":
        raise HTTPException(400, "AD 用户请在域内修改密码")
    if not verify_password(req.old_password, row["password_hash"] or ""):
        raise HTTPException(400, "原密码错误")
    update_user(user.id, password=req.new_password)
    audit(user.id, user.username, "change_password")
    return {"ok": True}


# --------------------------------------------------------------------------- #
# 用户管理（管理员）
# --------------------------------------------------------------------------- #
@router.get("/users")
def list_all_users(admin: User = Depends(require_admin)) -> list[dict]:
    return [u.to_dict() for u in list_users()]


@router.post("/users", status_code=201)
def admin_create_user(req: UserCreate, admin: User = Depends(require_admin)) -> dict:
    try:
        u = create_user(
            req.username,
            req.password,
            display_name=req.display_name,
            role=req.role,
            email=req.email,
        )
    except AuthError as e:
        raise HTTPException(400, str(e)) from e
    audit(admin.id, admin.username, "user.create", target=req.username)
    return u.to_dict()


@router.patch("/users/{user_id}")
def admin_update_user(
    user_id: str,
    req: UserUpdate,
    admin: User = Depends(require_admin),
) -> dict:
    target = get_user(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if target.role == "admin" and fields.get("role") == "user":
        raise HTTPException(400, "不能降级唯一的管理员")
    update_user(user_id, **fields)
    audit(admin.id, admin.username, "user.update", target=target.username,
          detail=",".join(fields))
    return get_user(user_id).to_dict()  # type: ignore[union-attr]


@router.delete("/users/{user_id}", status_code=204)
def admin_delete_user(user_id: str, admin: User = Depends(require_admin)) -> None:
    target = get_user(user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")
    try:
        delete_user(user_id)
    except AuthError as e:
        raise HTTPException(400, str(e)) from e
    audit(admin.id, admin.username, "user.delete", target=target.username)


@router.post("/ad/test")
def ad_test(admin: User = Depends(require_admin)) -> dict:
    """用当前保存的配置测试 AD 连接。"""
    return test_connection(ad_config())
