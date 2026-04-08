-- name: pragma_journal_mode_wal
PRAGMA journal_mode=WAL

-- name: pragma_busy_timeout
PRAGMA busy_timeout = 5000

-- name: create_agent_events_table
CREATE TABLE IF NOT EXISTS agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    node TEXT,
    kind TEXT NOT NULL,
    ts REAL NOT NULL,
    detail_json TEXT NOT NULL
)

-- name: create_agent_events_thread_idx
CREATE INDEX IF NOT EXISTS idx_agent_events_thread_id ON agent_events(thread_id)

-- name: insert_agent_event
INSERT INTO agent_events (thread_id, node, kind, ts, detail_json)
VALUES (?, ?, ?, ?, ?)

-- name: list_agent_events
SELECT id, thread_id, node, kind, ts, detail_json
FROM agent_events
__WHERE_CLAUSE__
ORDER BY id ASC
LIMIT ?

-- name: clear_all_agent_events
DELETE FROM agent_events

-- name: clear_thread_agent_events
DELETE FROM agent_events WHERE thread_id = ?

-- name: latest_agent_event_sequence
SELECT MAX(id) AS max_id FROM agent_events WHERE thread_id = ?

-- name: create_agent_snapshots_table
CREATE TABLE IF NOT EXISTS agent_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    ts REAL NOT NULL,
    step TEXT,
    duration_ms INTEGER,
    payload_json TEXT,
    state_json TEXT
)

-- name: create_agent_snapshots_thread_idx
CREATE INDEX IF NOT EXISTS idx_agent_snapshots_thread_id ON agent_snapshots(thread_id)

-- name: insert_agent_snapshot
INSERT INTO agent_snapshots (thread_id, ts, step, duration_ms, payload_json, state_json)
VALUES (?, ?, ?, ?, ?, ?)

-- name: list_agent_snapshots
SELECT id, thread_id, ts, step, duration_ms, payload_json, state_json
FROM agent_snapshots
WHERE thread_id = ?
ORDER BY id ASC

-- name: latest_agent_snapshot
SELECT id, thread_id, ts, step, duration_ms, payload_json, state_json
FROM agent_snapshots
WHERE thread_id = ?
ORDER BY id DESC
LIMIT 1

-- name: clear_all_agent_snapshots
DELETE FROM agent_snapshots

-- name: clear_thread_agent_snapshots
DELETE FROM agent_snapshots WHERE thread_id = ?
