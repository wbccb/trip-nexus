import os
import random
import sqlite3
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Optional

from fastapi import Depends, Header, HTTPException, status

from src.auth.jwt_handler import JwtError, decode_jwt
from src.config import Config


from src.auth.database import get_auth_db_connection, _AUTH_DB_LOCK
from src.utils.sql_loader import load_named_sql, load_sql_statements

_AUTH_INIT_MYSQL_SQL = "auth/init_mysql.sql"
_AUTH_INIT_SQLITE_SQL = "auth/init_sqlite.sql"
_AUTH_QUERIES_SQL = "auth/queries.sql"

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


def init_auth_tables() -> None:
    # 认证相关表都在这里集中初始化，保证：
    # 1) 任意 auth 路径第一次触达时都能自动建表
    # 2) register/login/profile/admin 等接口不需要各自重复写建表逻辑
    with _AUTH_DB_LOCK:
        conn = get_auth_db_connection()
        config = _get_config()
        if conn.backend == 'mysql':
            try:
                cursor = conn.cursor()
                # MySQL 认证表结构统一从独立 SQL 文件加载，避免 Python 代码内嵌 DDL。
                for statement in load_sql_statements(_AUTH_INIT_MYSQL_SQL):
                    cursor.execute(statement)
                # 插入超级管理员
                from src.auth.password import hash_password
                admin_email = config.SUPER_ADMIN_EMAIL
                admin_password = config.SUPER_ADMIN_PASSWORD
                if admin_email and admin_password:
                    cursor.execute(load_named_sql(_AUTH_QUERIES_SQL, "select_admin_user_id_by_email"), (admin_email,))
                    row = cursor.fetchone()
                    if not row:
                        cursor.execute(
                            load_named_sql(_AUTH_QUERIES_SQL, "insert_super_admin"),
                            (admin_email, hash_password(admin_password), "SuperAdmin"),
                        )
                    else:
                        cursor.execute(
                            load_named_sql(_AUTH_QUERIES_SQL, "promote_user_to_admin"),
                            (admin_email,)
                        )
                conn.commit()
            finally:
                conn.close()
            return

        try:
            cursor = conn.cursor()
            # SQLite 认证表结构与后续兼容性迁移统一从独立 SQL 文件/模板加载。
            for statement in load_sql_statements(_AUTH_INIT_SQLITE_SQL):
                cursor.execute(statement)
            cursor.execute(load_named_sql(_AUTH_QUERIES_SQL, "sqlite_pragma_audit_log_columns"))
            audit_columns = {
                str(row["name"]) if isinstance(row, dict) else (str(row["name"]) if isinstance(row, sqlite3.Row) else str(row[1]))
                for row in (cursor.fetchall() or [])
            }
            if "session_id" not in audit_columns:
                cursor.execute(load_named_sql(_AUTH_QUERIES_SQL, "sqlite_alter_audit_log_add_session_id"))
            if "message_id" not in audit_columns:
                cursor.execute(load_named_sql(_AUTH_QUERIES_SQL, "sqlite_alter_audit_log_add_message_id"))

            cursor.execute(load_named_sql(_AUTH_QUERIES_SQL, "sqlite_pragma_users_columns"))
            user_columns = {
                str(row["name"]) if isinstance(row, dict) else (str(row["name"]) if isinstance(row, sqlite3.Row) else str(row[1]))
                for row in (cursor.fetchall() or [])
            }
            if "llm_config" not in user_columns:
                cursor.execute(load_named_sql(_AUTH_QUERIES_SQL, "sqlite_alter_users_add_llm_config"))

            from src.auth.password import hash_password
            admin_email = config.SUPER_ADMIN_EMAIL
            admin_password = config.SUPER_ADMIN_PASSWORD
            
            if admin_email and admin_password:
                cursor.execute(load_named_sql(_AUTH_QUERIES_SQL, "select_admin_user_id_by_email"), (admin_email,))
                row = cursor.fetchone()
                if not row:
                    cursor.execute(
                        load_named_sql(_AUTH_QUERIES_SQL, "insert_super_admin"),
                        (admin_email, hash_password(admin_password), "SuperAdmin"),
                    )
                else:
                    cursor.execute(
                        load_named_sql(_AUTH_QUERIES_SQL, "promote_user_to_admin"),
                        (admin_email,)
                    )
            
            conn.commit()
        finally:
            conn.close()


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    # 这里返回 dict 而不是 ORM 对象，保持和项目里现有 sqlite 访问习惯一致。
    init_auth_tables()
    with get_auth_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(load_named_sql(_AUTH_QUERIES_SQL, "select_user_by_id"), (int(user_id),))
        row = cursor.fetchone()
        return dict(row) if row else None

def is_token_blocked(jti: str) -> bool:
    # auth_blocklist 主要为“主动失效 token”预留。
    # 虽然当前主链还没有完整的登出拉黑流程，但这里已经把查询和过期清理逻辑准备好了。
    if not jti:
        return False
    now_ts = int(__import__("time").time())
    with get_auth_db_connection() as conn:
        cursor = conn.cursor()
        # 仅 1% 概率执行清理，降低写入频率
        if random.random() < 0.01:
            cursor.execute(load_named_sql(_AUTH_QUERIES_SQL, "delete_expired_blocklist"), (now_ts,))
        cursor.execute(load_named_sql(_AUTH_QUERIES_SQL, "select_blocked_token"), (jti,))
        row = cursor.fetchone()
        conn.commit()
        return bool(row)


def revoke_token(jti: str, exp: int) -> None:
    # 未来如果要做 logout / 管理员强制下线，只要把 jti 写进 blocklist 即可。
    if not jti or not exp:
        return
    with get_auth_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            load_named_sql(_AUTH_QUERIES_SQL, "upsert_blocked_token"),
            (str(jti), int(exp)),
        )
        conn.commit()


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
