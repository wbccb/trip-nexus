-- name: pragma_busy_timeout
PRAGMA busy_timeout = 5000

-- name: pragma_journal_mode_wal
PRAGMA journal_mode=WAL

-- name: pragma_schema_version
PRAGMA schema_version

-- name: upsert_core_entities
INSERT INTO core_entities (session_id, entities_json, last_updated)
VALUES (?, ?, ?)
ON CONFLICT(session_id) DO UPDATE SET
    entities_json = excluded.entities_json,
    last_updated = excluded.last_updated

-- name: select_core_entities
SELECT entities_json FROM core_entities WHERE session_id = ?

-- name: upsert_long_term_summary
INSERT INTO long_term_summaries (session_id, summary, last_updated)
VALUES (?, ?, ?)
ON CONFLICT(session_id) DO UPDATE SET
    summary = excluded.summary,
    last_updated = excluded.last_updated

-- name: select_long_term_summary
SELECT summary FROM long_term_summaries WHERE session_id = ?

-- name: upsert_trip_data
INSERT INTO trip_data_store (session_id, data_json, last_updated)
VALUES (?, ?, ?)
ON CONFLICT(session_id) DO UPDATE SET
    data_json = excluded.data_json,
    last_updated = excluded.last_updated

-- name: select_trip_data
SELECT data_json FROM trip_data_store WHERE session_id = ?

-- name: upsert_session
INSERT INTO session_list (session_id, user_id, name, update_time)
VALUES (?, ?, ?, ?)
ON CONFLICT(session_id) DO UPDATE SET
    name = excluded.name,
    update_time = excluded.update_time,
    user_id = excluded.user_id

-- name: select_session_list
SELECT * FROM session_list WHERE user_id = ? ORDER BY update_time DESC

-- name: select_session_meta
SELECT * FROM session_list WHERE session_id = ?

-- name: delete_session_chat_by_session
DELETE FROM session_chat WHERE session_id = ?

-- name: delete_core_entities_by_session
DELETE FROM core_entities WHERE session_id = ?

-- name: delete_long_term_summaries_by_session
DELETE FROM long_term_summaries WHERE session_id = ?

-- name: delete_trip_data_by_session
DELETE FROM trip_data_store WHERE session_id = ?

-- name: delete_session_list_by_session
DELETE FROM session_list WHERE session_id = ?

-- name: insert_session_chat
INSERT INTO session_chat (session_id, message, update_time)
VALUES (?, ?, ?)

-- name: select_session_chat_list
SELECT message
FROM session_chat
WHERE session_id = ?
ORDER BY update_time ASC

-- name: delete_session_chat_by_id
DELETE FROM session_chat WHERE id = ?
