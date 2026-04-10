-- v2_orchestrator.sql
-- tac-master: Orchestrator session persistence schema (T066)
--
-- Migration strategy: additive-only, idempotent.
-- Safe to run on an existing tac_master.sqlite — no existing tables are altered.
-- Execute via: uv run orchestrator/migrate_db.py
-- Or automatically via StateStore._init_schema() on daemon startup.
--
-- SQLite pragmas (set at connection time in state_store.py, repeated here for clarity):
--   PRAGMA journal_mode = WAL;
--   PRAGMA synchronous = NORMAL;
--   PRAGMA foreign_keys = ON;

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- schema_version — tracks applied migrations
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    description TEXT    NOT NULL,
    applied_at  INTEGER NOT NULL   -- Unix epoch seconds
);

-- ---------------------------------------------------------------------------
-- orchestrator_agents — persistent orchestrator session state
--
-- One row per orchestrator session (active or archived).
-- status:  idle | executing | waiting | blocked | complete
-- archived: 0 = active, 1 = soft-deleted / historical
-- metadata: JSON text — {"model": "...", "tools": [...], "slash_commands": [...]}
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orchestrator_agents (
    id              TEXT    PRIMARY KEY,               -- 'oa-<uuid4>'
    session_id      TEXT,                              -- Claude SDK --session value
    system_prompt   TEXT,                              -- full system prompt at creation
    status          TEXT    NOT NULL DEFAULT 'idle',   -- idle|executing|waiting|blocked|complete
    working_dir     TEXT,                              -- CWD at time of spawn
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    total_cost      REAL    NOT NULL DEFAULT 0.0,      -- USD, accumulated
    archived        INTEGER NOT NULL DEFAULT 0,        -- 0=active, 1=archived
    metadata        TEXT    NOT NULL DEFAULT '{}',     -- JSON: model, tools, capabilities
    created_at      INTEGER NOT NULL,                  -- Unix epoch seconds
    updated_at      INTEGER NOT NULL                   -- Unix epoch seconds
);

CREATE INDEX IF NOT EXISTS idx_orch_agents_status   ON orchestrator_agents(status);
CREATE INDEX IF NOT EXISTS idx_orch_agents_session  ON orchestrator_agents(session_id);
CREATE INDEX IF NOT EXISTS idx_orch_agents_archived ON orchestrator_agents(archived);

-- ---------------------------------------------------------------------------
-- agent_instances — worker agent lifecycle tracking
--
-- One row per agent invocation within an orchestrator session.
-- adw_id bridges to the existing runs table (cross-table join key).
-- adw_step stores the PITER phase string (classify_iso, plan_iso, ...).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_instances (
    id                    TEXT    PRIMARY KEY,             -- 'ai-<uuid4>'
    orchestrator_agent_id TEXT    NOT NULL,                -- FK → orchestrator_agents.id
    name                  TEXT    NOT NULL,                -- human label, e.g. 'sdlc_implementor'
    model                 TEXT    NOT NULL,                -- e.g. 'claude-sonnet-4-5'
    system_prompt         TEXT,
    working_dir           TEXT,
    git_worktree          TEXT,                            -- worktree path if applicable
    status                TEXT    NOT NULL DEFAULT 'idle', -- idle|executing|waiting|blocked|complete
    session_id            TEXT,                            -- Claude SDK session for resumption
    adw_id                TEXT,                            -- links to runs.adw_id
    adw_step              TEXT,                            -- PITER phase
    input_tokens          INTEGER NOT NULL DEFAULT 0,
    output_tokens         INTEGER NOT NULL DEFAULT 0,
    total_cost            REAL    NOT NULL DEFAULT 0.0,
    archived              INTEGER NOT NULL DEFAULT 0,
    metadata              TEXT    NOT NULL DEFAULT '{}',   -- JSON: extra context
    created_at            INTEGER NOT NULL,                -- Unix epoch seconds
    updated_at            INTEGER NOT NULL,                -- Unix epoch seconds
    FOREIGN KEY(orchestrator_agent_id) REFERENCES orchestrator_agents(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_inst_orch    ON agent_instances(orchestrator_agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_inst_status  ON agent_instances(status);
CREATE INDEX IF NOT EXISTS idx_agent_inst_adw     ON agent_instances(adw_id);
CREATE INDEX IF NOT EXISTS idx_agent_inst_session ON agent_instances(session_id);

-- ---------------------------------------------------------------------------
-- chat_messages — full conversation history for session resumption
--
-- sender_type / receiver_type: user | orchestrator | agent
-- metadata JSON: {"input_tokens": N, "output_tokens": N, "cost_usd": N, "model": "..."}
-- summary is nullable — populated asynchronously by the AI summarizer.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_messages (
    id                    TEXT    PRIMARY KEY,         -- 'cm-<uuid4>'
    orchestrator_agent_id TEXT    NOT NULL,            -- FK → orchestrator_agents.id
    sender_type           TEXT    NOT NULL,            -- user | orchestrator | agent
    receiver_type         TEXT    NOT NULL,            -- user | orchestrator | agent
    message               TEXT    NOT NULL,            -- full text content
    summary               TEXT,                        -- AI-generated summary (nullable)
    agent_id              TEXT,                        -- FK → agent_instances.id (null if not agent-originated)
    session_id            TEXT,                        -- Claude SDK session ID at time of message
    metadata              TEXT    NOT NULL DEFAULT '{}', -- JSON: cost, tokens, model
    created_at            INTEGER NOT NULL,            -- Unix epoch seconds
    updated_at            INTEGER NOT NULL,            -- Unix epoch seconds
    FOREIGN KEY(orchestrator_agent_id) REFERENCES orchestrator_agents(id),
    FOREIGN KEY(agent_id)              REFERENCES agent_instances(id)
);

CREATE INDEX IF NOT EXISTS idx_chat_orch     ON chat_messages(orchestrator_agent_id);
CREATE INDEX IF NOT EXISTS idx_chat_sender   ON chat_messages(sender_type);
CREATE INDEX IF NOT EXISTS idx_chat_receiver ON chat_messages(receiver_type);
CREATE INDEX IF NOT EXISTS idx_chat_session  ON chat_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_chat_ts       ON chat_messages(created_at);

-- ---------------------------------------------------------------------------
-- system_logs — thinking blocks, tool use blocks, and app-level events
--
-- log_type:  thinking | tool_use | hook | response | app
-- level:     DEBUG | INFO | WARNING | ERROR
-- payload:   JSON — full structured data (ThinkingBlock, ToolUseBlock, etc.)
-- content:   human-readable text extracted from payload for display
-- entry_index: position within a conversation turn (ordering multi-block responses)
-- App-level logs (log_type='app') have orchestrator_agent_id=NULL, agent_id=NULL.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS system_logs (
    id                    TEXT    PRIMARY KEY,             -- 'sl-<uuid4>'
    orchestrator_agent_id TEXT,                           -- FK → orchestrator_agents.id (nullable)
    agent_id              TEXT,                           -- FK → agent_instances.id (nullable)
    session_id            TEXT,                           -- Claude SDK session
    adw_id                TEXT,                           -- links to runs.adw_id
    adw_step              TEXT,                           -- PITER phase
    level                 TEXT    NOT NULL DEFAULT 'INFO', -- DEBUG|INFO|WARNING|ERROR
    log_type              TEXT    NOT NULL,                -- thinking|tool_use|hook|response|app
    event_type            TEXT,                           -- specific event (PreToolUse, Stop, etc.)
    content               TEXT,                           -- primary text content
    payload               TEXT    NOT NULL DEFAULT '{}',  -- JSON: full structured data
    summary               TEXT,                           -- AI-generated summary (nullable)
    entry_index           INTEGER,                        -- position in conversation turn
    timestamp             INTEGER NOT NULL,               -- Unix epoch seconds
    FOREIGN KEY(orchestrator_agent_id) REFERENCES orchestrator_agents(id),
    FOREIGN KEY(agent_id)              REFERENCES agent_instances(id)
);

CREATE INDEX IF NOT EXISTS idx_syslog_orch    ON system_logs(orchestrator_agent_id);
CREATE INDEX IF NOT EXISTS idx_syslog_agent   ON system_logs(agent_id);
CREATE INDEX IF NOT EXISTS idx_syslog_session ON system_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_syslog_adw     ON system_logs(adw_id);
CREATE INDEX IF NOT EXISTS idx_syslog_type    ON system_logs(log_type);
CREATE INDEX IF NOT EXISTS idx_syslog_level   ON system_logs(level);
CREATE INDEX IF NOT EXISTS idx_syslog_ts      ON system_logs(timestamp);

-- ---------------------------------------------------------------------------
-- Initial schema_version entry (version 2 = v2 orchestrator tables)
-- INSERT OR IGNORE ensures idempotency on repeated runs.
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO schema_version (version, description, applied_at)
VALUES (2, 'Add orchestrator_agents, agent_instances, chat_messages, system_logs', strftime('%s', 'now'));
