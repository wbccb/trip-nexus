# SQL 目录说明

本目录用于统一维护项目运行时使用的 SQL 文件。

当前约定：

- 所有执行型 SQL 都放在 `sql/` 目录下，不再在 Python 代码中内嵌执行
- Python 侧统一通过 `src/utils/sql_loader.py` 读取 SQL
- 表结构变更、查询调整、初始化兜底都优先修改这里的 `.sql` 文件

## 加载方式

代码统一入口：

- `src/utils/sql_loader.py`

核心方法：

- `load_sql(relative_path)`：读取整个 SQL 文件原文
- `load_sql_statements(relative_path)`：按分号切分多条 SQL，适合建表脚本
- `load_named_sql(relative_path, name)`：读取带 `-- name:` 标记的命名 SQL
- `render_named_sql(relative_path, name, replacements)`：读取命名 SQL 后做占位符替换

## 目录结构

```text
sql/
├── agent/
│   └── event_bus.sql
├── auth/
│   ├── init_mysql.sql
│   ├── init_sqlite.sql
│   └── queries.sql
├── flow/
│   └── metrics.sql
└── storage/
    ├── prod/
    │   ├── init_tables.sql
    │   └── queries.sql
    └── test/
        ├── init_tables.sql
        └── queries.sql
```

## 文件清单

### `auth/init_mysql.sql`

- 负责模块：认证系统在 MySQL/TiDB 下的建表初始化
- 表范围：`users`、`password_reset_tokens`、`auth_blocklist`、`token_usage_log`、`audit_log`、`rate_limit_log`
- 调用入口：
  - `src/auth/middleware.py` 中 `init_auth_tables()`
- 使用方式：
  - 后端启动时自动执行
  - 生产库权限不足时可手工执行

手工执行示例：

```bash
mysql -h <TiDB host> -P 4000 -u <TiDB user> -p chat_context < sql/auth/init_mysql.sql
```

### `auth/init_sqlite.sql`

- 负责模块：认证系统在本地 SQLite 下的建表初始化
- 表范围：与 `auth/init_mysql.sql` 对应的 SQLite 版本
- 调用入口：
  - `src/auth/middleware.py` 中 `init_auth_tables()`
- 使用场景：
  - 本地开发
  - 单机认证库
  - 非 MySQL 模式下的自动初始化

手工执行示例：

```bash
sqlite3 auth.db < sql/auth/init_sqlite.sql
```

### `auth/queries.sql`

- 负责模块：认证、审计、限流、管理员后台相关运行时 SQL
- 主要内容：
  - 超级管理员初始化
  - 用户查询 / 注册 / 改密 / 改昵称 / 改额度 / 改状态
  - 审计日志写入
  - Token 用量写入
  - Rate limit 计数与写入
  - Admin 用户列表 / Dashboard / Audit Logs / Token Usage
- 调用入口：
  - `src/auth/middleware.py`
  - `src/api/routes/auth.py`
  - `src/api/routes/admin.py`
  - `src/api/dependencies.py`
- 维护建议：
  - 这里的 SQL 使用 `-- name:` 分段，修改时保持名称不变
  - 带 `__WHERE_CLAUSE__` 的语句会由 `render_named_sql()` 在运行时替换

### `storage/prod/init_tables.sql`

- 负责模块：生产会话存储 MySQL/TiDB 建表初始化
- 表范围：`session_list`、`session_chat`、`core_entities`、`long_term_summaries`、`trip_data_store`
- 调用入口：
  - `src/frontend/context/storage/prod_storage.py` 中 `_ensure_mysql_tables()`
  - `src/api/app.py` 启动阶段通过 `_get_storage()` 间接触发
- 使用方式：
  - Render 首次启动时自动执行
  - 如数据库权限受限，可手工兜底执行

手工执行示例：

```bash
mysql -h <TiDB host> -P 4000 -u <TiDB user> -p chat_context < sql/storage/prod/init_tables.sql
```

### `storage/prod/queries.sql`

- 负责模块：生产会话存储运行时 SQL
- 主要内容：
  - 核心实体增删改查
  - 长期摘要增删改查
  - 会话索引增删改查
  - 聊天记录增删改查
  - 行程数据增删改查
- 调用入口：
  - `src/frontend/context/storage/prod_storage.py`
- 维护建议：
  - 这里是生产环境 MySQL 版本 SQL
  - 如表结构调整，通常需要和 `storage/prod/init_tables.sql` 一起改

### `storage/test/init_tables.sql`

- 负责模块：测试存储 SQLite 建表初始化
- 表范围：与生产会话存储对应的 SQLite 版本
- 调用入口：
  - `src/frontend/context/storage/test_storage.py` 中 `_init_sqlite_tables()`
- 使用场景：
  - 本地测试
  - SQLite 模拟生产表结构

手工执行示例：

```bash
sqlite3 trip_test.db < sql/storage/test/init_tables.sql
```

### `storage/test/queries.sql`

- 负责模块：测试存储运行时 SQL 与 SQLite `PRAGMA`
- 主要内容：
  - SQLite 连接初始化参数
  - 会话 / 摘要 / 核心实体 / 行程数据 CRUD
  - 聊天记录 CRUD
- 调用入口：
  - `src/frontend/context/storage/test_storage.py`
- 维护建议：
  - 这里是测试环境 SQLite 版本 SQL
  - 若生产 `storage/prod/queries.sql` 有变更，通常这里也要同步保持语义一致

### `flow/metrics.sql`

- 负责模块：主流程指标落库与查询
- 表范围：`flow_metrics`
- 调用入口：
  - `src/api/logic/flow.py`
- 主要内容：
  - 指标表建表
  - 指标写入
  - 明细列表查询
  - 聚合统计查询
- 存储位置说明：
  - 当前仍写入项目根目录下的 `flow_metrics.db`
  - 适合演示和本地单机使用，不应视为稳定云端持久层

手工执行示例：

```bash
sqlite3 flow_metrics.db < sql/flow/metrics.sql
```

说明：

- 该文件同时包含命名查询 SQL
- `sqlite3 < file.sql` 会顺序执行全部语句，更适合建表初始化；运行时查询仍由 Python 调用指定命名 SQL

### `agent/event_bus.sql`

- 负责模块：Agent 事件总线与快照存储
- 表范围：`agent_events`、`agent_snapshots`
- 调用入口：
  - `src/agent/event_bus.py`
- 主要内容：
  - SQLite `PRAGMA`
  - 事件表建表与索引
  - 快照表建表与索引
  - 事件 / 快照的插入、查询、清理
- 存储位置说明：
  - 当前写入项目根目录下的 `agent_events.db`
  - 适合单机调试与回放，不建议直接当多实例持久化方案

手工执行示例：

```bash
sqlite3 agent_events.db < sql/agent/event_bus.sql
```

说明：

- 和 `flow/metrics.sql` 一样，该文件既包含初始化 SQL，也包含运行时命名 SQL
- 手工执行时主要用于建表和索引初始化

## 修改建议

- 改表结构：优先修改对应 `init_*.sql` 或 `init_tables.sql`
- 改 CRUD 或报表：优先修改对应 `queries.sql`
- 改动态筛选 SQL：保留 `__WHERE_CLAUSE__` 这类占位符，继续由 Python 侧替换
- 改命名 SQL：保留 `-- name:` 的名字，避免 Python 调用入口失效

## 常见维护路径

- 改认证表结构：
  - `sql/auth/init_mysql.sql`
  - `sql/auth/init_sqlite.sql`
  - 如有对应 CRUD 变化，再改 `sql/auth/queries.sql`

- 改生产会话存储表结构：
  - `sql/storage/prod/init_tables.sql`
  - 如有读写逻辑变化，再改 `sql/storage/prod/queries.sql`

- 改测试存储逻辑：
  - `sql/storage/test/init_tables.sql`
  - `sql/storage/test/queries.sql`

- 改主流程指标字段：
  - `sql/flow/metrics.sql`
  - 同时检查 `src/api/logic/flow.py` 的字段映射

- 改 Agent 事件或快照字段：
  - `sql/agent/event_bus.sql`
  - 同时检查 `src/agent/event_bus.py` 的序列化与反序列化逻辑
