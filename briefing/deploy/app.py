"""Briefing State API — tracks what's been briefed across channels."""

import os
from datetime import datetime, timezone

import mysql.connector
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Header
from pydantic import BaseModel

load_dotenv()

DB_CONFIG = {
    "host": "127.0.0.1",
    "user": os.environ.get("DB_USER", "briefing"),
    "password": os.environ["DB_PASSWORD"],
    "database": "briefing_state",
}
API_KEY = os.environ["BRIEFING_API_KEY"]


def get_db():
    conn = mysql.connector.connect(**DB_CONFIG)
    try:
        yield conn
    finally:
        conn.close()


def verify_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(401, "Invalid API key")


app = FastAPI(title="Briefing State", dependencies=[Depends(verify_key)])

VALID_CHANNELS = {"gmail", "zendesk", "gchat", "sms"}


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# -------------------------------------------------------------------------
# Health
# -------------------------------------------------------------------------


@app.get("/api/briefing/health")
def health(db=Depends(get_db)):
    cur = db.cursor()
    cur.execute("SELECT 1")
    cur.fetchone()
    return {"status": "ok", "db": "connected"}


# -------------------------------------------------------------------------
# Summary
# -------------------------------------------------------------------------


@app.get("/api/briefing/summary")
def summary(db=Depends(get_db)):
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT channel, last_briefed FROM channel_state")
    rows = cur.fetchall()
    result = {}
    for r in rows:
        result[r["channel"]] = r["last_briefed"].isoformat() if r["last_briefed"] else None
    return result


# -------------------------------------------------------------------------
# Get channel state
# -------------------------------------------------------------------------


@app.get("/api/briefing/state/{channel}")
def get_state(channel: str, db=Depends(get_db)):
    if channel not in VALID_CHANNELS:
        raise HTTPException(400, f"Invalid channel: {channel}")

    cur = db.cursor(dictionary=True)

    cur.execute(
        "SELECT last_briefed FROM channel_state WHERE channel = %s", (channel,)
    )
    row = cur.fetchone()
    last_briefed = row["last_briefed"].isoformat() if row else None

    details = {}

    if channel == "gmail":
        cur.execute(
            "SELECT message_id FROM gmail_seen ORDER BY briefed_at DESC LIMIT 200"
        )
        details["seen_ids"] = [r["message_id"] for r in cur.fetchall()]
        details["seen_count"] = len(details["seen_ids"])

    elif channel == "zendesk":
        cur.execute("SELECT ticket_id, last_update FROM zendesk_seen")
        details["tickets"] = {
            str(r["ticket_id"]): r["last_update"] for r in cur.fetchall()
        }

    elif channel == "gchat":
        cur.execute("SELECT space_name, last_message FROM gchat_seen")
        details["spaces"] = {
            r["space_name"]: r["last_message"] for r in cur.fetchall()
        }

    return {"channel": channel, "last_briefed": last_briefed, "details": details}


# -------------------------------------------------------------------------
# Mark as briefed
# -------------------------------------------------------------------------


class GmailMark(BaseModel):
    message_ids: list[str]
    timestamp: str = ""


class ZendeskMark(BaseModel):
    tickets: dict[str, str]  # {"ticket_id": "updated_at"}
    timestamp: str = ""


class GchatMark(BaseModel):
    spaces: dict[str, str]  # {"space_name": "last_message_time"}
    timestamp: str = ""


class SmsMark(BaseModel):
    timestamp: str = ""


@app.post("/api/briefing/state/gmail/mark")
def mark_gmail(body: GmailMark, db=Depends(get_db)):
    cur = db.cursor()
    ts = body.timestamp or _now_iso()
    for mid in body.message_ids:
        cur.execute(
            "INSERT INTO gmail_seen (message_id) VALUES (%s) "
            "ON DUPLICATE KEY UPDATE briefed_at = CURRENT_TIMESTAMP",
            (mid,),
        )
    cur.execute(
        "UPDATE channel_state SET last_briefed = %s WHERE channel = 'gmail'", (ts,)
    )
    db.commit()

    # Cleanup: remove entries older than 30 days
    cur.execute(
        "DELETE FROM gmail_seen WHERE briefed_at < DATE_SUB(NOW(), INTERVAL 30 DAY)"
    )
    db.commit()
    return {"status": "ok", "marked": len(body.message_ids)}


@app.post("/api/briefing/state/zendesk/mark")
def mark_zendesk(body: ZendeskMark, db=Depends(get_db)):
    cur = db.cursor()
    ts = body.timestamp or _now_iso()
    for tid, updated_at in body.tickets.items():
        cur.execute(
            "INSERT INTO zendesk_seen (ticket_id, last_update) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE last_update = %s, briefed_at = CURRENT_TIMESTAMP",
            (int(tid), updated_at, updated_at),
        )
    cur.execute(
        "UPDATE channel_state SET last_briefed = %s WHERE channel = 'zendesk'", (ts,)
    )
    db.commit()
    return {"status": "ok", "marked": len(body.tickets)}


@app.post("/api/briefing/state/gchat/mark")
def mark_gchat(body: GchatMark, db=Depends(get_db)):
    cur = db.cursor()
    ts = body.timestamp or _now_iso()
    for space, last_msg in body.spaces.items():
        cur.execute(
            "INSERT INTO gchat_seen (space_name, last_message) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE last_message = %s, briefed_at = CURRENT_TIMESTAMP",
            (space, last_msg, last_msg),
        )
    cur.execute(
        "UPDATE channel_state SET last_briefed = %s WHERE channel = 'gchat'", (ts,)
    )
    db.commit()
    return {"status": "ok", "marked": len(body.spaces)}


@app.post("/api/briefing/state/sms/mark")
def mark_sms(body: SmsMark, db=Depends(get_db)):
    cur = db.cursor()
    ts = body.timestamp or _now_iso()
    cur.execute(
        "UPDATE channel_state SET last_briefed = %s WHERE channel = 'sms'", (ts,)
    )
    db.commit()
    return {"status": "ok"}


# -------------------------------------------------------------------------
# Reset
# -------------------------------------------------------------------------


@app.delete("/api/briefing/state/{channel}/reset")
def reset_channel(channel: str, db=Depends(get_db)):
    if channel not in VALID_CHANNELS:
        raise HTTPException(400, f"Invalid channel: {channel}")

    cur = db.cursor()
    cur.execute(
        "UPDATE channel_state SET last_briefed = '1970-01-01 00:00:00' WHERE channel = %s",
        (channel,),
    )

    if channel == "gmail":
        cur.execute("DELETE FROM gmail_seen")
    elif channel == "zendesk":
        cur.execute("DELETE FROM zendesk_seen")
    elif channel == "gchat":
        cur.execute("DELETE FROM gchat_seen")

    db.commit()
    return {"status": "ok", "channel": channel, "reset": True}


# -------------------------------------------------------------------------
# Preferences
# -------------------------------------------------------------------------


class PrefSet(BaseModel):
    key: str
    value: str


@app.get("/api/briefing/prefs")
def get_prefs(db=Depends(get_db)):
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT pref_key, pref_value, updated_at FROM briefing_prefs ORDER BY pref_key")
    rows = cur.fetchall()
    return {
        r["pref_key"]: {"value": r["pref_value"], "updated_at": r["updated_at"].isoformat()}
        for r in rows
    }


@app.post("/api/briefing/prefs")
def set_pref(body: PrefSet, db=Depends(get_db)):
    cur = db.cursor()
    cur.execute(
        "INSERT INTO briefing_prefs (pref_key, pref_value) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE pref_value = %s",
        (body.key, body.value, body.value),
    )
    db.commit()
    return {"status": "ok", "key": body.key, "value": body.value}


@app.delete("/api/briefing/prefs/{key}")
def delete_pref(key: str, db=Depends(get_db)):
    cur = db.cursor()
    cur.execute("DELETE FROM briefing_prefs WHERE pref_key = %s", (key,))
    db.commit()
    return {"status": "ok", "key": key, "deleted": cur.rowcount > 0}
