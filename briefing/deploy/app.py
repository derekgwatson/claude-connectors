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


# -------------------------------------------------------------------------
# Follow-ups
# -------------------------------------------------------------------------


# -------------------------------------------------------------------------
# Requests — cross-channel grouping
# -------------------------------------------------------------------------


VALID_PRIORITIES = {"low", "normal", "high"}


class RequestCreate(BaseModel):
    name: str
    description: str = ""
    priority: str = "normal"


class RequestUpdate(BaseModel):
    name: str = ""
    description: str = ""
    status: str = ""
    priority: str = ""


class RequestItemAdd(BaseModel):
    channel: str
    item_id: str
    label: str = ""


@app.get("/api/briefing/requests")
def list_requests(status: str = "open", db=Depends(get_db)):
    cur = db.cursor(dictionary=True)
    if status == "all":
        cur.execute(
            "SELECT id, name, description, status, priority, created_at, closed_at "
            "FROM requests ORDER BY FIELD(priority, 'high', 'normal', 'low'), created_at DESC"
        )
    else:
        cur.execute(
            "SELECT id, name, description, status, priority, created_at, closed_at "
            "FROM requests WHERE status = %s ORDER BY FIELD(priority, 'high', 'normal', 'low'), created_at DESC",
            (status,),
        )
    rows = cur.fetchall()
    for r in rows:
        r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
        r["closed_at"] = r["closed_at"].isoformat() if r["closed_at"] else None
    return {"requests": rows}


@app.post("/api/briefing/requests")
def create_request(body: RequestCreate, db=Depends(get_db)):
    cur = db.cursor()
    if body.priority and body.priority not in VALID_PRIORITIES:
        raise HTTPException(400, f"Priority must be one of: {', '.join(VALID_PRIORITIES)}")
    cur.execute(
        "INSERT INTO requests (name, description, priority) VALUES (%s, %s, %s)",
        (body.name, body.description or None, body.priority or "normal"),
    )
    db.commit()
    return {"status": "ok", "id": cur.lastrowid, "name": body.name}


@app.get("/api/briefing/requests/search")
def search_requests(q: str, db=Depends(get_db)):
    cur = db.cursor(dictionary=True)
    cur.execute(
        "SELECT id, name, description, status, priority, created_at, closed_at "
        "FROM requests WHERE name LIKE %s ORDER BY created_at DESC",
        (f"%{q}%",),
    )
    rows = cur.fetchall()
    for r in rows:
        r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
        r["closed_at"] = r["closed_at"].isoformat() if r["closed_at"] else None
    return {"requests": rows}


@app.get("/api/briefing/requests/{request_id}")
def get_request(request_id: int, db=Depends(get_db)):
    cur = db.cursor(dictionary=True)
    cur.execute(
        "SELECT id, name, description, status, priority, created_at, closed_at "
        "FROM requests WHERE id = %s",
        (request_id,),
    )
    req = cur.fetchone()
    if not req:
        raise HTTPException(404, "Request not found")
    req["created_at"] = req["created_at"].isoformat() if req["created_at"] else None
    req["closed_at"] = req["closed_at"].isoformat() if req["closed_at"] else None

    cur.execute(
        "SELECT id, channel, item_id, label, added_at "
        "FROM request_items WHERE request_id = %s ORDER BY channel, added_at",
        (request_id,),
    )
    items = cur.fetchall()
    for item in items:
        item["added_at"] = item["added_at"].isoformat() if item["added_at"] else None
    req["items"] = items
    return req


@app.patch("/api/briefing/requests/{request_id}")
def update_request(request_id: int, body: RequestUpdate, db=Depends(get_db)):
    cur = db.cursor()
    updates = []
    params = []
    if body.name:
        updates.append("name = %s")
        params.append(body.name)
    if body.description:
        updates.append("description = %s")
        params.append(body.description)
    if body.priority:
        if body.priority not in VALID_PRIORITIES:
            raise HTTPException(400, f"Priority must be one of: {', '.join(VALID_PRIORITIES)}")
        updates.append("priority = %s")
        params.append(body.priority)
    if body.status:
        if body.status not in ("open", "closed", "pending"):
            raise HTTPException(400, "Status must be 'open', 'closed', or 'pending'")
        updates.append("status = %s")
        params.append(body.status)
        if body.status == "closed":
            updates.append("closed_at = NOW()")
        elif body.status in ("open", "pending"):
            updates.append("closed_at = NULL")
    if not updates:
        raise HTTPException(400, "No fields to update")
    params.append(request_id)
    cur.execute(
        f"UPDATE requests SET {', '.join(updates)} WHERE id = %s", params
    )
    db.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "Request not found")
    return {"status": "ok", "id": request_id}


@app.post("/api/briefing/requests/{request_id}/items")
def add_request_item(request_id: int, body: RequestItemAdd, db=Depends(get_db)):
    cur = db.cursor()
    try:
        cur.execute(
            "INSERT INTO request_items (request_id, channel, item_id, label) "
            "VALUES (%s, %s, %s, %s)",
            (request_id, body.channel, body.item_id, body.label or None),
        )
        db.commit()
    except Exception as e:
        if "Duplicate entry" in str(e):
            raise HTTPException(409, "Item already linked to this request")
        if "foreign key" in str(e).lower() or "Cannot add" in str(e):
            raise HTTPException(404, "Request not found")
        raise
    return {"status": "ok", "id": cur.lastrowid}


@app.delete("/api/briefing/requests/{request_id}/items/{item_id}")
def remove_request_item(request_id: int, item_id: str, db=Depends(get_db)):
    cur = db.cursor()
    cur.execute(
        "DELETE FROM request_items WHERE request_id = %s AND item_id = %s",
        (request_id, item_id),
    )
    db.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "Item not found in this request")
    return {"status": "ok", "deleted": True}


# -------------------------------------------------------------------------
# Follow-ups
# -------------------------------------------------------------------------


class FollowupCreate(BaseModel):
    person: str
    summary: str
    source_link: str = ""


@app.get("/api/briefing/followups")
def get_followups(db=Depends(get_db)):
    cur = db.cursor(dictionary=True)
    cur.execute(
        "SELECT id, person, summary, source_link, created_at "
        "FROM followups WHERE resolved_at IS NULL ORDER BY created_at"
    )
    rows = cur.fetchall()
    for r in rows:
        r["created_at"] = r["created_at"].isoformat()
    return {"followups": rows}


@app.post("/api/briefing/followups")
def add_followup(body: FollowupCreate, db=Depends(get_db)):
    cur = db.cursor()
    cur.execute(
        "INSERT INTO followups (person, summary, source_link) VALUES (%s, %s, %s)",
        (body.person, body.summary, body.source_link or None),
    )
    db.commit()
    return {"status": "ok", "id": cur.lastrowid}


@app.post("/api/briefing/followups/{followup_id}/resolve")
def resolve_followup(followup_id: int, db=Depends(get_db)):
    cur = db.cursor()
    cur.execute(
        "UPDATE followups SET resolved_at = NOW() WHERE id = %s AND resolved_at IS NULL",
        (followup_id,),
    )
    db.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "Follow-up not found or already resolved")
    return {"status": "ok", "id": followup_id, "resolved": True}


# -------------------------------------------------------------------------
# Memory — cloud-synced memory store
# -------------------------------------------------------------------------


class MemoryUpdate(BaseModel):
    content: str


@app.get("/api/briefing/memory")
def get_memory(db=Depends(get_db)):
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT content, updated_at FROM memory WHERE id = 1")
    row = cur.fetchone()
    if not row:
        return {"content": "", "updated_at": None}
    row["updated_at"] = row["updated_at"].isoformat() if row["updated_at"] else None
    return row


@app.put("/api/briefing/memory")
def put_memory(body: MemoryUpdate, db=Depends(get_db)):
    cur = db.cursor()
    cur.execute(
        "INSERT INTO memory (id, content) VALUES (1, %s) "
        "ON DUPLICATE KEY UPDATE content = %s, updated_at = NOW()",
        (body.content, body.content),
    )
    db.commit()
    return {"status": "ok"}
