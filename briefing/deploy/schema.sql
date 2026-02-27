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

-- Cross-channel requests: group related items from different channels
CREATE TABLE requests (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(256) NOT NULL,
    description TEXT,
    status      VARCHAR(32)  NOT NULL DEFAULT 'open',  -- open, pending, closed
    priority    VARCHAR(16)  NOT NULL DEFAULT 'normal',  -- low, normal, high
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at   DATETIME     DEFAULT NULL
);

-- Items linked to a request (emails, tickets, chat messages, etc.)
CREATE TABLE request_items (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    request_id  INT          NOT NULL,
    channel     VARCHAR(32)  NOT NULL,
    item_id     VARCHAR(128) NOT NULL,
    label       VARCHAR(256) DEFAULT NULL,
    added_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES requests(id) ON DELETE CASCADE,
    UNIQUE KEY uq_request_channel_item (request_id, channel, item_id)
);

-- Cloud-synced memory (single row)
CREATE TABLE memory (
    id          INT PRIMARY KEY DEFAULT 1,
    content     MEDIUMTEXT   NOT NULL,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Seed channel rows
INSERT INTO channel_state (channel) VALUES ('gmail'), ('zendesk'), ('gchat'), ('sms');
