CREATE DATABASE IF NOT EXISTS briefing_state
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE briefing_state;

-- Per-channel last-briefed timestamp
CREATE TABLE channel_state (
    channel      VARCHAR(32) PRIMARY KEY,
    last_briefed DATETIME    NOT NULL DEFAULT '1970-01-01 00:00:00',
    updated_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Gmail: individual message IDs already briefed
CREATE TABLE gmail_seen (
    message_id   VARCHAR(64) PRIMARY KEY,
    briefed_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Zendesk: per-ticket last-seen updated_at
CREATE TABLE zendesk_seen (
    ticket_id    INT          PRIMARY KEY,
    last_update  VARCHAR(32)  NOT NULL,
    briefed_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Google Chat: per-space last-seen message timestamp
CREATE TABLE gchat_seen (
    space_name   VARCHAR(128) PRIMARY KEY,
    last_message VARCHAR(32)  NOT NULL,
    briefed_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Follow-ups: people waiting on a reply from you
CREATE TABLE followups (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    person      VARCHAR(128) NOT NULL,
    summary     VARCHAR(512) NOT NULL,
    source_link VARCHAR(512) DEFAULT NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at DATETIME     DEFAULT NULL
);

-- Seed channel rows
INSERT INTO channel_state (channel) VALUES ('gmail'), ('zendesk'), ('gchat'), ('sms');
