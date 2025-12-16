import hashlib
import json
from typing import Optional, Dict

import mysql
import redis
import datetime

from networkx import cut_size
from torch.ao.quantization.fx import convert

from entity import StorageConfig
from src.frontend.context.entity import SessionContext, CoreEntity


# 提供一系列方法进行数据的存储，包括：redis、mysql等数据存储接口
# 提取实体、压缩对话、更新当前存储的对话内容都在ContextManager中处理
class ContextStorage:
    def __init__(self, config: StorageConfig):
        # Redis连接用于短期缓存
        self.redis = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            db=config.REDIS_DB,
            password=config.REDIS_PASSWORD,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5
        )

        # MySQL连接配置
        self.mysql_config = {
            'host': config.MYSQL_HOST,
            'port': config.MYSQL_PORT,
            'user': config.MYSQL_USER,
            'password': config.MYSQL_PASSWORD,
            'database': config.MYSQL_DATABASE,
            'connection_timeout': 10
        }

    def _get_mysql_connection(self):
        return mysql.connector.connect(**self.mysql_config)

    def generate_session_id(self, user_id: str, device_id: str) -> str:
        """生成唯一的id"""
        timestamp = str(int(datetime.now().timestamp()))
        unique_str = f"{user_id}:{device_id}:{timestamp}"
        return hashlib.sha256(unique_str.encode()).hexdigest() #SHA-256 哈希计算，并以十六进制字符串形式返回结果

    # 短期存储(redis): 会话时间没有超时的情况下存储到redis中
    def store_short_term_context(self, session_id: str, context: SessionContext):
        """存储会话（积极）到Redis中"""
        key = f"session:{session_id}:short_term"
        data = {
            "messages": [msg.model_dump() for msg in context.short_term_messages[-10:]], # 只保留最近10轮对话
            "last_active": context.last_active.isoformat(),
            "message_count": context.message_count,
        }
        self.redis.setex(key, datetime.timedelta(hours=2), json.dumps(data)) # 2小时过期

    def get_short_term_context(self, session_id: str) -> Optional[Dict]:
        """从Redis获取短期的上下文"""
        key = f"session:{session_id}:short_term"
        data = self.redis.get(key)
        return json.loads(data) if data is not None else None

    # 核心实体存储
    def store_core_entities(self, session_id: str, entities: CoreEntity):
        """"存储核心实体到MySQL和redis中"""
        redis_key = f"session:{session_id}:core_entities"
        self.redis.setex(redis_key, datetime.timedelta(hours=2), entities.model_dump_json())

        try:
            conn = self._get_mysql_connection()
            cursor = conn.cursor()

            query = """
            INSERT INTO core_entities (session_id, entities_json, last_updated)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE entities_json = %s, last_updated = %s
            """
            entities_json = entities.model_dump_json()
            now = datetime.now()
            cursor.execute(query, (session_id, entities_json, now, entities_json, now))
            conn.commit()
        except Exception as e:
            print(f"MySQL存储核心实体失败: {e}")
        finally:
            if conn.is_connected():
                cursor.close()
                conn.close()

    def get_core_entities(self, session_id: str) -> Optional[CoreEntity]:
        """获取核心实体"""
        redis_key = f"session:{session_id}:core_entities"
        data = self.redis.get(redis_key)
        if data:
            return CoreEntity.model_validate_json(data)

        # 如果redis没有数据则从MySQL中获取数据
        try:
            conn = self._get_mysql_connection()
            cursor = conn.cursor(dictionary=True)

            query = "SELECT entities_json FROM core_entities WHERE session_id = %s"
            cursor.execute(query, (session_id,))
            result = cursor.fetchone()

            if result:
                entities = CoreEntity.model_validate_json(result)
                # 缓存到Redis中
                self.redis.setex(redis_key, datetime.timedelta(hours=2), json.dumps(entities.model_dump_json()))
                return entities
        except Exception as e:
            print(f"MySQL获取核心实体失败: {e}")
        finally:
            if conn.is_connected():
                cursor.close()
                conn.close()
        return None
        # 长期摘要存储

    def store_long_term_summary(self, session_id: str, summary: str):
        """存储长期摘要到MySQL"""
        try:
            conn = self._get_mysql_connection()
            cursor = conn.cursor()

            query = """
             INSERT INTO long_term_summaries (session_id, summary, last_updated)
             VALUES (%s, %s, %s)
             ON DUPLICATE KEY UPDATE summary = %s, last_updated = %s
             """
            now = datetime.now()
            cursor.execute(query, (session_id, summary, now, summary, now))
            conn.commit()
        except Exception as e:
            print(f"MySQL存储摘要失败: {e}")
        finally:
            if conn.is_connected():
                cursor.close()
                conn.close()

    def get_long_term_summary(self, session_id: str) -> str:
        """获取长期摘要"""
        try:
            conn = self._get_mysql_connection()
            cursor = conn.cursor(dictionary=True)

            query = "SELECT summary FROM long_term_summaries WHERE session_id = %s"
            cursor.execute(query, (session_id,))
            result = cursor.fetchone()

            if result:
                return result['summary']
        except Exception as e:
            print(f"MySQL获取摘要失败: {e}")
        finally:
            if conn.is_connected():
                cursor.close()
                conn.close()
        return ""