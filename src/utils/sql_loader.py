import os
from functools import lru_cache
from typing import Dict, List, Optional

from src.config import PROJECT_ROOT


def _sql_root() -> str:
    """返回项目 SQL 根目录，统一管理所有运行时 SQL 文件。"""
    return os.path.join(PROJECT_ROOT, "sql")


def _resolve_sql_path(relative_path: str) -> str:
    """把相对 SQL 路径解析为绝对路径，避免调用方重复拼接目录。"""
    return os.path.join(_sql_root(), str(relative_path or "").strip())


@lru_cache(maxsize=256)
def load_sql(relative_path: str) -> str:
    """读取单个 SQL 文件原文，并缓存结果降低重复 IO。"""
    sql_path = _resolve_sql_path(relative_path)
    with open(sql_path, "r", encoding="utf-8") as sql_file:
        content = sql_file.read().strip()
    if not content:
        raise RuntimeError(f"SQL 文件为空: {sql_path}")
    return content


@lru_cache(maxsize=128)
def load_sql_statements(relative_path: str) -> List[str]:
    """读取多语句 SQL 文件，并按分号拆成可逐条执行的语句列表。"""
    statements = [statement.strip() for statement in load_sql(relative_path).split(";") if statement.strip()]
    if not statements:
        raise RuntimeError(f"SQL 语句为空: {_resolve_sql_path(relative_path)}")
    return statements


@lru_cache(maxsize=128)
def load_named_sql_map(relative_path: str) -> Dict[str, str]:
    """解析带 `-- name:` 分段的 SQL 文件，便于按名字加载单条语句模板。"""
    result: Dict[str, str] = {}
    current_name: Optional[str] = None
    current_lines: List[str] = []
    for line in load_sql(relative_path).splitlines():
        if line.startswith("-- name:"):
            if current_name and current_lines:
                result[current_name] = "\n".join(current_lines).strip()
            current_name = str(line.split(":", 1)[1] or "").strip()
            current_lines = []
            continue
        if current_name:
            current_lines.append(line)
    if current_name and current_lines:
        result[current_name] = "\n".join(current_lines).strip()
    if not result:
        raise RuntimeError(f"未找到命名 SQL: {_resolve_sql_path(relative_path)}")
    return result


def load_named_sql(relative_path: str, name: str) -> str:
    """按名称读取 SQL 模板，统一用于 CRUD、报表和动态 where 子句场景。"""
    sql_map = load_named_sql_map(relative_path)
    if name not in sql_map:
        raise KeyError(f"SQL 模板不存在: {relative_path}::{name}")
    return sql_map[name]


def render_named_sql(relative_path: str, name: str, replacements: Optional[Dict[str, str]] = None) -> str:
    """读取命名 SQL 后做受控占位符替换，避免在 Python 中直接拼接整段 SQL。"""
    rendered_sql = load_named_sql(relative_path, name)
    for key, value in (replacements or {}).items():
        rendered_sql = rendered_sql.replace(str(key), str(value))
    return rendered_sql
