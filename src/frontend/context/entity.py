from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
import json
import hashlib
import tiktoken
from pydantic import BaseModel, Field
from enum import Enum
import redis
import mysql.connector
from mysql.connector import Error

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

# 加载 .env 文件
load_dotenv()


class StorageConfig:
    # Redis 配置
    REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
    REDIS_PORT = int(os.getenv('REDIS_PORT', '6379'))
    REDIS_DB = int(os.getenv('REDIS_DB', '0'))
    REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', None)

    # MySQL 配置
    MYSQL_HOST = os.getenv('MYSQL_HOST', 'localhost')
    MYSQL_PORT = int(os.getenv('MYSQL_PORT', '3306'))
    MYSQL_USER = os.getenv('MYSQL_USER', 'root')
    MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', '')
    MYSQL_DATABASE = os.getenv('MYSQL_DATABASE', 'chat_context')

    # 业务配置
    MAX_SHORT_TERM_MESSAGES = int(os.getenv('MAX_SHORT_TERM_MESSAGES', '10'))
    MAX_CONTEXT_TOKENS = int(os.getenv('MAX_CONTEXT_TOKENS', '4096'))
    SESSION_EXPIRY_HOURS = int(os.getenv('SESSION_EXPIRY_HOURS', '2'))
    CORE_ENTITIES_EXPIRY_HOURS = int(os.getenv('CORE_ENTITIES_EXPIRY_HOURS', '24'))