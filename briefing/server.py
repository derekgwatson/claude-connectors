"""Briefing State MCP Server for Claude Code.

Connects to the remote briefing state API to track what's been
briefed across sessions. Enables incremental morning briefings.
"""

import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# --- Config ---
PROJECT_DIR = Path(__file__).parent
load_dotenv(PROJECT_DIR / ".env")

API_BASE = os.environ["BRIEFING_API_URL"].rstrip("/")
API_KEY = os.environ["BRIEFING_API_KEY"]
HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

mcp = FastMCP("briefing-state")


def _get(path: str) -> dict:
    r = requests.get(f"{API_BASE}{path}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def _post(path: str, data: dict) -> dict:
    r = requests.post(f"{API_BASE}{path}", headers=HEADERS, json=data, timeout=15)
    r.raise_for_status()
    return r.json()


def _delete(path: str) -> dict:
    r = requests.delete(f"{API_BASE}{path}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


# -----------------------------------------------------------------------
# Tools
# -----------------------------------------------------------------------


@mcp.tool()
def get_briefing_summary() -> str:
    """Get when each channel was last briefed.

    Returns a summary of all channels (gmail, zendesk, gchat, sms)
    with their last-briefed timestamps. Call this first when starting
    a morning briefing to see what's stale.
    """
    data = _get("/summary")
    lines = ["Channel Briefing State", "=" * 40]
    for channel, ts in data.items():
        lines.append(f"  {channel:10s}  last briefed: {ts or 'never'}")
    return "\n".join(lines)


@mcp.tool()
def get_channel_state(channel: str) -> str:
    """Get detailed briefing state for a specific channel.

    Args:
        channel: One of 'gmail', 'zendesk', 'gchat', 'sms'

    Returns the last-briefed timestamp plus channel-specific details:
    - gmail: list of already-seen message IDs
    - zendesk: per-ticket last-seen updated_at timestamps
    - gchat: per-space last-seen message timestamps
    """
    data = _get(f"/state/{channel}")
    lines = [f"Channel: {data['channel']}", f"Last briefed: {data['last_briefed'] or 'never'}", ""]

    details = data.get("details", {})

    if channel == "gmail":
        seen = details.get("seen_ids", [])
        lines.append(f"Seen message IDs ({details.get('seen_count', 0)} total):")
        for mid in seen[:50]:
            lines.append(f"  {mid}")
        if len(seen) > 50:
            lines.append(f"  ... and {len(seen) - 50} more")

    elif channel == "zendesk":
        tickets = details.get("tickets", {})
        lines.append(f"Tracked tickets ({len(tickets)}):")
        for tid, updated in tickets.items():
            lines.append(f"  #{tid}  last update: {updated}")

    elif channel == "gchat":
        spaces = details.get("spaces", {})
        lines.append(f"Tracked spaces ({len(spaces)}):")
        for space, last_msg in spaces.items():
            lines.append(f"  {space}  last message: {last_msg}")

    return "\n".join(lines)


@mcp.tool()
def mark_gmail_briefed(message_ids: str, timestamp: str = "") -> str:
    """Mark Gmail messages as briefed.

    Args:
        message_ids: Comma-separated Gmail message IDs (e.g. "abc123,def456")
        timestamp: Optional ISO timestamp. Defaults to now.
    """
    ids = [mid.strip() for mid in message_ids.split(",") if mid.strip()]
    data = {"message_ids": ids}
    if timestamp:
        data["timestamp"] = timestamp
    result = _post("/state/gmail/mark", data)
    return f"Marked {result['marked']} Gmail message(s) as briefed"


@mcp.tool()
def mark_zendesk_briefed(tickets: str, timestamp: str = "") -> str:
    """Mark Zendesk tickets as briefed with their updated_at timestamps.

    Args:
        tickets: Comma-separated pairs of ticket_id=updated_at
                 (e.g. "12345=2026-02-21 09:00,12346=2026-02-21 08:30")
        timestamp: Optional ISO timestamp for channel-level marker. Defaults to now.
    """
    ticket_map = {}
    for pair in tickets.split(","):
        pair = pair.strip()
        if "=" in pair:
            tid, updated = pair.split("=", 1)
            ticket_map[tid.strip()] = updated.strip()
    data = {"tickets": ticket_map}
    if timestamp:
        data["timestamp"] = timestamp
    result = _post("/state/zendesk/mark", data)
    return f"Marked {result['marked']} Zendesk ticket(s) as briefed"


@mcp.tool()
def mark_gchat_briefed(spaces: str, timestamp: str = "") -> str:
    """Mark Google Chat spaces as briefed with their last message timestamps.

    Args:
        spaces: Comma-separated pairs of space_name=last_message_time
                (e.g. "spaces/AAA=2026-02-21 09:00,spaces/BBB=2026-02-21 08:00")
        timestamp: Optional ISO timestamp for channel-level marker. Defaults to now.
    """
    space_map = {}
    for pair in spaces.split(","):
        pair = pair.strip()
        if "=" in pair:
            space, last_msg = pair.split("=", 1)
            space_map[space.strip()] = last_msg.strip()
    data = {"spaces": space_map}
    if timestamp:
        data["timestamp"] = timestamp
    result = _post("/state/gchat/mark", data)
    return f"Marked {result['marked']} Google Chat space(s) as briefed"


@mcp.tool()
def get_briefing_prefs() -> str:
    """Get all briefing preferences.

    Returns stored preferences that guide briefing behaviour,
    such as email cleanup rules and notification settings.
    Check these at the start of every briefing.
    """
    data = _get("/prefs")
    if not data:
        return "No briefing preferences set."
    lines = ["Briefing Preferences", "=" * 40]
    for key, info in data.items():
        lines.append(f"  {key} = {info['value']}")
    return "\n".join(lines)


@mcp.tool()
def set_briefing_pref(key: str, value: str) -> str:
    """Set a briefing preference.

    Args:
        key: Preference name (e.g. "zendesk_email_archive_after_hours")
        value: Preference value (e.g. "24")
    """
    result = _post("/prefs", {"key": key, "value": value})
    return f"Preference set: {result['key']} = {result['value']}"


@mcp.tool()
def delete_briefing_pref(key: str) -> str:
    """Delete a briefing preference.

    Args:
        key: Preference name to delete
    """
    result = _delete(f"/prefs/{key}")
    if result.get("deleted"):
        return f"Preference '{key}' deleted."
    return f"Preference '{key}' not found."


@mcp.tool()
def reset_channel(channel: str) -> str:
    """Reset a channel's briefing state back to the beginning.

    Args:
        channel: One of 'gmail', 'zendesk', 'gchat', 'sms'

    This clears all seen markers and resets last_briefed to epoch.
    Use when you want to re-brief everything for a channel.
    """
    result = _delete(f"/state/{channel}/reset")
    return f"Channel '{result['channel']}' has been reset. Next briefing will include all items."


# -----------------------------------------------------------------------
# Follow-ups
# -----------------------------------------------------------------------


@mcp.tool()
def get_followups() -> str:
    """Get open follow-ups — people waiting on a reply from you.

    Returns a list of outstanding follow-ups. Check this at the start
    of every briefing and surface any items that need attention.
    """
    data = _get("/followups")
    items = data.get("followups", [])
    if not items:
        return "No open follow-ups."
    lines = ["People waiting on you", "=" * 40]
    for f in items:
        link = f"  Link: {f['source_link']}" if f.get("source_link") else ""
        lines.append(f"  [{f['id']}] {f['person']} — {f['summary']}  (added {f['created_at']})")
        if link:
            lines.append(link)
    return "\n".join(lines)


@mcp.tool()
def add_followup(person: str, summary: str, source_link: str = "") -> str:
    """Add a follow-up — someone is waiting on you to get back to them.

    Args:
        person: Who is waiting (e.g. "Ben Smith")
        summary: What they're waiting for (e.g. "wants to know how the scanner setup went")
        source_link: Optional link to the email or ticket for quick access
    """
    data = {"person": person, "summary": summary}
    if source_link:
        data["source_link"] = source_link
    result = _post("/followups", data)
    return f"Follow-up #{result['id']} added: {person} — {summary}"


@mcp.tool()
def resolve_followup(followup_id: int) -> str:
    """Mark a follow-up as done — you've gotten back to the person.

    Args:
        followup_id: The ID of the follow-up to resolve (from get_followups)
    """
    result = _post(f"/followups/{followup_id}/resolve", {})
    return f"Follow-up #{result['id']} resolved."


if __name__ == "__main__":
    mcp.run(transport="stdio")
