import hashlib
import json
import datetime
import redis
import mysql.connector
from typing import Optional, Dict, List
from pydantic import ValidationError
import logging
from src.frontend.context.entity import SessionContext, CoreEntity
from src.config import Config
from .base_storage import BaseConversationStorage
from src.observability import log_event

logger = logging.getLogger(__name__)

class ProdConversationStorage(BaseConversationStorage):
    """生产用存储：Redis + MySQL"""

    def __init__(self, config: Config):
        # 1. Redis连接（短期缓存）
        if config.REDIS_URL:
            self.redis = redis.from_url(
                config.REDIS_URL,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5
            )
        else:
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
        if config.MYSQL_SSL_CA:
            self.mysql_config['ssl_ca'] = config.MYSQL_SSL_CA
            self.mysql_config['ssl_verify_cert'] = True

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
            log_event(logger, logging.ERROR, "MySQL 存储核心实体失败", {"原因": str(e)})
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
            try:
                return CoreEntity.model_validate_json(data)
            except ValidationError:
                pass

        # 2. 再查MySQL
        entities_json = None
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
        except Exception as e:
            log_event(logger, logging.ERROR, "MySQL 获取核心实体失败", {"原因": str(e)})
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()

        if not entities_json and data:
             entities_json = data

        if entities_json:
            try:
                return CoreEntity.model_validate_json(entities_json)
            except ValidationError:
                try:
                    data_obj = json.loads(entities_json)
                    if isinstance(data_obj.get("travel_dates"), list):
                        data_obj["travel_dates"] = [d for d in data_obj["travel_dates"] if d]
                    return CoreEntity.model_validate(data_obj)
                except Exception as e:
                    log_event(logger, logging.ERROR, "核心实体数据修复失败", {"原因": str(e)})
                    return None

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
            log_event(logger, logging.ERROR, "MySQL 存储摘要失败", {"原因": str(e)})
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
            log_event(logger, logging.ERROR, "MySQL 获取摘要失败", {"原因": str(e)})
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()
        return ""

    def store_session(self, user_id: str, session_id: str):
        """存储会话（MySQL）"""
        try:
            conn = self._get_mysql_connection()
            cursor = conn.cursor()
            now = datetime.datetime.now()
            name = f"会话 {session_id[:8]}"
            query = """
            INSERT INTO session_list (session_id, user_id, name, update_time)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE update_time = %s
            """
            cursor.execute(query, (session_id, user_id, name, now, now))
            conn.commit()
        except Exception as e:
            log_event(logger, logging.ERROR, "MySQL 存储会话失败", {"原因": str(e)})
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()

    def get_session_list(self, user_id: str) -> List[Dict]:
        """获取会话列表（MySQL）"""
        try:
            conn = self._get_mysql_connection()
            cursor = conn.cursor(dictionary=True)
            query = "SELECT * FROM session_list WHERE user_id = %s ORDER BY update_time DESC"
            cursor.execute(query, (user_id,))
            return cursor.fetchall()
        except Exception as e:
            log_event(logger, logging.ERROR, "MySQL 获取会话列表失败", {"原因": str(e)})
            return []
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()

    def get_session_meta(self, session_id: str) -> Optional[Dict]:
        """获取会话元数据（MySQL）"""
        try:
            conn = self._get_mysql_connection()
            cursor = conn.cursor(dictionary=True)
            query = "SELECT * FROM session_list WHERE session_id = %s"
            cursor.execute(query, (session_id,))
            return cursor.fetchone()
        except Exception as e:
            log_event(logger, logging.ERROR, "MySQL 获取会话元数据失败", {"原因": str(e)})
            return None
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()

    def store_session_chat(self, session_id: str, message: str):
        """存储聊天记录（MySQL）"""
        try:
            conn = self._get_mysql_connection()
            cursor = conn.cursor()
            now = datetime.datetime.now()
            query = "INSERT INTO session_chat (session_id, message, update_time) VALUES (%s, %s, %s)"
            cursor.execute(query, (session_id, message, now))
            conn.commit()
        except Exception as e:
            log_event(logger, logging.ERROR, "MySQL 存储聊天记录失败", {"原因": str(e)})
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()

    def get_session_chat_list(self, session_id: str) -> List[str]:
        """获取聊天记录列表（MySQL）"""
        try:
            conn = self._get_mysql_connection()
            cursor = conn.cursor()
            query = "SELECT message FROM session_chat WHERE session_id = %s ORDER BY update_time ASC"
            cursor.execute(query, (session_id,))
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            log_event(logger, logging.ERROR, "MySQL 获取聊天记录失败", {"原因": str(e)})
            return []
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()

    def delete_session_chat_by_id(self, chat_id: int):
        """删除单条聊天记录（MySQL）"""
        try:
            conn = self._get_mysql_connection()
            cursor = conn.cursor()
            query = "DELETE FROM session_chat WHERE id = %s"
            cursor.execute(query, (chat_id,))
            conn.commit()
        except Exception as e:
            log_event(logger, logging.ERROR, "MySQL 删除聊天记录失败", {"原因": str(e)})
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()

    def store_trip_data(self, session_id: str, trip_data: Dict):
        """存储行程数据（MySQL）"""
        try:
            conn = self._get_mysql_connection()
            cursor = conn.cursor()
            data_json = json.dumps(trip_data, ensure_ascii=False)
            now = datetime.datetime.now()
            query = """
            INSERT INTO trip_data_store (session_id, data_json, last_updated)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE data_json = %s, last_updated = %s
            """
            cursor.execute(query, (session_id, data_json, now, data_json, now))
            conn.commit()
        except Exception as e:
            log_event(logger, logging.ERROR, "MySQL 存储行程数据失败", {"原因": str(e)})
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()

    def get_trip_data(self, session_id: str) -> Optional[Dict]:
        """获取行程数据（MySQL）"""
        try:
            conn = self._get_mysql_connection()
            cursor = conn.cursor(dictionary=True)
            query = "SELECT data_json FROM trip_data_store WHERE session_id = %s"
            cursor.execute(query, (session_id,))
            result = cursor.fetchone()
            if result:
                return json.loads(result['data_json'])
        except Exception as e:
            log_event(logger, logging.ERROR, "MySQL 获取行程数据失败", {"原因": str(e)})
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()
        return None

    def delete_session(self, session_id: str):
        redis_keys = [
            f"session:{session_id}:short_term",
            f"session:{session_id}:core_entities",
            f"session:{session_id}:trip_data",
        ]
        for key in redis_keys:
            try:
                self.redis.delete(key)
            except Exception as e:
                log_event(logger, logging.ERROR, "Redis 删除会话缓存失败", {"原因": str(e), "键": key})
        try:
            conn = self._get_mysql_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM session_chat WHERE session_id = %s", (session_id,))
            cursor.execute("DELETE FROM core_entities WHERE session_id = %s", (session_id,))
            cursor.execute("DELETE FROM long_term_summaries WHERE session_id = %s", (session_id,))
            cursor.execute("DELETE FROM trip_data_store WHERE session_id = %s", (session_id,))
            cursor.execute("DELETE FROM session_list WHERE session_id = %s", (session_id,))
            conn.commit()
        except Exception as e:
            log_event(logger, logging.ERROR, "MySQL 删除会话数据失败", {"原因": str(e), "session_id": session_id})
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()
