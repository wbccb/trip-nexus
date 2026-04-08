-- name: create_table
CREATE TABLE IF NOT EXISTS flow_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    intent TEXT NOT NULL,
    status TEXT NOT NULL,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    tool_count INTEGER NOT NULL DEFAULT 0,
    rag_hit INTEGER NOT NULL DEFAULT 0,
    agent_escalated INTEGER NOT NULL DEFAULT 0,
    context_count INTEGER NOT NULL DEFAULT 0,
    context_chars INTEGER NOT NULL DEFAULT 0,
    context_budget_json TEXT NOT NULL DEFAULT '{}',
    escalation_reasons_json TEXT NOT NULL DEFAULT '[]',
    error_text TEXT,
    created_at TEXT NOT NULL
)

-- name: insert_metric
INSERT INTO flow_metrics (
    message_id, session_id, user_id, device_id, mode, intent, status,
    latency_ms, tool_count, rag_hit, agent_escalated, context_count, context_chars,
    context_budget_json, escalation_reasons_json, error_text, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

-- name: count_metrics
SELECT COUNT(1) AS total FROM flow_metrics__WHERE_CLAUSE__

-- name: list_metrics
SELECT message_id, session_id, user_id, device_id, mode, intent, status,
       latency_ms, tool_count, rag_hit, agent_escalated, context_count, context_chars,
       context_budget_json, escalation_reasons_json, error_text, created_at
FROM flow_metrics
__WHERE_CLAUSE__
ORDER BY created_at DESC, id DESC
LIMIT ? OFFSET ?

-- name: summary_metrics
SELECT status, latency_ms, tool_count, rag_hit, agent_escalated
FROM flow_metrics
__WHERE_CLAUSE__
