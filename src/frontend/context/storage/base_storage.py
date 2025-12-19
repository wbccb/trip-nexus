from abc import ABC, abstractmethod
from typing import Optional, Dict
from src.frontend.context.entity import SessionContext, CoreEntity

class BaseConversationStorage(ABC):
    """存储抽象基类：定义所有存储接口"""

    @abstractmethod
    def generate_session_id(self, user_id: str, device_id: str) -> str:
        """生成唯一会话ID"""
        pass

    @abstractmethod
    def store_short_term_context(self, session_id: str, context: SessionContext):
        """存储短期会话上下文（模拟Redis）"""
        pass

    @abstractmethod
    def get_short_term_context(self, session_id: str) -> Optional[Dict]:
        """获取短期会话上下文（模拟Redis）"""
        pass

    @abstractmethod
    def store_core_entities(self, session_id: str, entities: CoreEntity):
        """存储核心实体（Redis+数据库）"""
        pass

    @abstractmethod
    def get_core_entities(self, session_id: str) -> Optional[CoreEntity]:
        """获取核心实体（先查Redis，再查数据库）"""
        pass

    @abstractmethod
    def store_long_term_summary(self, session_id: str, summary: str):
        """存储长期摘要（数据库）"""
        pass

    @abstractmethod
    def get_long_term_summary(self, session_id: str) -> str:
        """获取长期摘要（数据库）"""
        pass