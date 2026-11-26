"""密码哈希与会话令牌。

用标准库 ``hashlib.scrypt``，不引入 bcrypt/passlib 依赖。
scrypt 是内存硬算法，抗 GPU 暴力破解，Python 标准库直接支持。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# scrypt 参数：n=2^14, r=8, p=1 约 16MB 内存、几十毫秒，够用且不拖慢登录
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DK_LEN = 32


def hash_password(password: str) -> str:
    """返回 ``scrypt$n$r$p$salt_b64$hash_b64``。"""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_DK_LEN,
    )
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(dk).decode(),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    """校验密码，任何格式异常都返回 False（不抛异常）。"""
    try:
        algo, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(base64.b64decode(hash_b64)),
        )
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)
