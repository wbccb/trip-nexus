import os

from src.config import Config
from .base_storage import BaseConversationStorage
from .test_storage import TestConversationStorage
from .prod_storage import ProdConversationStorage


DEFAULT_STORAGE_TYPE = "test"


def get_conversation_storage(config: Config) -> BaseConversationStorage:
    """获取存储实例(工厂方法)"""
    storage_type = str(
        os.getenv("TRIP_CONTEXT_STORAGE_TYPE", getattr(config, "TRIP_CONTEXT_STORAGE_TYPE", DEFAULT_STORAGE_TYPE))
    ).strip().lower() or DEFAULT_STORAGE_TYPE
    if storage_type == "prod":
        return ProdConversationStorage(config)
    if storage_type == "test":
        return TestConversationStorage(config)
    raise ValueError(f"Unsupported storage type: {storage_type}")


__all__ = ["BaseConversationStorage", "get_conversation_storage"]
