"""
AD 域认证（LDAP）。

用 ``ldap3`` 对接。流程：
1. 用服务账号（或匿名）绑定，按 ``user_filter`` 搜到用户 DN
2. 用该 DN + 用户密码再绑一次，成功即认证通过

配置项全部来自管理员配置页（存 SQLite），无硬编码。
"""

from __future__ import annotations

from dataclasses import dataclass

from kgrag.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ADConfig:
    enabled: bool = False
    server_uri: str = ""          # ldap://ad.example.com:389
    use_ssl: bool = False
    base_dn: str = ""             # dc=example,dc=com
    user_filter: str = "(sAMAccountName={username})"
    bind_dn: str = ""             # 服务账号
    bind_password: str = ""
    display_name_attr: str = "displayName"
    email_attr: str = "mail"
    timeout: int = 5


@dataclass
class ADUser:
    username: str
    display_name: str
    email: str


class ADError(Exception):
    pass


def authenticate(cfg: ADConfig, username: str, password: str) -> ADUser:
    """认证成功返回用户信息，失败抛 :class:`ADError`。"""
    if not cfg.enabled:
        raise ADError("AD 认证未启用")
    if not password:
        # 空密码在 LDAP 里会被当成匿名绑定并"成功"，必须显式拒绝
        raise ADError("密码不能为空")

    try:
        from ldap3 import NONE, SIMPLE, SUBTREE, Connection, Server
        from ldap3.core.exceptions import LDAPException
    except ImportError as e:  # pragma: no cover
        raise ADError("缺少 ldap3 依赖，请执行 pip install ldap3") from e

    server = Server(cfg.server_uri, use_ssl=cfg.use_ssl, get_info=NONE, connect_timeout=cfg.timeout)

    try:
        # 1. 服务账号绑定，查用户 DN
        if cfg.bind_dn:
            svc = Connection(
                server,
                user=cfg.bind_dn,
                password=cfg.bind_password,
                authentication=SIMPLE,
                auto_bind=False,
                receive_timeout=cfg.timeout,
            )
        else:
            svc = Connection(
                server,
                authentication=NONE,
                auto_bind=False,
                receive_timeout=cfg.timeout,
            )
        if not svc.bind():
            raise ADError(f"服务账号绑定失败: {svc.result.get('description', '未知')}")

        search_filter = cfg.user_filter.format(username=username)
        ok = svc.search(
            cfg.base_dn,
            search_filter,
            search_scope=SUBTREE,
            attributes=[cfg.display_name_attr, cfg.email_attr],
        )
        if not ok or not svc.entries:
            raise ADError(f"用户不存在: {username}")

        entry = svc.entries[0]
        user_dn = entry.entry_dn
        display_name = _attr(entry, cfg.display_name_attr) or username
        email = _attr(entry, cfg.email_attr) or ""
        svc.unbind()

        # 2. 用用户 DN + 密码验证
        user_conn = Connection(
            server,
            user=user_dn,
            password=password,
            authentication=SIMPLE,
            auto_bind=False,
            receive_timeout=cfg.timeout,
        )
        if not user_conn.bind():
            raise ADError("用户名或密码错误")
        user_conn.unbind()

    except LDAPException as e:
        raise ADError(f"LDAP 连接失败: {e}") from e

    logger.info(f"AD 认证通过: {username}")
    return ADUser(username=username, display_name=display_name, email=email)


def test_connection(cfg: ADConfig) -> dict:
    """配置页的「测试连接」按钮用。"""
    try:
        from ldap3 import NONE, SIMPLE, Connection, Server
        from ldap3.core.exceptions import LDAPException
    except ImportError:
        return {"ok": False, "message": "缺少 ldap3 依赖"}

    try:
        server = Server(
            cfg.server_uri, use_ssl=cfg.use_ssl, get_info=NONE, connect_timeout=cfg.timeout
        )
        conn = Connection(
            server,
            user=cfg.bind_dn or None,
            password=cfg.bind_password or None,
            authentication=SIMPLE if cfg.bind_dn else NONE,
            auto_bind=False,
            receive_timeout=cfg.timeout,
        )
        if conn.bind():
            conn.unbind()
            return {"ok": True, "message": "连接成功"}
        return {"ok": False, "message": f"绑定失败: {conn.result.get('description', '未知')}"}
    except LDAPException as e:
        return {"ok": False, "message": f"连接失败: {e}"}


def _attr(entry, name: str) -> str:
    try:
        val = entry[name].value
        return str(val) if val is not None else ""
    except (KeyError, AttributeError):
        return ""
