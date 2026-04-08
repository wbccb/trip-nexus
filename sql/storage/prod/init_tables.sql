CREATE TABLE IF NOT EXISTS session_list (
    session_id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    name VARCHAR(255) NOT NULL,
    update_time DATETIME NOT NULL,
    KEY idx_session_list_user_id (user_id),
    KEY idx_session_list_update_time (update_time)
);

CREATE TABLE IF NOT EXISTS session_chat (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    session_id VARCHAR(128) NOT NULL,
    message LONGTEXT NOT NULL,
    update_time DATETIME NOT NULL,
    KEY idx_session_chat_session_id (session_id),
    KEY idx_session_chat_update_time (update_time)
);

CREATE TABLE IF NOT EXISTS core_entities (
    session_id VARCHAR(128) PRIMARY KEY,
    entities_json LONGTEXT NOT NULL,
    last_updated DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS long_term_summaries (
    session_id VARCHAR(128) PRIMARY KEY,
    summary LONGTEXT NOT NULL,
    last_updated DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS trip_data_store (
    session_id VARCHAR(128) PRIMARY KEY,
    data_json LONGTEXT NOT NULL,
    last_updated DATETIME NOT NULL
);
