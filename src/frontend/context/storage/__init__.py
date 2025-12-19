from src.config import Config
from .base_storage import BaseConversationStorage
from .test_storage import TestConversationStorage
from .prod_storage import ProdConversationStorage


# 配置开关
STORAGE_TYPE = "test"


def get_conversation_storage(config: Config) -> BaseConversationStorage:
    """获取存储实例(工厂方法)"""
    if STORAGE_TYPE == "prod":
        return ProdConversationStorage(config)
    elif STORAGE_TYPE == "test":
        return TestConversationStorage(config)
    else:
        raise ValueError(f"Unsupported storage type: {STORAGE_TYPE}")


__all__ = ["BaseConversationStorage", "get_conversation_storage"]