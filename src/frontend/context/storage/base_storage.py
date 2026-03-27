from abc import ABC, abstractmethod
from typing import Optional, Dict, List
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
    def store_trip_data(self, session_id: str, trip_data: Dict):
        """存储完整的行程数据（Redis + 数据库）"""
        pass

    @abstractmethod
    def get_trip_data(self, session_id: str) -> Optional[Dict]:
        """获取完整的行程数据（Redis + 数据库）"""
        pass

    @abstractmethod
    def store_long_term_summary(self, session_id: str, summary: str):

        """存储长期摘要（数据库）"""
        pass

    @abstractmethod
    def get_long_term_summary(self, session_id: str) -> str:
        """获取长期摘要（数据库）"""
        pass

    @abstractmethod
    def store_session(self, user_id: str, session_id: str):
        """存储会话（数据库）"""
        pass

    @abstractmethod
    def get_session_list(self, user_id: str) -> List[Dict]:
        """会话数据列表（数据库）"""
        pass

    @abstractmethod
    def get_session_meta(self, session_id: str) -> Optional[Dict]:
        """获取单个会话的元数据（数据库）"""
        pass

    @abstractmethod
    def delete_session(self, session_id: str) -> None:
        """会话数据列表（数据库）"""
        pass

    @abstractmethod
    def store_session_chat(self, session_id: str, message: str):
        pass

    @abstractmethod
    def get_session_chat_list(self, session_id: str):
        pass

    @abstractmethod
    def delete_session_chat_by_id(self, chat_id: int):
        pass


