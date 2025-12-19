import hashlib
import json
import sqlite3
import datetime
from typing import Optional, Dict
from src.frontend.context.entity import SessionContext, CoreEntity
from src.config import Config
from .base_storage import BaseConversationStorage
from .date_time_encoder import DateTimeEncoder


class TestConversationStorage(BaseConversationStorage):
    """测试用例存储： SQLite(模拟MySQL) + 内存字典(模拟Redis)"""

    def __init__(self, config: Config):
        # 1. 内存字典模拟Redis（键值对+过期时间）
        self.redis_mock: Dict[str, tuple[[str, float]]] = {}
        # 2. SQLite连接（文件型数据库，无需安装，也可改用:memory:内存模式）
        self.sqlite_conn = sqlite3.connect(
            "trip_test.db",
            check_same_thread=False,
        )
        # 初始化SQLite表结构
        self._init_sqlite_tables()

    def _init_sqlite_tables(self):
        """初始化SQLite表（与MySQL表结构一致）"""
        cursor = self.sqlite_conn.cursor()
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
        self.redis_mock[key] = (key, expire_ts)

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

    def get_short_term_context(self, session_id: str) -> Optional[Dict]:
        """获取短期上下文（模拟Redis）"""
        key = f"session:{session_id}:short_term"
        data = self._redis_get(key)
        return json.loads(data) if data is not None else None

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
            return CoreEntity.model_validate_json(data)

        # 2. 再查SQLite
        cursor = self.sqlite_conn.cursor()
        cursor.execute("SELECT entities_json FROM core_entities WHERE session_id = ?", (session_id,))
        result = cursor.fetchone()
        if result:
            entities_json = result[0]
            # 缓存回模拟Redis
            self._redis_setex(redis_key, datetime.timedelta(hours=2), entities_json)
            return CoreEntity.model_validate_json(entities_json)

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