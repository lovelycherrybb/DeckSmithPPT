"""密码哈希与校验（用户系统 v0.8）。

只用标准库 hashlib.pbkdf2_hmac + secrets，不引 bcrypt/argon2/passlib——后端依赖入口
是单一 requirements.txt、镜像离线部署，多一个 C 扩展依赖就多一处跨平台可解析性风险
（见 requirements.txt 顶注与 ADR-012「共用一个解释器」口径）。pbkdf2-sha256 对本地
单节点产品的口令场景足够安全。

存储格式（自描述，便于将来调迭代数）：pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 240_000  # OWASP 2023 对 PBKDF2-HMAC-SHA256 的推荐量级
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """把明文口令散列成自描述字符串。明文不落地、不进日志。"""
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验明文是否匹配存储串；格式非法或算法不符一律判失败，不抛。"""
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)
