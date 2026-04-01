from typing import Optional
from pydantic import BaseModel, Field
from src.models.user import PublicUserProfile

class AuthRegisterRequest(BaseModel):
    email: str = Field(..., description="登录邮箱")
    password: str = Field(..., description="登录密码")
    nickname: Optional[str] = Field(None, description="昵称")


class AuthLoginRequest(BaseModel):
    email: str = Field(..., description="登录邮箱")
    password: str = Field(..., description="登录密码")


class AuthRefreshResponse(BaseModel):
    token: str = Field(..., description="新的访问 token")


class AuthResponse(BaseModel):
    user_id: int = Field(..., description="用户 ID")
    token: str = Field(..., description="访问 token")
    role: str = Field(..., description="用户角色")
    profile: PublicUserProfile = Field(..., description="当前用户信息")


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., description="登录邮箱")


class ForgotPasswordResponse(BaseModel):
    message: str = Field(..., description="提示信息")
    reset_token: Optional[str] = Field(None, description="开发模式透出的重置 token")


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., description="重置 token")
    new_password: str = Field(..., description="新密码")


class SimpleMessageResponse(BaseModel):
    message: str = Field(..., description="操作结果")


class UserProfileUpdateRequest(BaseModel):
    nickname: Optional[str] = Field(None, description="昵称")


class UserLlmConfig(BaseModel):
    analysis_provider: str = Field(..., description="第 1 次调用-模型提供方")
    analysis_base_url: str = Field(..., description="第 1 次调用-Base URL")
    analysis_model_name: str = Field(..., description="第 1 次调用-模型名称")
    analysis_api_key: str = Field("", description="第 1 次调用-API Key")
    analysis_temperature: float = Field(0.7, description="第 1 次调用-温度")
    
    generation_provider: str = Field(..., description="第 2 次调用-模型提供方")
    generation_base_url: str = Field(..., description="第 2 次调用-Base URL")
    generation_model_name: str = Field(..., description="第 2 次调用-模型名称")
    generation_api_key: str = Field("", description="第 2 次调用-API Key")
    generation_temperature: float = Field(0.7, description="第 2 次调用-温度")


class UserPasswordUpdateRequest(BaseModel):
    old_password: str = Field(..., description="旧密码")
    new_password: str = Field(..., description="新密码")
