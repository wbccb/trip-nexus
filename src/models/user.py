from typing import Optional

from pydantic import BaseModel, Field


class UserRecord(BaseModel):
    id: int = Field(..., description="用户主键 ID")
    email: str = Field(..., description="登录邮箱")
    nickname: str = Field("", description="昵称")
    role: str = Field("user", description="角色 user/admin")
    status: str = Field("active", description="状态 active/banned")
    token_quota: int = Field(1000000, description="Token 额度")
    token_used: int = Field(0, description="已消耗 Token")
    token_version: int = Field(0, description="Token 版本号，用于密码更新后使旧 token 失效")
    llm_config: str = Field("{}", description="用户级大模型配置 JSON")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")


class PublicUserProfile(BaseModel):
    user_id: int = Field(..., description="用户 ID")
    email: str = Field(..., description="登录邮箱")
    nickname: str = Field("", description="昵称")
    role: str = Field("user", description="角色")
    status: str = Field("active", description="状态")
    token_quota: int = Field(1000000, description="Token 额度")
    token_used: int = Field(0, description="已消耗 Token")
    has_llm_config: bool = Field(False, description="是否已配置私有大模型")
    created_at: str = Field(..., description="注册时间")
    updated_at: str = Field(..., description="更新时间")


class PasswordResetTokenRecord(BaseModel):
    id: int = Field(..., description="重置令牌主键")
    user_id: int = Field(..., description="关联用户 ID")
    token: str = Field(..., description="重置令牌")
    expires_at: str = Field(..., description="过期时间")
    used: bool = Field(False, description="是否已使用")
    created_at: str = Field(..., description="创建时间")
    used_at: Optional[str] = Field(None, description="使用时间")
