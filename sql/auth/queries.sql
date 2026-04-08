-- name: select_admin_user_id_by_email
SELECT id FROM users WHERE email = ?

-- name: insert_super_admin
INSERT INTO users (email, password_hash, nickname, role, status, token_quota, token_used, token_version)
VALUES (?, ?, ?, 'admin', 'active', 999999999, 0, 0)

-- name: promote_user_to_admin
UPDATE users SET role = 'admin' WHERE email = ?

-- name: sqlite_pragma_audit_log_columns
PRAGMA table_info(audit_log)

-- name: sqlite_alter_audit_log_add_session_id
ALTER TABLE audit_log ADD COLUMN session_id TEXT NOT NULL DEFAULT ''

-- name: sqlite_alter_audit_log_add_message_id
ALTER TABLE audit_log ADD COLUMN message_id TEXT NOT NULL DEFAULT ''

-- name: sqlite_pragma_users_columns
PRAGMA table_info(users)

-- name: sqlite_alter_users_add_llm_config
ALTER TABLE users ADD COLUMN llm_config TEXT NOT NULL DEFAULT '{}'

-- name: select_user_by_id
SELECT * FROM users WHERE id = ?

-- name: select_user_by_email
SELECT * FROM users WHERE email = ?

-- name: count_users
SELECT COUNT(1) AS total FROM users

-- name: delete_expired_blocklist
DELETE FROM auth_blocklist WHERE expires_at <= ?

-- name: select_blocked_token
SELECT 1 FROM auth_blocklist WHERE jti = ? LIMIT 1

-- name: upsert_blocked_token
INSERT OR REPLACE INTO auth_blocklist (jti, expires_at) VALUES (?, ?)

-- name: insert_user
INSERT INTO users (email, password_hash, nickname, role, status, token_quota, token_used, token_version)
VALUES (?, ?, ?, ?, 'active', 1000000, 0, 0)

-- name: insert_password_reset_token
INSERT INTO password_reset_tokens (user_id, token, expires_at, used) VALUES (?, ?, ?, 0)

-- name: select_password_reset_token
SELECT * FROM password_reset_tokens
WHERE token = ? AND used = 0
ORDER BY id DESC
LIMIT 1

-- name: update_user_password_and_token_version
UPDATE users
SET password_hash = ?, token_version = token_version + 1, updated_at = CURRENT_TIMESTAMP
WHERE id = ?

-- name: update_password_reset_token_used
UPDATE password_reset_tokens SET used = 1, used_at = ? WHERE id = ?

-- name: update_user_nickname
UPDATE users SET nickname = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?

-- name: update_user_llm_config
UPDATE users SET llm_config = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?

-- name: update_user_status
UPDATE users SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?

-- name: update_user_quota
UPDATE users SET token_quota = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?

-- name: list_users_count
SELECT COUNT(1) AS total FROM users __WHERE_CLAUSE__

-- name: list_users_page
SELECT id, email, nickname, role, status, token_quota, token_used, created_at, updated_at
FROM users
__WHERE_CLAUSE__
ORDER BY created_at DESC, id DESC
LIMIT ? OFFSET ?

-- name: admin_dashboard
SELECT
    COUNT(1) AS total_users,
    SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active_users,
    SUM(CASE WHEN status = 'banned' THEN 1 ELSE 0 END) AS banned_users,
    SUM(CASE WHEN role = 'admin' THEN 1 ELSE 0 END) AS admin_users,
    COALESCE(SUM(token_quota), 0) AS total_token_quota,
    COALESCE(SUM(token_used), 0) AS total_token_used
FROM users

-- name: token_usage_count_by_user
SELECT COUNT(1) AS total FROM token_usage_log WHERE user_id = ?

-- name: token_usage_list_by_user
SELECT id, user_id, session_id, request_path, model_name, prompt_tokens,
       completion_tokens, total_tokens, stage, message_id, created_at
FROM token_usage_log
WHERE user_id = ?
ORDER BY id DESC
LIMIT ?

-- name: audit_logs_count
SELECT COUNT(1) AS total FROM audit_log __WHERE_CLAUSE__

-- name: audit_logs_list
SELECT id, user_id, user_email, action, session_id, message_id,
       request_path, status, detail_json, ip_address, created_at
FROM audit_log
__WHERE_CLAUSE__
ORDER BY id DESC
LIMIT ?

-- name: insert_audit_log
INSERT INTO audit_log (
    user_id, user_email, action, session_id, message_id,
    request_path, status, detail_json, ip_address
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)

-- name: insert_token_usage_log
INSERT INTO token_usage_log (
    user_id, session_id, request_path, model_name,
    prompt_tokens, completion_tokens, total_tokens, stage, message_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)

-- name: increment_user_token_used
UPDATE users SET token_used = token_used + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?

-- name: delete_old_rate_limit_log
DELETE FROM rate_limit_log WHERE created_at < ?

-- name: count_rate_limit_window
SELECT COUNT(1) AS total FROM rate_limit_log
WHERE subject_key = ? AND bucket = ? AND created_at >= ?

-- name: insert_rate_limit_log
INSERT INTO rate_limit_log (subject_key, bucket, request_path, ip_address, created_at)
VALUES (?, ?, ?, ?, ?)
