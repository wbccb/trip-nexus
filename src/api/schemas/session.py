from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

class StartSessionRequest(BaseModel):
    """创建新会话的请求体"""
    user_id: Optional[str] = Field(None, description="用户唯一ID，鉴权模式下会自动从 JWT 提取")
    device_id: str = Field(..., description="设备唯一ID")


class StartSessionResponse(BaseModel):
    """创建新会话的响应体"""
    session_id: str = Field(..., description="新会话ID")


class SessionItem(BaseModel):
    """会话列表展示结构"""
    session_id: str = Field(..., description="会话ID")
    user_id: str = Field(..., description="用户ID")
    name: str = Field(..., description="会话名称")
    update_time: str = Field(..., description="最后更新时间")


class DeleteSessionResponse(BaseModel):
    session_id: str = Field(..., description="会话ID")
    success: bool = Field(..., description="是否删除成功")


class ChatHistoryItem(BaseModel):
    role: str = Field(..., description="消息角色")
    content: str = Field(..., description="消息内容")
    timestamp: str = Field(..., description="消息时间")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="消息元数据")
    is_redundant: bool = Field(False, description="是否为冗余消息")
