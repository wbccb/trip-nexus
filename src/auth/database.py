import os
import sqlite3
import threading
from typing import Any, Dict, Optional
import mysql.connector

from src.config import Config

AUTH_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "auth.db"))
_AUTH_DB_LOCK = threading.Lock()

class AuthDBCursor:
    def __init__(self, cursor, backend):
        self.cursor = cursor
        self.backend = backend

    def execute(self, query, params=None):
        if self.backend == 'mysql' and '?' in query:
            query = query.replace('?', '%s')
        
        # also handle INSERT OR REPLACE -> INSERT ... ON DUPLICATE KEY UPDATE or REPLACE INTO
        if self.backend == 'mysql' and 'INSERT OR REPLACE' in query:
            query = query.replace('INSERT OR REPLACE', 'REPLACE')
            
        if params:
            self.cursor.execute(query, params)
        else:
            self.cursor.execute(query)

    def fetchone(self):
        row = self.cursor.fetchone()
        if not row:
            return None
        # MySQL cursor 已配置 dictionary=True，此处统一转换为 dict 格式以兼容业务逻辑
        if self.backend == 'sqlite':
            return dict(row)
        return row

    def fetchall(self):
        rows = self.cursor.fetchall()
        if not rows:
            return []
        # MySQL cursor 已配置 dictionary=True，此处统一转换为 dict 格式以兼容业务逻辑
        if self.backend == 'sqlite':
            return [dict(r) for r in rows]
        return rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        self.cursor.close()

    @property
    def lastrowid(self):
        return self.cursor.lastrowid

class AuthDBConnection:
    def __init__(self, conn, backend):
        self.conn = conn
        self.backend = backend

    def cursor(self):
        if self.backend == 'mysql':
            return AuthDBCursor(self.conn.cursor(dictionary=True), self.backend)
        else:
            return AuthDBCursor(self.conn.cursor(), self.backend)

    def commit(self):
        self.conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        self.conn.close()

def get_auth_db_connection() -> AuthDBConnection:
    config = Config()
    if config.AUTH_DB_BACKEND == 'mysql':
        mysql_config = {
            'host': config.MYSQL_HOST,
            'port': config.MYSQL_PORT,
            'user': config.MYSQL_USER,
            'password': config.MYSQL_PASSWORD,
            'database': config.MYSQL_DATABASE,
            'connection_timeout': 10
        }
        if config.MYSQL_SSL_CA:
            mysql_config['ssl_ca'] = config.MYSQL_SSL_CA
            mysql_config['ssl_verify_cert'] = True
        conn = mysql.connector.connect(**mysql_config)
        return AuthDBConnection(conn, 'mysql')
    else:
        conn = sqlite3.connect(AUTH_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return AuthDBConnection(conn, 'sqlite')
