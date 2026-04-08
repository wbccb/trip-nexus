CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    nickname VARCHAR(255) NOT NULL DEFAULT '',
    role VARCHAR(32) NOT NULL DEFAULT 'user',
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    token_quota BIGINT NOT NULL DEFAULT 1000000,
    token_used BIGINT NOT NULL DEFAULT 0,
    token_version BIGINT NOT NULL DEFAULT 0,
    llm_config JSON NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    token VARCHAR(255) NOT NULL UNIQUE,
    expires_at DATETIME NOT NULL,
    used TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    used_at DATETIME NULL,
    KEY idx_password_reset_user_id (user_id),
    CONSTRAINT fk_password_reset_user_id FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS auth_blocklist (
    jti VARCHAR(255) PRIMARY KEY,
    expires_at BIGINT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS token_usage_log (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    session_id VARCHAR(128) NOT NULL DEFAULT '',
    request_path VARCHAR(255) NOT NULL,
    model_name VARCHAR(255) NOT NULL DEFAULT '',
    prompt_tokens BIGINT NOT NULL DEFAULT 0,
    completion_tokens BIGINT NOT NULL DEFAULT 0,
    total_tokens BIGINT NOT NULL DEFAULT 0,
    stage VARCHAR(64) NOT NULL DEFAULT '',
    message_id VARCHAR(128) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_token_usage_user_id (user_id),
    KEY idx_token_usage_created_at (created_at)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NULL,
    user_email VARCHAR(255) NOT NULL DEFAULT '',
    action VARCHAR(128) NOT NULL,
    session_id VARCHAR(128) NOT NULL DEFAULT '',
    message_id VARCHAR(128) NOT NULL DEFAULT '',
    request_path VARCHAR(255) NOT NULL DEFAULT '',
    status VARCHAR(64) NOT NULL DEFAULT '',
    detail_json LONGTEXT NOT NULL,
    ip_address VARCHAR(128) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_audit_log_user_id (user_id),
    KEY idx_audit_log_created_at (created_at)
);

CREATE TABLE IF NOT EXISTS rate_limit_log (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    subject_key VARCHAR(255) NOT NULL,
    bucket VARCHAR(64) NOT NULL,
    request_path VARCHAR(255) NOT NULL DEFAULT '',
    ip_address VARCHAR(128) NOT NULL DEFAULT '',
    created_at BIGINT NOT NULL,
    KEY idx_rate_limit_subject_bucket_time (subject_key, bucket, created_at)
);
