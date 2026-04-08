import json
from datetime import datetime, timedelta
from uuid import uuid4
from typing import Dict, List, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from src.auth.jwt_handler import build_access_token
from src.auth.middleware import (
    AuthenticatedUser,
    get_auth_db_connection,
    get_current_user,
    init_auth_tables,
)
from src.auth.oauth import get_supported_oauth_providers
from src.auth.password import hash_password, verify_password
from src.api.schemas.auth import (
    AuthRegisterRequest,
    AuthLoginRequest,
    AuthRefreshResponse,
    AuthResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    ResetPasswordRequest,
    SimpleMessageResponse,
    UserProfileUpdateRequest,
    UserPasswordUpdateRequest,
    UserLlmConfig,
)
from src.api.schemas.admin import AdminUserListResponse
from src.models.user import PublicUserProfile
from src.api.dependencies import (
    _get_config,
    _get_user_by_email,
    _get_user_row,
    _count_all_users,
    _build_auth_response,
    _row_to_public_user_profile,
    _apply_auth_request_guard,
    _reset_observability_context,
    _record_audit_log,
)
from src.api.dependencies import _get_llm_manager_for_user
from src.utils.sql_loader import load_named_sql

_AUTH_QUERIES_SQL = "auth/queries.sql"

router = APIRouter(prefix="/api", tags=["auth"])

@router.get("/auth/providers")
def list_auth_providers() -> Dict[str, Dict[str, str]]:
    """获取支持的 OAuth 提供商列表"""
    return {"providers": get_supported_oauth_providers()}


@router.post("/auth/register", response_model=AuthResponse)
def register(payload: AuthRegisterRequest, request: Request) -> AuthResponse:
    """注册新用户"""
    guard_token = _apply_auth_request_guard(request, "/api/auth/register", "auth_register")
    try:
        email = str(payload.email or "").strip().lower()
        if not email:
            raise HTTPException(status_code=400, detail="email 不能为空")
        if _get_user_by_email(email):
            _record_audit_log(action="auth_register", status="duplicate", detail={"email": email})
            raise HTTPException(status_code=409, detail="该邮箱已注册")
        try:
            password_hash = hash_password(payload.password)
        except ValueError as exc:
            _record_audit_log(action="auth_register", status="invalid", detail={"email": email})
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        nickname = str(payload.nickname or "").strip() or email.split("@")[0]
        role = "admin" if _count_all_users() == 0 else "user"
        init_auth_tables()
        with get_auth_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                load_named_sql(_AUTH_QUERIES_SQL, "insert_user"),
                (email, password_hash, nickname, role),
            )
            conn.commit()
            user_id = int(cursor.lastrowid or 0)
        user_row = _get_user_row(user_id)
        if not user_row:
            raise HTTPException(status_code=500, detail="用户创建失败")
        _record_audit_log(action="auth_register", status="success", user_id=user_id, user_email=email, detail={"role": role})
        return _build_auth_response(user_row)
    finally:
        _reset_observability_context(guard_token)


@router.post("/auth/login", response_model=AuthResponse)
def login(payload: AuthLoginRequest, request: Request) -> AuthResponse:
    """用户登录"""
    guard_token = _apply_auth_request_guard(request, "/api/auth/login", "auth_login")
    try:
        email = str(payload.email or "").strip().lower()
        user_row = _get_user_by_email(email)
        if not user_row:
            _record_audit_log(action="auth_login", status="failed", detail={"email": email, "reason": "user_not_found"})
            raise HTTPException(status_code=401, detail="邮箱或密码错误")
        if str(user_row.get("status") or "active") != "active":
            _record_audit_log(action="auth_login", status="blocked", user_id=int(user_row.get("id") or 0), user_email=email, detail={"reason": "banned"})
            raise HTTPException(status_code=403, detail="账号已被禁用")
        if not verify_password(payload.password, str(user_row.get("password_hash") or "")):
            _record_audit_log(action="auth_login", status="failed", user_id=int(user_row.get("id") or 0), user_email=email, detail={"reason": "wrong_password"})
            raise HTTPException(status_code=401, detail="邮箱或密码错误")
        _record_audit_log(action="auth_login", status="success", user_id=int(user_row.get("id") or 0), user_email=email, detail={"role": str(user_row.get("role") or "user")})
        return _build_auth_response(user_row)
    finally:
        _reset_observability_context(guard_token)


@router.post("/auth/refresh", response_model=AuthRefreshResponse)
def refresh_token(current_user: AuthenticatedUser = Depends(get_current_user)) -> AuthRefreshResponse:
    """刷新访问令牌"""
    user_row = _get_user_row(current_user.user_id)
    if not user_row:
        raise HTTPException(status_code=401, detail="用户不存在")
    auth_response = _build_auth_response(user_row)
    return AuthRefreshResponse(token=auth_response.token)


@router.post("/auth/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(payload: ForgotPasswordRequest, request: Request) -> ForgotPasswordResponse:
    """忘记密码，请求重置令牌"""
    guard_token = _apply_auth_request_guard(request, "/api/auth/forgot-password", "auth_forgot_password")
    try:
        email = str(payload.email or "").strip().lower()
        user_row = _get_user_by_email(email)
        if not user_row:
            _record_audit_log(action="auth_forgot_password", status="accepted", detail={"email": email, "matched": False})
            return ForgotPasswordResponse(message="如果邮箱存在，重置链接已发送")
        reset_token = uuid4().hex
        expires_at = datetime.now() + timedelta(minutes=_get_config().PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)
        with get_auth_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                load_named_sql(_AUTH_QUERIES_SQL, "insert_password_reset_token"),
                (int(user_row.get("id") or 0), reset_token, expires_at.isoformat()),
            )
            conn.commit()
        _record_audit_log(action="auth_forgot_password", status="success", user_id=int(user_row.get("id") or 0), user_email=email, detail={"matched": True})
        return ForgotPasswordResponse(message="开发模式已生成重置 token", reset_token=reset_token)
    finally:
        _reset_observability_context(guard_token)


@router.post("/auth/reset-password", response_model=SimpleMessageResponse)
def reset_password(payload: ResetPasswordRequest, request: Request) -> SimpleMessageResponse:
    """使用重置令牌重置密码"""
    guard_token = _apply_auth_request_guard(request, "/api/auth/reset-password", "auth_reset_password")
    try:
        try:
            next_password_hash = hash_password(payload.new_password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with get_auth_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                load_named_sql(_AUTH_QUERIES_SQL, "select_password_reset_token"),
                (str(payload.token or "").strip(),),
            )
            token_row = cursor.fetchone()
            if not token_row:
                _record_audit_log(action="auth_reset_password", status="failed", detail={"reason": "invalid_token"})
                raise HTTPException(status_code=400, detail="重置 token 无效")
            token_data = dict(token_row)
            if datetime.fromisoformat(str(token_data.get("expires_at"))) < datetime.now():
                _record_audit_log(action="auth_reset_password", status="failed", detail={"reason": "expired_token"})
                raise HTTPException(status_code=400, detail="重置 token 已过期")
            cursor.execute(
                load_named_sql(_AUTH_QUERIES_SQL, "update_user_password_and_token_version"),
                (next_password_hash, int(token_data.get("user_id") or 0)),
            )
            cursor.execute(
                load_named_sql(_AUTH_QUERIES_SQL, "update_password_reset_token_used"),
                (datetime.now().isoformat(), int(token_data.get("id") or 0)),
            )
            conn.commit()
            updated_user = _get_user_row(int(token_data.get("user_id") or 0)) or {}
        _record_audit_log(
            action="auth_reset_password",
            status="success",
            user_id=int(updated_user.get("id") or 0),
            user_email=str(updated_user.get("email") or ""),
        )
        return SimpleMessageResponse(message="密码重置成功")
    finally:
        _reset_observability_context(guard_token)


@router.get("/user/profile", response_model=PublicUserProfile)
def get_user_profile(current_user: AuthenticatedUser = Depends(get_current_user)) -> PublicUserProfile:
    """获取当前登录用户资料"""
    user_row = _get_user_row(current_user.user_id)
    if not user_row:
        raise HTTPException(status_code=404, detail="用户不存在")
    return _row_to_public_user_profile(user_row)


@router.put("/user/profile", response_model=PublicUserProfile)
def update_user_profile(
    payload: UserProfileUpdateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> PublicUserProfile:
    """更新当前登录用户资料"""
    nickname = str(payload.nickname or "").strip()
    if not nickname:
        raise HTTPException(status_code=400, detail="nickname 不能为空")
    with get_auth_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            load_named_sql(_AUTH_QUERIES_SQL, "update_user_nickname"),
            (nickname, current_user.user_id),
        )
        conn.commit()
    user_row = _get_user_row(current_user.user_id)
    if not user_row:
        raise HTTPException(status_code=404, detail="用户不存在")
    return _row_to_public_user_profile(user_row)


@router.put("/user/password", response_model=SimpleMessageResponse)
def update_user_password(
    payload: UserPasswordUpdateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> SimpleMessageResponse:
    """更新当前登录用户密码"""
    user_row = _get_user_row(current_user.user_id)
    if not user_row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not verify_password(payload.old_password, str(user_row.get("password_hash") or "")):
        raise HTTPException(status_code=401, detail="旧密码错误")
    try:
        next_password_hash = hash_password(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with get_auth_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            load_named_sql(_AUTH_QUERIES_SQL, "update_user_password_and_token_version"),
            (next_password_hash, current_user.user_id),
        )
        conn.commit()
    return SimpleMessageResponse(message="密码更新成功，请重新登录")

@router.get("/auth/user/llm-config", response_model=UserLlmConfig)
@router.get("/user/llm-config", response_model=UserLlmConfig)
def get_user_llm_config(current_user: AuthenticatedUser = Depends(get_current_user)) -> UserLlmConfig:
    """获取当前登录用户的私有大模型配置"""
    user_row = _get_user_row(current_user.user_id)
    if not user_row:
        raise HTTPException(status_code=404, detail="用户不存在")
    llm_config_str = str(user_row.get("llm_config") or "{}")
    user_config = {}
    if llm_config_str and llm_config_str != "{}":
        try:
            parsed = json.loads(llm_config_str)
            if isinstance(parsed, dict):
                user_config = parsed
        except Exception:
            pass

    config = _get_config()
    return UserLlmConfig(
        analysis_provider=user_config.get("analysis_provider", config.ANALYSIS_PROVIDER),
        analysis_base_url=user_config.get("analysis_base_url", config.ANALYSIS_BASE_URL),
        analysis_model_name=user_config.get("analysis_model_name", config.ANALYSIS_MODEL_NAME),
        analysis_api_key=user_config.get("analysis_api_key", config.ANALYSIS_API_KEY),
        analysis_temperature=user_config.get("analysis_temperature", config.ANALYSIS_TEMPERATURE),
        generation_provider=user_config.get("generation_provider", config.GENERATION_PROVIDER),
        generation_base_url=user_config.get("generation_base_url", config.GENERATION_BASE_URL),
        generation_model_name=user_config.get("generation_model_name", config.GENERATION_MODEL_NAME),
        generation_api_key=user_config.get("generation_api_key", config.GENERATION_API_KEY),
        generation_temperature=user_config.get("generation_temperature", config.GENERATION_TEMPERATURE),
    )


@router.put("/auth/user/llm-config", response_model=SimpleMessageResponse)
@router.put("/user/llm-config", response_model=SimpleMessageResponse)
def update_user_llm_config(
    payload: UserLlmConfig,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> SimpleMessageResponse:
    """更新当前登录用户的私有大模型配置（保存前自动测试连通性）"""
    from src.llm.llm_manager import LlmManager
    from langchain_core.messages import HumanMessage
    
    test_config = payload.model_dump()
    
    # 自动测试 LLM 配置的连通性
    try:
        temp_llm_manager = LlmManager(
            model_name=test_config["generation_model_name"],
            ollama_base_url=test_config["generation_base_url"],
            provider=test_config["generation_provider"],
            base_url=test_config["generation_base_url"],
            api_key=test_config["generation_api_key"],
            temperature=test_config["generation_temperature"],
        )
        temp_llm_manager.update_llm_config(test_config)
        
        # 测试意图识别模型
        analysis_llm = temp_llm_manager.get_analysis_llm()
        analysis_response = analysis_llm.invoke([HumanMessage(content="Hello! Please reply with just 'OK'.")])
        if not analysis_response or not getattr(analysis_response, "content", ""):
            raise HTTPException(status_code=400, detail="模型连接测试失败，未返回有效内容")
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"模型连接失败: {str(e)}")

    # 测试通过，落库保存
    llm_config_str = json.dumps(test_config, ensure_ascii=False)
    with get_auth_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            load_named_sql(_AUTH_QUERIES_SQL, "update_user_llm_config"),
            (llm_config_str, current_user.user_id),
        )
        conn.commit()

    return SimpleMessageResponse(message="LLM 配置测试通过并保存成功")
