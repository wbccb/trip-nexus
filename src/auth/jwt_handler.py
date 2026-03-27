import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict
from uuid import uuid4


class JwtError(Exception):
    """JWT 解析或校验失败。"""


def _b64url_encode(raw: bytes) -> str:
    # JWT 规范要求使用 base64url 且去掉尾部 "=" padding，
    # 这样生成出来的 token 才能直接安全地放进 URL / Header 中传输。
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(raw: str) -> bytes:
    # 解析时要把编码阶段拿掉的 padding 补回来，否则标准库无法正确解码。
    text = str(raw or "")
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def _json_dumps(payload: Dict[str, Any]) -> bytes:
    # JWT header / payload 都是 JSON 对象，这里统一压缩序列化格式，
    # 避免不同调用点拼接出来的 token 因空格差异而不一致。
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def encode_jwt(payload: Dict[str, Any], secret_key: str, algorithm: str = "HS256") -> str:
    # 当前 Phase 1 / Phase 2 只支持 HS256，一方面依赖更轻，
    # 另一方面足够覆盖当前单体项目的服务端签发场景。
    if algorithm != "HS256":
        raise JwtError("当前仅支持 HS256")
    header = {"alg": algorithm, "typ": "JWT"}
    header_part = _b64url_encode(_json_dumps(header))
    payload_part = _b64url_encode(_json_dumps(payload))
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    signature = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{_b64url_encode(signature)}"


def decode_jwt(token: str, secret_key: str, algorithm: str = "HS256") -> Dict[str, Any]:
    # 解码时除了还原 payload，还会一并完成签名校验、过期校验和生效时间校验。
    # 也就是说，只要 decode_jwt 返回成功，调用方就可以把这个 payload 当成“可信 token”使用。
    if algorithm != "HS256":
        raise JwtError("当前仅支持 HS256")
    parts = str(token or "").split(".")
    if len(parts) != 3:
        raise JwtError("token 格式不合法")
    header_part, payload_part, signature_part = parts
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    expected_signature = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    provided_signature = _b64url_decode(signature_part)
    if not hmac.compare_digest(expected_signature, provided_signature):
        raise JwtError("token 签名校验失败")
    try:
        payload = json.loads(_b64url_decode(payload_part).decode("utf-8"))
    except Exception as exc:
        raise JwtError("token payload 解析失败") from exc
    now_ts = int(time.time())
    if int(payload.get("exp") or 0) <= now_ts:
        raise JwtError("token 已过期")
    if int(payload.get("nbf") or 0) > now_ts:
        raise JwtError("token 尚未生效")
    return payload


def build_access_token(
    *,
    user_id: int,
    email: str,
    role: str,
    token_version: int,
    secret_key: str,
    expire_minutes: int = 120,
    algorithm: str = "HS256",
) -> str:
    # token_version 是这次认证体系里非常关键的一个字段：
    # 当用户修改密码后，我们只需要把 users.token_version + 1，
    # 所有旧 token 就会因为版本不匹配而自然失效，不需要全量追踪每一枚历史 token。
    issued_at = int(time.time())
    payload = {
        "sub": str(user_id),
        "email": str(email or ""),
        "role": str(role or "user"),
        "token_version": int(token_version or 0),
        "iat": issued_at,
        "nbf": issued_at,
        "exp": issued_at + max(60, int(expire_minutes) * 60),
        "jti": uuid4().hex,
    }
    return encode_jwt(payload, secret_key=secret_key, algorithm=algorithm)
