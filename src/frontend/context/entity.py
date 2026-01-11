from typing import List, Dict, Optional, Any
from datetime import datetime
import tiktoken
from pydantic import BaseModel, Field
from enum import Enum


import os
from dotenv import load_dotenv

# 初始化token计算器
ENCODER = tiktoken.get_encoding("cl100k_base")

class MessageType(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

class Message(BaseModel):
    role: MessageType
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)
    is_redundant: bool = False  # 标记是否为寒暄等冗余信息
    metadata: Dict[str, Any] = Field(default_factory=dict)

class CoreEntity(BaseModel):
    destination: Optional[str] = None
    budget: Optional[float] = None
    travel_dates: Optional[List[datetime]] = None
    preferences: Dict[str, Any] = Field(default_factory=dict)
    last_updated: datetime = Field(default_factory=datetime.now)

class SessionContext(BaseModel):
    session_id: str
    user_id: str
    device_id: str
    short_term_messages: List[Message] = Field(default_factory=list)
    core_entities: CoreEntity = Field(default_factory=CoreEntity)
    long_term_summary: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    last_active: datetime = Field(default_factory=datetime.now)
    message_count: int = 0
    trip_data: Optional[Dict[str, Any]] = None  # 完整的行程数据

# 加载 .env 文件
load_dotenv()