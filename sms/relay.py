#!/usr/bin/env python3
"""
SMS/iMessage Relay Service

Reads new inbound messages from macOS Messages (chat.db)
and forwards them to a Google Chat webhook or email.

Designed to run as a launchd service on macOS.

Usage:
    python3 relay.py              # Run as daemon (continuous polling)
    python3 relay.py --once       # Single poll cycle, then exit
    python3 relay.py --init       # Create default config and exit
    python3 relay.py --seed       # Skip all existing messages (start fresh)
    python3 relay.py --status     # Show current state and config
"""

import argparse
import json
import logging
import smtplib
import sqlite3
import sys
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Seconds between Unix epoch (1970-01-01) and Apple epoch (2001-01-01)
APPLE_EPOCH_OFFSET = 978307200

CONFIG_DIR = Path.home() / ".sms-relay"
CONFIG_FILE = CONFIG_DIR / "config.json"
STATE_FILE = CONFIG_DIR / "state.json"
LOG_FILE = CONFIG_DIR / "relay.log"

DEFAULT_CONFIG = {
    "chat_db": str(Path.home() / "Library/Messages/chat.db"),

    # "gchat_webhook" or "email"
    "forward_method": "gchat_webhook",
    "gchat_webhook_url": "",

    "email": {
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "username": "",
        "password": "",
        "from_addr": "",
        "to_addr": "",
    },

    "poll_interval_seconds": 60,
    "max_messages_per_run": 50,

    "filters": {
        "allowed_senders": [],
        "blocked_senders": [],
        "business_hours_only": False,
        "business_hours": {"start": 8, "end": 18},
        "business_days": [0, 1, 2, 3, 4],
    },

    "digest_mode": False,
    "digest_interval_minutes": 30,
}

# ---------------------------------------------------------------------------
# Config & state
# ---------------------------------------------------------------------------


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
        print(f"Created default config at {CONFIG_FILE}")
        print("Edit it with your Google Chat webhook URL, then run again.")
        sys.exit(1)
    return json.loads(CONFIG_FILE.read_text())


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_rowid": 0}


def save_state(state: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Apple timestamp handling
# ---------------------------------------------------------------------------


def apple_ts_to_datetime(apple_ts) -> Optional[datetime]:
    """Convert Apple CoreData timestamp to UTC datetime.

    macOS Sierra+ stores nanoseconds since 2001-01-01.
    Older versions stored seconds. We detect by magnitude.
    """
    if apple_ts is None or apple_ts == 0:
        return None
    if apple_ts > 1e15:
        unix_ts = (apple_ts / 1e9) + APPLE_EPOCH_OFFSET
    else:
        unix_ts = apple_ts + APPLE_EPOCH_OFFSET
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc)


# ---------------------------------------------------------------------------
# Database queries
# ---------------------------------------------------------------------------


def fetch_new_messages(db_path: str, last_rowid: int, limit: int = 50,
                       relay_number: str = "") -> list[dict]:
    """Read new inbound messages from chat.db after last_rowid.

    Opens the database in read-only mode to avoid locking issues with
    the Messages app.  Uses ROWID as a monotonically-increasing cursor
    which is more reliable than Apple's nanosecond timestamps.

    Query joins:
        message -> handle  (sender phone/email)
        message -> chat_message_join -> chat  (chat identifier for group names)
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    where = """
        WHERE m.is_from_me = 0
          AND m.ROWID > ?
          AND m.text IS NOT NULL
          AND m.text != ''
    """
    params = [last_rowid]

    if relay_number:
        where += "  AND c.account_login LIKE ?\n"
        params.append(f"%{relay_number}%")

    params.append(limit)

    try:
        rows = conn.execute(
            f"""
            SELECT
                m.ROWID,
                m.guid,
                m.text,
                m.date,
                m.service,
                m.is_audio_message,
                m.cache_has_attachments,
                h.id          AS sender,
                c.display_name AS chat_name
            FROM message m
            LEFT JOIN handle h
                   ON m.handle_id = h.ROWID
            LEFT JOIN chat_message_join cmj
                   ON cmj.message_id = m.ROWID
            LEFT JOIN chat c
                   ON c.ROWID = cmj.chat_id
            {where}
            ORDER BY m.ROWID ASC
            LIMIT ?
            """,
            params,
        ).fetchall()

        messages = []
        for r in rows:
            messages.append(
                {
                    "rowid": r["ROWID"],
                    "guid": r["guid"],
                    "text": r["text"],
                    "date": apple_ts_to_datetime(r["date"]),
                    "service": r["service"] or "SMS",
                    "sender": r["sender"] or "unknown",
                    "chat_name": r["chat_name"] or "",
                    "has_attachment": bool(r["cache_has_attachments"]),
                }
            )
        return messages
    finally:
        conn.close()


def get_max_rowid(db_path: str) -> int:
    """Return the current maximum ROWID in the message table."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT MAX(ROWID) FROM message").fetchone()
        return row[0] or 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def apply_filters(messages: list[dict], filters: dict) -> list[dict]:
    """Apply sender and business-hours filters."""
    allowed = set(filters.get("allowed_senders") or [])
    blocked = set(filters.get("blocked_senders") or [])
    bh_only = filters.get("business_hours_only", False)
    bh = filters.get("business_hours", {"start": 8, "end": 18})
    bdays = set(filters.get("business_days", [0, 1, 2, 3, 4]))

    result = []
    for msg in messages:
        if allowed and msg["sender"] not in allowed:
            continue
        if msg["sender"] in blocked:
            continue
        if bh_only and msg["date"]:
            lt = msg["date"].astimezone()
            if lt.weekday() not in bdays:
                continue
            if not (bh["start"] <= lt.hour < bh["end"]):
                continue
        result.append(msg)
    return result


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_message(msg: dict) -> str:
    ts = msg["date"].strftime("%Y-%m-%d %H:%M") if msg["date"] else "unknown"
    svc = "iMessage" if msg["service"] == "iMessage" else "SMS"
    group = f" [{msg['chat_name']}]" if msg["chat_name"] else ""
    attachment = " +attachment" if msg["has_attachment"] else ""
    return f"[{svc}] {msg['sender']}{group} ({ts}{attachment}):\n{msg['text']}"


def format_digest(messages: list[dict]) -> str:
    header = f"SMS/iMessage Digest - {len(messages)} new message(s)"
    body = "\n\n".join(format_message(m) for m in messages)
    return f"{header}\n{'=' * len(header)}\n\n{body}"


# ---------------------------------------------------------------------------
# Forwarding
# ---------------------------------------------------------------------------


def send_gchat_webhook(webhook_url: str, text: str) -> bool:
    """POST a text message to a Google Chat incoming webhook."""
    payload = json.dumps({"text": text}).encode("utf-8")
    req = Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except URLError as exc:
        logging.error("Webhook POST failed: %s", exc)
        return False


def send_email(cfg: dict, subject: str, body: str) -> bool:
    """Send a message via SMTP (Gmail app-password friendly)."""
    ecfg = cfg["email"]
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = ecfg["from_addr"]
    msg["To"] = ecfg["to_addr"]
    try:
        with smtplib.SMTP(ecfg["smtp_server"], ecfg["smtp_port"]) as srv:
            srv.starttls()
            srv.login(ecfg["username"], ecfg["password"])
            srv.send_message(msg)
        return True
    except Exception as exc:
        logging.error("Email send failed: %s", exc)
        return False


def forward(cfg: dict, messages: list[dict]) -> bool:
    """Forward messages using the configured method."""
    if not messages:
        return True

    method = cfg["forward_method"]

    if cfg.get("digest_mode"):
        text = format_digest(messages)
        if method == "gchat_webhook":
            return send_gchat_webhook(cfg["gchat_webhook_url"], text)
        return send_email(cfg, f"SMS Digest: {len(messages)} new", text)

    ok = True
    for i, msg in enumerate(messages):
        if i > 0 and method == "gchat_webhook":
            time.sleep(1.5)
        text = format_message(msg)
        if method == "gchat_webhook":
            if not send_gchat_webhook(cfg["gchat_webhook_url"], text):
                ok = False
        else:
            if not send_email(cfg, f"SMS from {msg['sender']}", text):
                ok = False
    return ok


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------


def run_once(cfg: dict) -> None:
    """Single poll cycle: fetch -> filter -> forward -> save state."""
    state = load_state()
    last_rowid = state.get("last_rowid", 0)

    logging.info("Polling chat.db  (last_rowid=%d)", last_rowid)

    try:
        messages = fetch_new_messages(
            cfg["chat_db"],
            last_rowid,
            cfg.get("max_messages_per_run", 50),
            cfg.get("relay_number", ""),
        )
    except sqlite3.OperationalError as exc:
        if "database is locked" in str(exc):
            logging.warning("chat.db locked by Messages app — will retry next cycle")
            return
        raise

    if not messages:
        logging.info("No new messages")
        return

    logging.info("Found %d new inbound message(s)", len(messages))

    filtered = apply_filters(messages, cfg.get("filters", {}))
    skipped = len(messages) - len(filtered)
    if skipped:
        logging.info("Filtered out %d message(s), forwarding %d", skipped, len(filtered))

    if filtered:
        if forward(cfg, filtered):
            logging.info("Forwarded %d message(s)", len(filtered))
        else:
            logging.error("Some messages failed to forward (will not retry)")

    # Advance cursor to highest ROWID we saw, even if some were filtered
    new_rowid = messages[-1]["rowid"]
    state["last_rowid"] = new_rowid
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    logging.info("Cursor advanced to rowid %d", new_rowid)


def run_daemon(cfg: dict) -> None:
    """Poll in a loop with configured interval."""
    interval = cfg.get("poll_interval_seconds", 60)
    logging.info("SMS relay daemon started  (interval=%ds)", interval)
    while True:
        try:
            run_once(cfg)
        except Exception:
            logging.exception("Unexpected error during poll cycle")
        time.sleep(interval)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def setup_logging() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(),
        ],
    )


def cmd_status(cfg: dict) -> None:
    state = load_state()
    print(f"Config:    {CONFIG_FILE}")
    print(f"State:     {STATE_FILE}")
    print(f"Log:       {LOG_FILE}")
    print(f"chat.db:   {cfg['chat_db']}")
    print(f"Method:    {cfg['forward_method']}")
    print(f"Last ROWID: {state.get('last_rowid', 0)}")
    print(f"Last run:   {state.get('last_run', 'never')}")
    db_max = get_max_rowid(cfg["chat_db"])
    pending = db_max - state.get("last_rowid", 0)
    print(f"DB max ROWID: {db_max}  (pending inbound: ~{max(pending, 0)})")


def main() -> None:
    parser = argparse.ArgumentParser(description="SMS/iMessage Relay Service")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="Run one poll cycle and exit")
    group.add_argument("--init", action="store_true", help="Create default config and exit")
    group.add_argument("--seed", action="store_true", help="Set cursor to latest ROWID (skip history)")
    group.add_argument("--status", action="store_true", help="Show current state")
    args = parser.parse_args()

    if args.init:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.exists():
            print(f"Config already exists: {CONFIG_FILE}")
        else:
            CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
            print(f"Config created: {CONFIG_FILE}")
            print("Edit gchat_webhook_url, then run: python3 relay.py --seed")
        return

    cfg = load_config()
    setup_logging()

    if args.seed:
        max_rowid = get_max_rowid(cfg["chat_db"])
        state = {
            "last_rowid": max_rowid,
            "seeded_at": datetime.now(timezone.utc).isoformat(),
        }
        save_state(state)
        logging.info("Seeded state: last_rowid=%d  (all existing messages skipped)", max_rowid)
        return

    if args.status:
        cmd_status(cfg)
        return

    if args.once:
        run_once(cfg)
    else:
        run_daemon(cfg)


if __name__ == "__main__":
    main()
