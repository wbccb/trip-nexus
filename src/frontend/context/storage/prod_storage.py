import hashlib
import json
import datetime
import redis
import mysql.connector
from typing import Optional, Dict
from src.frontend.context.entity import SessionContext, CoreEntity
from src.config import Config
from .base_storage import BaseConversationStorage

class ProdConversationStorage(BaseConversationStorage):
    """生产用存储：Redis + MySQL"""

    def __init__(self, config: Config):
        # 1. Redis连接（短期缓存）
        self.redis = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            db=config.REDIS_DB,
            password=config.REDIS_PASSWORD,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5
        )
        # 2. MySQL连接配置
        self.mysql_config = {
            'host': config.MYSQL_HOST,
            'port': config.MYSQL_PORT,
            'user': config.MYSQL_USER,
            'password': config.MYSQL_PASSWORD,
            'database': config.MYSQL_DATABASE,
            'connection_timeout': 10
        }

    def _get_mysql_connection(self):
        """获取MySQL连接"""
        return mysql.connector.connect(**self.mysql_config)

    # ------------------------------ 实现抽象方法 ------------------------------
    def generate_session_id(self, user_id: str, device_id: str) -> str:
        """生成唯一会话ID（与测试环境逻辑一致）"""
        timestamp = str(int(datetime.datetime.now().timestamp()))
        unique_str = f"{user_id}:{device_id}:{timestamp}"
        return hashlib.sha256(unique_str.encode()).hexdigest()

    def store_short_term_context(self, session_id: str, context: SessionContext):
        """存储短期上下文（Redis）"""
        key = f"session:{session_id}:short_term"
        data = {
            "messages": [msg.model_dump() for msg in context.short_term_messages[-10:]],
            "last_active": context.last_active.isoformat(),
            "message_count": context.message_count,
        }
        # Redis 2小时过期
        self.redis.setex(key, datetime.timedelta(hours=2), json.dumps(data))

    def get_short_term_context(self, session_id: str) -> Optional[Dict]:
        """获取短期上下文（Redis）"""
        key = f"session:{session_id}:short_term"
        data = self.redis.get(key)
        return json.loads(data) if data is not None else None

    def store_core_entities(self, session_id: str, entities: CoreEntity):
        """存储核心实体（Redis + MySQL）"""
        # 1. Redis存储
        redis_key = f"session:{session_id}:core_entities"
        entities_json = entities.model_dump_json()
        self.redis.setex(redis_key, datetime.timedelta(hours=2), entities_json)

        # 2. MySQL存储
        try:
            conn = self._get_mysql_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO core_entities (session_id, entities_json, last_updated)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE entities_json = %s, last_updated = %s
            """
            now = datetime.datetime.now()
            cursor.execute(query, (session_id, entities_json, now, entities_json, now))
            conn.commit()
        except Exception as e:
            print(f"MySQL存储核心实体失败: {e}")
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()

    def get_core_entities(self, session_id: str) -> Optional[CoreEntity]:
        """获取核心实体（先查Redis，再查MySQL）"""
        # 1. 先查Redis
        redis_key = f"session:{session_id}:core_entities"
        data = self.redis.get(redis_key)
        if data:
            return CoreEntity.model_validate_json(data)

        # 2. 再查MySQL
        try:
            conn = self._get_mysql_connection()
            cursor = conn.cursor(dictionary=True)
            query = "SELECT entities_json FROM core_entities WHERE session_id = %s"
            cursor.execute(query, (session_id,))
            result = cursor.fetchone()
            if result:
                entities_json = result['entities_json']
                # 缓存回Redis
                self.redis.setex(redis_key, datetime.timedelta(hours=2), entities_json)
                return CoreEntity.model_validate_json(entities_json)
        except Exception as e:
            print(f"MySQL获取核心实体失败: {e}")
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()

        return None

    def store_long_term_summary(self, session_id: str, summary: str):
        """存储长期摘要（MySQL）"""
        try:
            conn = self._get_mysql_connection()
            cursor = conn.cursor()
            query = """
            INSERT INTO long_term_summaries (session_id, summary, last_updated)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE summary = %s, last_updated = %s
            """
            now = datetime.datetime.now()
            cursor.execute(query, (session_id, summary, now, summary, now))
            conn.commit()
        except Exception as e:
            print(f"MySQL存储摘要失败: {e}")
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()

    def get_long_term_summary(self, session_id: str) -> str:
        """获取长期摘要（MySQL）"""
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
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()
        return ""