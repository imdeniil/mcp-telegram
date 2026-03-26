-- Migration: 001_initial_schema
-- Description: PostgreSQL schema for storing Telethon session data with multi-tenancy support
-- Version: 1
-- Created: 2025-03-25
--
-- This schema mirrors the Telethon SQLite session structure but with:
--   1. Multi-tenancy support (multiple Telegram accounts)
--   2. PostgreSQL-native types and features
--   3. Proper foreign key constraints and cascading deletes
--   4. Performance-optimized indexes

-- Enable UUID extension for account identifiers
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

--------------------------------------------------------------------------------
-- Schema Version Tracking
--------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description TEXT
);

--------------------------------------------------------------------------------
-- Telegram Accounts (Multi-tenancy Root)
--------------------------------------------------------------------------------

-- Each row represents a separate Telegram user account
-- This is the root entity for multi-tenancy - all other tables reference this
CREATE TABLE IF NOT EXISTS telegram_accounts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,  -- Human-readable identifier (e.g., "work", "personal")
    slug TEXT UNIQUE,  -- URL-safe identifier for API references

    -- API credentials (from my.telegram.org)
    api_id INTEGER NOT NULL,
    api_hash TEXT NOT NULL,  -- Stored as hex string

    -- Account info (populated after first login)
    phone TEXT,
    username TEXT,
    user_id BIGINT,  -- Telegram user ID (populated after auth)

    -- Lifecycle
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_connected_at TIMESTAMPTZ,

    -- Extensible storage for future features
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_telegram_accounts_slug ON telegram_accounts(slug);
CREATE INDEX IF NOT EXISTS idx_telegram_accounts_phone ON telegram_accounts(phone);
CREATE INDEX IF NOT EXISTS idx_telegram_accounts_active ON telegram_accounts(is_active) WHERE is_active = TRUE;

--------------------------------------------------------------------------------
-- Sessions Table
--------------------------------------------------------------------------------

-- Stores DC connection info and authentication key
-- Mirrors Telethon's sessions table with account_id for multi-tenancy
CREATE TABLE IF NOT EXISTS sessions (
    account_id UUID NOT NULL REFERENCES telegram_accounts(id) ON DELETE CASCADE,
    dc_id INTEGER NOT NULL,  -- Data center ID (typically 1-5)
    server_address TEXT NOT NULL,  -- e.g., '149.154.167.50'
    port INTEGER NOT NULL DEFAULT 443,
    auth_key BYTEA,  -- 256-byte MTProto authentication key (KEEP SECRET!)
    takeout_id BIGINT,  -- For Telegram data export (takeout) sessions
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (account_id, dc_id)
);

-- Useful for querying all sessions for a specific DC
CREATE INDEX IF NOT EXISTS idx_sessions_dc_id ON sessions(dc_id);

--------------------------------------------------------------------------------
-- Entities Table
--------------------------------------------------------------------------------

-- Cache of users, groups, and channels with their access hashes
-- Mirrors Telethon's entities table with account_id for multi-tenancy
-- The access hash is REQUIRED to interact with entities via Telegram API
CREATE TABLE IF NOT EXISTS entities (
    account_id UUID NOT NULL REFERENCES telegram_accounts(id) ON DELETE CASCADE,
    id BIGINT NOT NULL,  -- Telegram entity ID
    hash BIGINT NOT NULL,  -- Access hash (required for API operations)
    username TEXT,  -- @username (may change over time)
    phone TEXT,  -- Phone number for users (E.164 format)
    name TEXT,  -- Display name (first+last for users, title for groups/channels)
    date BIGINT,  -- Unix timestamp of when this entity was last seen/updated
    PRIMARY KEY (account_id, id)
);

-- Indexes for the most common entity lookup patterns
-- Partial indexes for sparse columns (WHERE column IS NOT NULL)
CREATE INDEX IF NOT EXISTS idx_entities_username
    ON entities(account_id, username)
    WHERE username IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_entities_phone
    ON entities(account_id, phone)
    WHERE phone IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_entities_name
    ON entities(account_id, name)
    WHERE name IS NOT NULL;

-- Index for cache invalidation queries (find stale entities)
CREATE INDEX IF NOT EXISTS idx_entities_date ON entities(account_id, date);

--------------------------------------------------------------------------------
-- Sent Files Table
--------------------------------------------------------------------------------

-- Cache of uploaded files to enable reuse (avoid re-uploading)
-- Mirrors Telethon's sent_files table with account_id for multi-tenancy
-- The composite primary key matches Telethon's lookup pattern
CREATE TABLE IF NOT EXISTS sent_files (
    account_id UUID NOT NULL REFERENCES telegram_accounts(id) ON DELETE CASCADE,
    md5_digest BYTEA NOT NULL,  -- MD5 hash of file content
    file_size BIGINT NOT NULL,  -- File size in bytes
    type SMALLINT NOT NULL,  -- 0=photo (InputPhoto), 1=document (InputDocument)
    id BIGINT NOT NULL,  -- Telegram file ID
    hash BIGINT NOT NULL,  -- Access hash for the file
    PRIMARY KEY (account_id, md5_digest, file_size, type)
);

-- Useful for reverse lookups (find file by Telegram ID)
CREATE INDEX IF NOT EXISTS idx_sent_files_id ON sent_files(account_id, id);

--------------------------------------------------------------------------------
-- Update State Table
--------------------------------------------------------------------------------

-- Tracks position in update streams for each entity
-- Used for catching up on missed updates after reconnection
-- Mirrors Telethon's update_state table with account_id for multi-tenancy
CREATE TABLE IF NOT EXISTS update_state (
    account_id UUID NOT NULL REFERENCES telegram_accounts(id) ON DELETE CASCADE,
    id BIGINT NOT NULL,  -- Entity ID (me for self, or channel/chat ID)
    pts BIGINT,  -- Position timestamp (position in updates)
    qts BIGINT,  -- QTS position (for qts-based updates)
    date BIGINT,  -- Unix timestamp of last known update
    seq INTEGER,  -- Sequence number
    PRIMARY KEY (account_id, id)
);

-- Index for finding entities with stale update state
CREATE INDEX IF NOT EXISTS idx_update_state_date ON update_state(account_id, date);

--------------------------------------------------------------------------------
-- Triggers
--------------------------------------------------------------------------------

-- Auto-update updated_at timestamp on telegram_accounts
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_telegram_accounts_updated_at
    BEFORE UPDATE ON telegram_accounts
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

--------------------------------------------------------------------------------
-- Initial Data
--------------------------------------------------------------------------------

-- Record schema version
INSERT INTO schema_version (version, description)
VALUES (1, 'Initial schema with multi-tenancy support')
ON CONFLICT (version) DO NOTHING;

--------------------------------------------------------------------------------
-- Documentation Comments
--------------------------------------------------------------------------------

COMMENT ON TABLE telegram_accounts IS 'Telegram accounts (multi-tenancy root). Each row represents a separate Telegram user account.';
COMMENT ON TABLE sessions IS 'Session data per account. Stores DC info and authentication keys. Mirrors Telethon sessions table.';
COMMENT ON TABLE entities IS 'Entity cache (users, groups, channels) with access hashes. Mirrors Telethon entities table.';
COMMENT ON TABLE sent_files IS 'Cache of uploaded files for reuse. Mirrors Telethon sent_files table.';
COMMENT ON TABLE update_state IS 'Update stream position tracking for catch-up. Mirrors Telethon update_state table.';
COMMENT ON TABLE schema_version IS 'Database schema version history for migrations.';

COMMENT ON COLUMN telegram_accounts.api_hash IS 'Telegram API hash from my.telegram.org - SENSITIVE';
COMMENT ON COLUMN sessions.auth_key IS '256-byte MTProto authentication key - HIGHLY SENSITIVE, NEVER share';
COMMENT ON COLUMN entities.hash IS 'Telegram access hash required to interact with the entity';
COMMENT ON COLUMN sent_files.type IS 'File type: 0=photo (InputPhoto), 1=document (InputDocument)';
COMMENT ON COLUMN update_state.pts IS 'Position in updates (pts = position timestamp)';
COMMENT ON COLUMN update_state.qts IS 'Position in qts-based updates';
COMMENT ON COLUMN update_state.seq IS 'Sequence number for ordered updates';
