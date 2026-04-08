-- name: upsert_core_entities
INSERT INTO core_entities (session_id, entities_json, last_updated)
VALUES (%s, %s, %s)
ON DUPLICATE KEY UPDATE entities_json = %s, last_updated = %s

-- name: select_core_entities
SELECT entities_json FROM core_entities WHERE session_id = %s

-- name: upsert_long_term_summary
INSERT INTO long_term_summaries (session_id, summary, last_updated)
VALUES (%s, %s, %s)
ON DUPLICATE KEY UPDATE summary = %s, last_updated = %s

-- name: select_long_term_summary
SELECT summary FROM long_term_summaries WHERE session_id = %s

-- name: upsert_session
INSERT INTO session_list (session_id, user_id, name, update_time)
VALUES (%s, %s, %s, %s)
ON DUPLICATE KEY UPDATE update_time = %s

-- name: select_session_list
SELECT * FROM session_list WHERE user_id = %s ORDER BY update_time DESC

-- name: select_session_meta
SELECT * FROM session_list WHERE session_id = %s

-- name: insert_session_chat
INSERT INTO session_chat (session_id, message, update_time) VALUES (%s, %s, %s)

-- name: select_session_chat_list
SELECT message FROM session_chat WHERE session_id = %s ORDER BY update_time ASC

-- name: delete_session_chat_by_id
DELETE FROM session_chat WHERE id = %s

-- name: upsert_trip_data
INSERT INTO trip_data_store (session_id, data_json, last_updated)
VALUES (%s, %s, %s)
ON DUPLICATE KEY UPDATE data_json = %s, last_updated = %s

-- name: select_trip_data
SELECT data_json FROM trip_data_store WHERE session_id = %s

-- name: delete_session_chat_by_session
DELETE FROM session_chat WHERE session_id = %s

-- name: delete_core_entities_by_session
DELETE FROM core_entities WHERE session_id = %s

-- name: delete_long_term_summaries_by_session
DELETE FROM long_term_summaries WHERE session_id = %s

-- name: delete_trip_data_by_session
DELETE FROM trip_data_store WHERE session_id = %s

-- name: delete_session_list_by_session
DELETE FROM session_list WHERE session_id = %s
