import os
import sqlite3
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Optional

from fastapi import Depends, Header, HTTPException, status

from src.auth.jwt_handler import JwtError, decode_jwt
from src.config import Config


AUTH_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "auth.db"))
_AUTH_DB_LOCK = threading.Lock()


@dataclass
class AuthenticatedUser:
    # 这里不是完整的 users 表镜像，而是“当前请求真正需要的鉴权上下文”。
    # 这样 API 层拿到的对象足够轻，后续若更换底层数据库实现，也不容易影响上层业务代码。
    user_id: int
    email: str
    nickname: str
    role: str
    status: str
    token_quota: int
    token_used: int
    token_version: int


@lru_cache(maxsize=1)
def _get_config() -> Config:
    return Config()


def get_auth_db_connection() -> sqlite3.Connection:
    # 认证系统目前独立落在 auth.db，不和原会话/业务表混在一起，
    # 目的是先把账号体系快速独立落地，避免牵一发动全身。
    conn = sqlite3.connect(AUTH_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_auth_tables() -> None:
    # 认证相关表都在这里集中初始化，保证：
    # 1) 任意 auth 路径第一次触达时都能自动建表
    # 2) register/login/profile/admin 等接口不需要各自重复写建表逻辑
    with _AUTH_DB_LOCK:
        conn = get_auth_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    nickname TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT 'user',
                    status TEXT NOT NULL DEFAULT 'active',
                    token_quota INTEGER NOT NULL DEFAULT 1000000,
                    token_used INTEGER NOT NULL DEFAULT 0,
                    token_version INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    used_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_blocklist (
                    jti TEXT PRIMARY KEY,
                    expires_at INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS token_usage_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    session_id TEXT NOT NULL DEFAULT '',
                    request_path TEXT NOT NULL,
                    model_name TEXT NOT NULL DEFAULT '',
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    stage TEXT NOT NULL DEFAULT '',
                    message_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    user_email TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT '',
                    message_id TEXT NOT NULL DEFAULT '',
                    request_path TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    ip_address TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS rate_limit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject_key TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    request_path TEXT NOT NULL DEFAULT '',
                    ip_address TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL
                )
                """
            )
            # audit_log 是 v0.0.7 过程中逐步补强出来的表。
            # 线上已有旧库时，不能假设表结构和最新代码完全一致，
            # 所以这里在建表后再做一次“按列补齐”的轻量迁移，保证新增列能自动落地。
            cursor.execute("PRAGMA table_info(audit_log)")
            audit_columns = {
                str(row["name"]) if isinstance(row, sqlite3.Row) else str(row[1])
                for row in (cursor.fetchall() or [])
            }
            if "session_id" not in audit_columns:
                cursor.execute("ALTER TABLE audit_log ADD COLUMN session_id TEXT NOT NULL DEFAULT ''")
            if "message_id" not in audit_columns:
                cursor.execute("ALTER TABLE audit_log ADD COLUMN message_id TEXT NOT NULL DEFAULT ''")
            conn.commit()
        finally:
            conn.close()


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    # 这里返回 dict 而不是 ORM 对象，保持和项目里现有 sqlite 访问习惯一致。
    init_auth_tables()
    conn = get_auth_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (int(user_id),))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def is_token_blocked(jti: str) -> bool:
    # auth_blocklist 主要为“主动失效 token”预留。
    # 虽然当前主链还没有完整的登出拉黑流程，但这里已经把查询和过期清理逻辑准备好了。
    if not jti:
        return False
    now_ts = int(__import__("time").time())
    conn = get_auth_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM auth_blocklist WHERE expires_at <= ?", (now_ts,))
        cursor.execute("SELECT 1 FROM auth_blocklist WHERE jti = ? LIMIT 1", (jti,))
        row = cursor.fetchone()
        conn.commit()
        return bool(row)
    finally:
        conn.close()


def revoke_token(jti: str, exp: int) -> None:
    # 未来如果要做 logout / 管理员强制下线，只要把 jti 写进 blocklist 即可。
    if not jti or not exp:
        return
    conn = get_auth_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO auth_blocklist (jti, expires_at) VALUES (?, ?)",
            (str(jti), int(exp)),
        )
        conn.commit()
    finally:
        conn.close()


def _build_authenticated_user(user_row: Dict[str, Any]) -> AuthenticatedUser:
    # 所有鉴权通过后的用户信息都在这里统一收敛成 AuthenticatedUser，
    # 避免每个 Depends 调用点各自做字段转换。
    return AuthenticatedUser(
        user_id=int(user_row.get("id") or 0),
        email=str(user_row.get("email") or ""),
        nickname=str(user_row.get("nickname") or ""),
        role=str(user_row.get("role") or "user"),
        status=str(user_row.get("status") or "active"),
        token_quota=int(user_row.get("token_quota") or 0),
        token_used=int(user_row.get("token_used") or 0),
        token_version=int(user_row.get("token_version") or 0),
    )


def _parse_bearer_token(authorization: Optional[str]) -> Optional[str]:
    # 只接受标准 Authorization: Bearer <token> 头，
    # 不再保留旧 query/body 传 user_id 的路径，减少认证边界的歧义。
    header_text = str(authorization or "").strip()
    if not header_text:
        return None
    prefix = "bearer "
    if header_text.lower().startswith(prefix):
        return header_text[len(prefix):].strip()
    return None


def resolve_current_user(authorization: Optional[str], required: bool = True) -> Optional[AuthenticatedUser]:
    # 这是整套鉴权的核心入口，顺序上依次做：
    # 1) 解析 Bearer Token
    # 2) 校验 JWT 签名与时间
    # 3) 检查是否在 blocklist
    # 4) 回表确认用户仍存在且状态正常
    # 5) 校验 token_version，确保改密后旧 token 失效
    token = _parse_bearer_token(authorization)
    if not token:
        if required:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供登录凭证")
        return None
    config = _get_config()
    try:
        payload = decode_jwt(token, secret_key=config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)
    except JwtError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    if is_token_blocked(str(payload.get("jti") or "")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token 已失效")
    user_row = get_user_by_id(int(payload.get("sub") or 0))
    if not user_row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    if str(user_row.get("status") or "active") != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被禁用")
    if int(user_row.get("token_version") or 0) != int(payload.get("token_version") or 0):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token 已失效，请重新登录")
    return _build_authenticated_user(user_row)


def get_current_user(authorization: Optional[str] = Header(None)) -> AuthenticatedUser:
    # 业务接口默认都应走强鉴权版本。
    return resolve_current_user(authorization, required=True)


def get_optional_user(authorization: Optional[str] = Header(None)) -> Optional[AuthenticatedUser]:
    # 仅用于 health 之类“可匿名也可带登录态”的接口。
    return resolve_current_user(authorization, required=False)


def require_admin(current_user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    # Admin 接口统一在 Depends 层拦截，避免每个路由函数内部再重复写角色判断。
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return current_user
