import hashlib
import json
import sqlite3
import datetime
import time
from typing import Optional, Dict, List
from pydantic import ValidationError
import logging
from src.frontend.context.entity import SessionContext, CoreEntity
from src.config import Config
from .base_storage import BaseConversationStorage
from .date_time_encoder import DateTimeEncoder
from src.observability import log_event

logger = logging.getLogger(__name__)


class TestConversationStorage(BaseConversationStorage):
    """测试用例存储： SQLite(模拟MySQL) + 内存字典(模拟Redis)"""

    def __init__(self, config: Config):
        # 1. 内存字典模拟Redis（键值对+过期时间）
        self.redis_mock: Dict[str, tuple[[str, float]]] = {}
        # 2. SQLite连接（文件型数据库，无需安装，也可改用:memory:内存模式）
        self.sqlite_conn = sqlite3.connect(
            "trip_test.db",
            check_same_thread=False,
            timeout=5,
        )
        self.sqlite_conn.row_factory = sqlite3.Row
        self.sqlite_conn.execute("PRAGMA journal_mode=WAL")
        self.sqlite_conn.execute("PRAGMA busy_timeout = 5000")
        # 初始化SQLite表结构
        self._init_sqlite_tables()

    def _init_sqlite_tables(self):
        """初始化SQLite表（与MySQL表结构一致）"""
        cursor = self.sqlite_conn.cursor()

        # 会话数据
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_list (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXE NOT NULL,
            update_time TIMESTAMP NOT NULL
        )
        """)

        # 聊天数据
        cursor.execute("""
       CREATE TABLE IF NOT EXISTS session_chat (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           session_id TEXT NOT NULL,
           message TEXT NOT NULL,
           update_time TIMESTAMP NOT NULL
       )
       """)

        # 核心实体表（对应MySQL的core_entities）
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS core_entities (
            session_id TEXT PRIMARY KEY,
            entities_json TEXT NOT NULL,
            last_updated TIMESTAMP NOT NULL
        )
        """)
        # 长期摘要表（对应MySQL的long_term_summaries）
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS long_term_summaries (
            session_id TEXT PRIMARY KEY,
            summary TEXT NOT NULL,
            last_updated TIMESTAMP NOT NULL
        )
        """)
        self.sqlite_conn.commit()

    def _is_key_expired(self, key: str) -> bool:
        """检查redis是否过期"""
        if key not in self.redis_mock:
            return True
        _, expire_ts = self.redis_mock[key]
        return datetime.datetime.now().timestamp() > expire_ts

    def _redis_setex(self, key: str, expire: datetime.timedelta, value: str):
        expire_ts = datetime.datetime.now().timestamp() + expire.total_seconds()
        self.redis_mock[key] = (value, expire_ts)

    def _redis_get(self, key: str) -> Optional[str]:
        if self._is_key_expired(key):
            if key in self.redis_mock:
                del self.redis_mock[key]
            return None
        return self.redis_mock[key][0] if key in self.redis_mock else None

    def generate_session_id(self, user_id: str, device_id: str) -> str:
        """生成唯一会话ID（逻辑与生产环境一致）"""
        timestamp = str(int(datetime.datetime.now().timestamp()))
        unique_str = f"{user_id}:{device_id}:{timestamp}"
        return hashlib.sha256(unique_str.encode()).hexdigest()

    def store_short_term_context(self, session_id: str, context: SessionContext):
        """存储短期上下文（模拟Redis）"""
        key = f"session:{session_id}:short_term"
        # 序列化SessionContext（适配Pydantic v2的model_dump）
        data = {
            "messages": [msg.model_dump() for msg in context.short_term_messages[-10:]],
            "last_active": context.last_active.isoformat(),
            "message_count": context.message_count,
        }
        # 模拟Redis 2小时过期
        self._redis_setex(key, datetime.timedelta(hours=2), json.dumps(data, cls=DateTimeEncoder))



    def get_short_term_context(self, session_id: str) -> List[Dict]:
        """获取短期上下文（模拟Redis）"""
        key = f"session:{session_id}:short_term"
        data = self._redis_get(key)
        if not data:
            return []

        parsed_data = json.loads(data)
        if isinstance(parsed_data, dict):
            return parsed_data
        else:
            return []

    def store_core_entities(self, session_id: str, entities: CoreEntity):
        """存储核心实体（模拟Redis + SQLite）"""
        # 1. 模拟Redis存储（短期缓存）
        redis_key = f"session:{session_id}:core_entities"
        entities_json = entities.model_dump_json()
        self._redis_setex(redis_key, datetime.timedelta(hours=2), entities_json)

        # 2. SQLite存储（模拟MySQL）
        now = datetime.datetime.now().isoformat()
        cursor = self.sqlite_conn.cursor()
        # SQLite的UPSERT语法（对应MySQL的ON DUPLICATE KEY UPDATE）
        cursor.execute("""
        INSERT INTO core_entities (session_id, entities_json, last_updated)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            entities_json = excluded.entities_json,
            last_updated = excluded.last_updated
        """, (session_id, entities_json, now))
        self.sqlite_conn.commit()

    def get_core_entities(self, session_id: str) -> Optional[CoreEntity]:
        """获取核心实体（先查模拟Redis，再查SQLite）"""
        # 1. 先查模拟Redis
        redis_key = f"session:{session_id}:core_entities"
        data = self._redis_get(redis_key)
        if data:
            try:
                return CoreEntity.model_validate_json(data)
            except ValidationError:
                pass  # Redis数据坏了，尝试查数据库或修复

        # 2. 再查SQLite
        cursor = self.sqlite_conn.cursor()
        cursor.execute("SELECT entities_json FROM core_entities WHERE session_id = ?", (session_id,))
        result = cursor.fetchone()
        
        entities_json = None
        if result:
            entities_json = result[0]
        elif data:
            # 如果数据库没查到但Redis有（虽然前面校验失败了），尝试修复Redis数据
            entities_json = data

        if entities_json:
            # 尝试解析并修复
            try:
                return CoreEntity.model_validate_json(entities_json)
            except ValidationError:
                try:
                    # 尝试修复常见的数据错误
                    data_obj = json.loads(entities_json)
                    if isinstance(data_obj.get("travel_dates"), list):
                        # 过滤掉None值
                        data_obj["travel_dates"] = [d for d in data_obj["travel_dates"] if d]
                    
                    entity = CoreEntity.model_validate(data_obj)
                    
                    # 修复成功后，更新回缓存和数据库（为了下次读取）
                    # 注意：这里可能需要谨慎写入，但为了自愈，写入是好的
                    # self.store_core_entities(session_id, entity) 
                    # 暂时不自动回写，只返回修复后的对象，下次更新时会覆盖
                    
                    # 仍然缓存回Redis（修复后的数据不好直接存回Redis raw json，除非重序列化）
                    # 这里保持原逻辑：如果有result才缓存。
                    if result:
                         self._redis_setex(redis_key, datetime.timedelta(hours=2), entities_json)
                    
                    return entity
                except Exception as e:
                    log_event(logger, logging.ERROR, "测试存储核心实体修复失败", {"原因": str(e)})
                    return None

        return None

    def store_long_term_summary(self, session_id: str, summary: str):
        """存储长期摘要（SQLite模拟MySQL）"""
        now = datetime.datetime.now().isoformat()
        cursor = self.sqlite_conn.cursor()
        cursor.execute("""
        INSERT INTO long_term_summaries (session_id, summary, last_updated)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            summary = excluded.summary,
            last_updated = excluded.last_updated
        """, (session_id, summary, now))
        self.sqlite_conn.commit()

    def get_long_term_summary(self, session_id: str) -> str:
        """获取长期摘要（SQLite模拟MySQL）"""
        cursor = self.sqlite_conn.cursor()
        cursor.execute("SELECT summary FROM long_term_summaries WHERE session_id = ?", (session_id,))
        result = cursor.fetchone()
        return result[0] if result else ""

    def store_trip_data(self, session_id: str, trip_data: Dict):
        """存储行程数据（Redis + SQLite）"""
        # 1. Redis 缓存
        key = f"session:{session_id}:trip_data"
        self._redis_setex(key, datetime.timedelta(hours=2), json.dumps(trip_data, ensure_ascii=False))

        # 2. SQLite 持久化
        # 这里为了简化，我们复用 core_entities 表结构，或者新建表。
        # 考虑到这是一个新功能，我们在 _init_sqlite_tables 中添加 trip_data 表
        # 但由于 _init_sqlite_tables 只在初始化调用，我们这里动态检查/创建表比较安全，
        # 或者假设表已存在（需要修改 _init_sqlite_tables）
        # 为方便起见，我们在 store 时检查并创建表（仅供测试用）
        cursor = self.sqlite_conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS trip_data_store (
            session_id TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            last_updated TIMESTAMP NOT NULL
        )
        """)
        
        now = datetime.datetime.now().isoformat()
        data_json = json.dumps(trip_data, ensure_ascii=False)
        cursor.execute("""
        INSERT INTO trip_data_store (session_id, data_json, last_updated)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            data_json = excluded.data_json,
            last_updated = excluded.last_updated
        """, (session_id, data_json, now))
        self.sqlite_conn.commit()

    def get_trip_data(self, session_id: str) -> Optional[Dict]:
        """获取行程数据（Redis + SQLite）"""
        # 1. Redis
        key = f"session:{session_id}:trip_data"
        data = self._redis_get(key)
        if data:
            try:
                return json.loads(data)
            except:
                pass

        # 2. SQLite
        cursor = self.sqlite_conn.cursor()
        # 确保表存在
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS trip_data_store (
            session_id TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            last_updated TIMESTAMP NOT NULL
        )
        """)
        cursor.execute("SELECT data_json FROM trip_data_store WHERE session_id = ?", (session_id,))
        result = cursor.fetchone()
        
        if result:
            try:
                trip_data = json.loads(result[0])
                # 回填 Redis
                self._redis_setex(key, datetime.timedelta(hours=2), result[0])
                return trip_data
            except Exception as e:
                log_event(logger, logging.ERROR, "测试存储解析行程数据失败", {"原因": str(e), "session_id": session_id})
                return None
        return None

    def store_session(self, user_id: str, session_id: str):
        update_time = datetime.datetime.now().isoformat()
        name = f"名称{session_id}-{update_time}"
        cursor = self.sqlite_conn.cursor()
        cursor.execute("""
               INSERT INTO session_list (session_id, user_id, name, update_time)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                   name = excluded.name,
                   update_time = excluded.update_time,
                   user_id = excluded.user_id
               """, (session_id, user_id, name, update_time))
        self.sqlite_conn.commit()

    def get_session_list(self, user_id: str):
        cursor = self.sqlite_conn.cursor()
        # 根据更新时间倒序排列，更新时间较新的排在最上面
        cursor.execute("SELECT * FROM session_list WHERE user_id = ? ORDER BY update_time DESC", (user_id,))
        return cursor.fetchall()

    def delete_session(self, session_id: str):
        cursor = self.sqlite_conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS trip_data_store (
            session_id TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            last_updated TIMESTAMP NOT NULL
        )
        """)
        cursor.execute("DELETE FROM session_chat WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM core_entities WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM long_term_summaries WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM trip_data_store WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM session_list WHERE session_id = ?", (session_id,))
        self.sqlite_conn.commit()
        redis_keys = [
            f"session:{session_id}:short_term",
            f"session:{session_id}:core_entities",
            f"session:{session_id}:trip_data",
        ]
        for key in redis_keys:
            if key in self.redis_mock:
                del self.redis_mock[key]

    def store_session_chat(self, session_id: str, message: str):
        update_time = datetime.datetime.now().isoformat()
        cursor = self.sqlite_conn.cursor()
        cursor.execute("""
            INSERT INTO session_chat (session_id, message, update_time)
            VALUES (?, ?, ?)
        """, (session_id, message, update_time))
        self.sqlite_conn.commit()

    def get_session_chat_list(self, session_id: str) -> List[str]:
        """获取会话的所有聊天记录（按时间正序排列）"""
        last_error = None
        for _ in range(3):
            try:
                cursor = self.sqlite_conn.cursor()
                cursor.execute("""
                    SELECT message
                    FROM session_chat
                    WHERE session_id = ?
                    ORDER BY update_time ASC
                """, (session_id,))
                return [row[0] for row in cursor.fetchall()]
            except sqlite3.OperationalError as exc:
                last_error = exc
                time.sleep(0.1)
        raise last_error

    def delete_session_chat_by_id(self, chat_id: int):
        cursor = self.sqlite_conn.cursor()
        cursor.execute("DELETE FROM session_chat WHERE id = ?", (chat_id,))
        self.sqlite_conn.commit()
