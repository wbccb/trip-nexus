CREATE TABLE IF NOT EXISTS session_list (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    update_time TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS session_chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    message TEXT NOT NULL,
    update_time TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS core_entities (
    session_id TEXT PRIMARY KEY,
    entities_json TEXT NOT NULL,
    last_updated TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS long_term_summaries (
    session_id TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    last_updated TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS trip_data_store (
    session_id TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    last_updated TIMESTAMP NOT NULL
);
