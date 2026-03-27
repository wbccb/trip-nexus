from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

class ChatSendRequest(BaseModel):
    user_id: Optional[str] = Field(None, description="用户唯一ID，鉴权模式下会自动从 JWT 提取")
    device_id: str = Field(..., description="设备唯一ID")
    session_id: Optional[str] = Field(None, description="会话ID，可为空以便新建")
    message: str = Field(..., description="用户输入消息")


class ChatSendResponse(BaseModel):
    session_id: str = Field(..., description="会话ID")
    response: str = Field(..., description="助手回复")
    trip_data: Optional[Dict[str, Any]] = Field(None, description="结构化行程数据")
    intent: Optional[str] = Field(None, description="意图类型")
    needs_more_info: bool = Field(False, description="是否需要补充信息")
