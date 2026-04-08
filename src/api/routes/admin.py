from typing import List, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.auth.middleware import (
    AuthenticatedUser,
    get_auth_db_connection,
    init_auth_tables,
    require_admin,
)
from src.api.schemas.admin import (
    AdminUserListResponse,
    AdminUserStatusUpdateRequest,
    AdminUserQuotaUpdateRequest,
    AdminDashboardResponse,
    TokenUsageLogItem,
    TokenUsageLogListResponse,
    AuditLogItem,
    AuditLogListResponse,
)
from src.api.schemas.auth import PublicUserProfile
from src.api.dependencies import (
    _get_user_row,
    _row_to_public_user_profile,
    _apply_authenticated_request_guard,
    _reset_observability_context,
    _record_audit_log,
)
from src.utils.sql_loader import load_named_sql, render_named_sql

_AUTH_QUERIES_SQL = "auth/queries.sql"

router = APIRouter(prefix="/api/admin", tags=["admin"])

@router.get("/users", response_model=AdminUserListResponse)
def admin_list_users(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    keyword: Optional[str] = Query(None, description="搜索关键字"),
    request: Request = None,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> AdminUserListResponse:
    """获取所有用户列表（分页）"""
    guard_token = _apply_authenticated_request_guard(
        request=request,
        current_user=current_user,
        request_path="/api/admin/users",
        bucket="admin_users_list",
    )
    try:
        init_auth_tables()
        with get_auth_db_connection() as conn:
            cursor = conn.cursor()
            like_keyword = f"%{str(keyword or '').strip()}%"
            where_clause = "WHERE email LIKE ? OR nickname LIKE ?" if str(keyword or "").strip() else ""
            params: List[Any] = [like_keyword, like_keyword] if str(keyword or "").strip() else []
            cursor.execute(
                render_named_sql(_AUTH_QUERIES_SQL, "list_users_count", {"__WHERE_CLAUSE__": where_clause}),
                params,
            )
            total_row = cursor.fetchone()
            total = int(dict(total_row).get("total") or 0) if total_row else 0
            
            cursor.execute(
                render_named_sql(_AUTH_QUERIES_SQL, "list_users_page", {"__WHERE_CLAUSE__": where_clause}),
                params + [page_size, (page - 1) * page_size],
            )
            rows = cursor.fetchall() or []
            items = [_row_to_public_user_profile(dict(row)) for row in rows]
            return AdminUserListResponse(total=total, items=items)
    finally:
        _reset_observability_context(guard_token)


@router.put("/users/{user_id}/status", response_model=PublicUserProfile)
def admin_update_user_status(
    user_id: int,
    payload: AdminUserStatusUpdateRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> PublicUserProfile:
    """更新用户状态（激活/封禁）"""
    next_status = str(payload.status or "").strip().lower()
    if next_status not in {"active", "banned"}:
        raise HTTPException(status_code=400, detail="status 仅支持 active/banned")
    guard_token = _apply_authenticated_request_guard(
        request=request,
        current_user=current_user,
        request_path=f"/api/admin/users/{user_id}/status",
        bucket="admin_user_status",
    )
    try:
        target_user = _get_user_row(user_id)
        if not target_user:
            raise HTTPException(status_code=404, detail="用户不存在")
        with get_auth_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                load_named_sql(_AUTH_QUERIES_SQL, "update_user_status"),
                (next_status, user_id),
            )
            conn.commit()
        refreshed_user = _get_user_row(user_id)
        if not refreshed_user:
            raise HTTPException(status_code=404, detail="用户不存在")
        _record_audit_log(
            action="admin_update_user_status",
            status="success",
            user_id=current_user.user_id,
            user_email=current_user.email,
            detail={"target_user_id": user_id, "status": next_status},
        )
        return _row_to_public_user_profile(refreshed_user)
    finally:
        _reset_observability_context(guard_token)


@router.put("/users/{user_id}/quota", response_model=PublicUserProfile)
def admin_update_user_quota(
    user_id: int,
    payload: AdminUserQuotaUpdateRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> PublicUserProfile:
    """更新用户 Token 配额"""
    next_quota = int(payload.token_quota or 0)
    if next_quota < 0:
        raise HTTPException(status_code=400, detail="token_quota 不能小于 0")
    guard_token = _apply_authenticated_request_guard(
        request=request,
        current_user=current_user,
        request_path=f"/api/admin/users/{user_id}/quota",
        bucket="admin_user_quota",
    )
    try:
        target_user = _get_user_row(user_id)
        if not target_user:
            raise HTTPException(status_code=404, detail="用户不存在")
        with get_auth_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                load_named_sql(_AUTH_QUERIES_SQL, "update_user_quota"),
                (int(payload.token_quota or 0), user_id),
            )
            conn.commit()
        refreshed_user = _get_user_row(user_id)
        if not refreshed_user:
            raise HTTPException(status_code=404, detail="用户不存在")
        _record_audit_log(
            action="admin_update_user_quota",
            status="success",
            user_id=current_user.user_id,
            user_email=current_user.email,
            detail={"target_user_id": user_id, "token_quota": next_quota},
        )
        return _row_to_public_user_profile(refreshed_user)
    finally:
        _reset_observability_context(guard_token)


@router.get("/dashboard", response_model=AdminDashboardResponse)
def admin_dashboard(
    request: Request,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> AdminDashboardResponse:
    """获取管理后台概览数据"""
    guard_token = _apply_authenticated_request_guard(
        request=request,
        current_user=current_user,
        request_path="/api/admin/dashboard",
        bucket="admin_dashboard",
    )
    try:
        init_auth_tables()
        with get_auth_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(load_named_sql(_AUTH_QUERIES_SQL, "admin_dashboard"))
            row = cursor.fetchone()
            payload = dict(row) if row else {}
            total_quota = int(payload.get("total_token_quota") or 0)
            total_used = int(payload.get("total_token_used") or 0)
            return AdminDashboardResponse(
                total_users=int(payload.get("total_users") or 0),
                active_users=int(payload.get("active_users") or 0),
                banned_users=int(payload.get("banned_users") or 0),
                admin_users=int(payload.get("admin_users") or 0),
                total_token_quota=total_quota,
                total_token_used=total_used,
                quota_remaining=max(total_quota - total_used, 0),
            )
    finally:
        _reset_observability_context(guard_token)


@router.get("/users/{user_id}/token-usage", response_model=TokenUsageLogListResponse)
def admin_user_token_usage(
    user_id: int,
    request: Request,
    limit: int = Query(50, ge=1, le=200, description="返回数量"),
    current_user: AuthenticatedUser = Depends(require_admin),
) -> TokenUsageLogListResponse:
    """获取特定用户的 Token 使用日志"""
    guard_token = _apply_authenticated_request_guard(
        request=request,
        current_user=current_user,
        request_path=f"/api/admin/users/{user_id}/token-usage",
        bucket="admin_user_token_usage",
    )
    try:
        init_auth_tables()
        with get_auth_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(load_named_sql(_AUTH_QUERIES_SQL, "token_usage_count_by_user"), (user_id,))
            total_row = cursor.fetchone()
            total = int((dict(total_row) if total_row else {}).get("total") or 0)
            cursor.execute(
                load_named_sql(_AUTH_QUERIES_SQL, "token_usage_list_by_user"),
                (user_id, limit),
            )
            rows = cursor.fetchall() or []
            items = [TokenUsageLogItem(**dict(row)) for row in rows]
            return TokenUsageLogListResponse(total=total, items=items)
    finally:
        _reset_observability_context(guard_token)


@router.get("/audit-logs", response_model=AuditLogListResponse)
def admin_audit_logs(
    request: Request,
    limit: int = Query(100, ge=1, le=500, description="返回数量"),
    action: Optional[str] = Query(None, description="动作筛选"),
    user_id: Optional[int] = Query(None, description="用户 ID 筛选"),
    session_id: Optional[str] = Query(None, description="会话 ID 筛选"),
    message_id: Optional[str] = Query(None, description="消息 ID 筛选"),
    request_path: Optional[str] = Query(None, description="接口路径筛选"),
    current_user: AuthenticatedUser = Depends(require_admin),
) -> AuditLogListResponse:
    """获取系统审计日志"""
    guard_token = _apply_authenticated_request_guard(
        request=request,
        current_user=current_user,
        request_path="/api/admin/audit-logs",
        bucket="admin_audit_logs",
    )
    try:
        init_auth_tables()
        with get_auth_db_connection() as conn:
            cursor = conn.cursor()
            where_clauses: List[str] = []
            params: List[Any] = []
            if str(action or "").strip():
                where_clauses.append("action = ?")
                params.append(str(action).strip())
            if user_id is not None:
                where_clauses.append("user_id = ?")
                params.append(int(user_id))
            if str(session_id or "").strip():
                where_clauses.append("session_id = ?")
                params.append(str(session_id).strip())
            if str(message_id or "").strip():
                where_clauses.append("message_id = ?")
                params.append(str(message_id).strip())
            if str(request_path or "").strip():
                where_clauses.append("request_path = ?")
                params.append(str(request_path).strip())
            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            cursor.execute(
                render_named_sql(_AUTH_QUERIES_SQL, "audit_logs_count", {"__WHERE_CLAUSE__": where_sql}),
                tuple(params),
            )
            total_row = cursor.fetchone()
            total = int((dict(total_row) if total_row else {}).get("total") or 0)
            cursor.execute(
                render_named_sql(_AUTH_QUERIES_SQL, "audit_logs_list", {"__WHERE_CLAUSE__": where_sql}),
                tuple(params + [limit]),
            )
            rows = cursor.fetchall() or []
            items = [AuditLogItem(**dict(row)) for row in rows]
            return AuditLogListResponse(total=total, items=items)
    finally:
        _reset_observability_context(guard_token)
