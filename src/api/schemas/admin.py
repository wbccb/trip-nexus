from typing import Optional, Dict, List, Any
from pydantic import BaseModel, Field
from src.models.user import PublicUserProfile

class AdminUserListResponse(BaseModel):
    total: int = Field(..., description="用户总数")
    items: List[PublicUserProfile] = Field(default_factory=list, description="用户列表")


class AdminUserStatusUpdateRequest(BaseModel):
    status: str = Field(..., description="用户状态 active/banned")


class AdminUserQuotaUpdateRequest(BaseModel):
    token_quota: int = Field(..., description="Token 配额")


class AdminDashboardResponse(BaseModel):
    total_users: int = Field(0, description="总用户数")
    active_users: int = Field(0, description="活跃用户数")
    banned_users: int = Field(0, description="封禁用户数")
    admin_users: int = Field(0, description="管理员数量")
    total_token_quota: int = Field(0, description="总配额")
    total_token_used: int = Field(0, description="总消耗")
    quota_remaining: int = Field(0, description="剩余配额")


class TokenUsageLogItem(BaseModel):
    id: int = Field(..., description="日志主键")
    user_id: int = Field(..., description="用户 ID")
    session_id: str = Field("", description="会话 ID")
    request_path: str = Field(..., description="接口路径")
    model_name: str = Field("", description="模型名称")
    prompt_tokens: int = Field(0, description="输入 token")
    completion_tokens: int = Field(0, description="输出 token")
    total_tokens: int = Field(0, description="总 token")
    stage: str = Field("", description="调用阶段")
    message_id: str = Field("", description="消息 ID")
    created_at: str = Field(..., description="创建时间")


class TokenUsageLogListResponse(BaseModel):
    total: int = Field(0, description="总条数")
    items: List[TokenUsageLogItem] = Field(default_factory=list, description="日志列表")


class AuditLogItem(BaseModel):
    id: int = Field(..., description="日志主键")
    user_id: Optional[int] = Field(None, description="用户 ID")
    user_email: str = Field("", description="用户邮箱")
    action: str = Field(..., description="动作")
    session_id: str = Field("", description="会话 ID")
    message_id: str = Field("", description="消息 ID")
    request_path: str = Field("", description="接口路径")
    status: str = Field("", description="状态")
    detail_json: str = Field("{}", description="详情 JSON")
    ip_address: str = Field("", description="IP 地址")
    created_at: str = Field(..., description="创建时间")


class AuditLogListResponse(BaseModel):
    total: int = Field(0, description="总条数")
    items: List[AuditLogItem] = Field(default_factory=list, description="审计日志列表")
