"""口令哈希与会话 token。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_DKLEN = 32


def hash_password(password: str) -> str:
    """scrypt 哈希，格式：scrypt$n$r$p$salt_b64$dk_b64。"""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def password_strength_error(password: str) -> str | None:
    """返回错误信息；None 表示通过。"""
    if password is None or len(password) < 8:
        return "密码至少 8 位"
    if len(password) > 128:
        return "密码过长（最多 128 位）"
    if password.isdigit():
        return "密码不能是纯数字"
    return None
