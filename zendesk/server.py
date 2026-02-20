"""Zendesk MCP Server for Claude Code.

Connects to Zendesk via API token and exposes tools for managing support tickets.
"""

import os
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# --- Config ---
PROJECT_DIR = Path(__file__).parent
load_dotenv(PROJECT_DIR / ".env")

SUBDOMAIN = os.environ["ZENDESK_SUBDOMAIN"]
EMAIL = os.environ["ZENDESK_EMAIL"]
API_TOKEN = os.environ["ZENDESK_API_TOKEN"]

BASE_URL = f"https://{SUBDOMAIN}.zendesk.com/api/v2"
AUTH = (f"{EMAIL}/token", API_TOKEN)


def zen_get(endpoint: str, params: dict | None = None) -> dict:
    """Make an authenticated GET request to the Zendesk API."""
    r = requests.get(f"{BASE_URL}/{endpoint}", auth=AUTH, params=params)
    r.raise_for_status()
    return r.json()


def zen_put(endpoint: str, data: dict) -> dict:
    """Make an authenticated PUT request to the Zendesk API."""
    r = requests.put(f"{BASE_URL}/{endpoint}", auth=AUTH, json=data)
    r.raise_for_status()
    return r.json()


def zen_post(endpoint: str, data: dict) -> dict:
    """Make an authenticated POST request to the Zendesk API."""
    r = requests.post(f"{BASE_URL}/{endpoint}", auth=AUTH, json=data)
    r.raise_for_status()
    return r.json()


def format_dt(dt_str: str | None) -> str:
    """Format a Zendesk datetime string."""
    if not dt_str:
        return ""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_str


# Cache for user names to avoid repeated lookups
_user_cache: dict[int, str] = {}


def get_user_name(user_id: int | None) -> str:
    """Look up a Zendesk user's name by ID."""
    if not user_id:
        return "Unassigned"
    if user_id in _user_cache:
        return _user_cache[user_id]
    try:
        data = zen_get(f"users/{user_id}")
        name = data["user"]["name"]
        _user_cache[user_id] = name
        return name
    except Exception:
        return f"User {user_id}"


# --- MCP Server ---
mcp = FastMCP("zendesk")


@mcp.tool()
def list_tickets(
    status: str = "",
    assignee: str = "me",
    group_id: str = "",
    max_results: int = 25,
) -> str:
    """List Zendesk tickets with filters.

    Args:
        status: Filter by status (new, open, pending, hold, solved, closed). Leave empty for all open statuses.
        assignee: Filter by assignee. "me" for your tickets, "all" for any, or an email address.
        group_id: Filter by group ID. Leave empty to show your group's tickets.
        max_results: Number of tickets to return (default 25, max 100)
    """
    max_results = min(max_results, 100)

    # Build search query
    query_parts = ["type:ticket"]

    if status:
        query_parts.append(f"status:{status}")
    else:
        query_parts.append("status<solved")

    if group_id:
        query_parts.append(f"group_id:{group_id}")
    elif assignee == "me":
        # Default: my group's tickets
        query_parts.append(f"assignee:{EMAIL}")
    elif assignee != "all":
        query_parts.append(f"assignee:{assignee}")

    query = " ".join(query_parts)
    data = zen_get("search", params={"query": query, "per_page": max_results, "sort_by": "updated_at", "sort_order": "desc"})

    tickets = data.get("results", [])
    if not tickets:
        return f"No tickets found (query: {query})"

    lines = [f"Found {len(tickets)} tickets (query: {query}):\n"]
    for t in tickets:
        status_val = (t.get("status") or "unknown").upper()
        subject = t.get("subject") or "(no subject)"
        updated = format_dt(t.get("updated_at"))
        assignee_name = get_user_name(t.get("assignee_id"))
        requester_name = get_user_name(t.get("requester_id"))
        priority = t.get("priority") or "-"
        ticket_id = t.get("id")

        lines.append(
            f"[{status_val}] #{ticket_id}  |  {updated}\n"
            f"         Subject: {subject}\n"
            f"         Requester: {requester_name}  |  Assignee: {assignee_name}  |  Priority: {priority}\n"
        )

    return "\n".join(lines)


@mcp.tool()
def read_ticket(ticket_id: int) -> str:
    """Read the full details of a Zendesk ticket.

    Args:
        ticket_id: The Zendesk ticket ID (e.g. 657367)
    """
    data = zen_get(f"tickets/{ticket_id}")
    t = data["ticket"]

    subject = t.get("subject") or "(no subject)"
    description = t.get("description") or "(no description)"
    status = t.get("status") or "unknown"
    priority = t.get("priority") or "-"
    ticket_type = t.get("type") or "-"
    tags = ", ".join(t.get("tags", []))
    created = format_dt(t.get("created_at"))
    updated = format_dt(t.get("updated_at"))
    requester_name = get_user_name(t.get("requester_id"))
    assignee_name = get_user_name(t.get("assignee_id"))
    group_id = t.get("group_id") or "-"

    if len(description) > 10000:
        description = description[:10000] + "\n\n... (truncated)"

    return (
        f"Ticket #{ticket_id}\n"
        f"Subject: {subject}\n"
        f"Status: {status}  |  Priority: {priority}  |  Type: {ticket_type}\n"
        f"Requester: {requester_name}\n"
        f"Assignee: {assignee_name}  |  Group: {group_id}\n"
        f"Created: {created}  |  Updated: {updated}\n"
        f"Tags: {tags}\n"
        f"\n--- Description ---\n\n{description}"
    )


@mcp.tool()
def get_ticket_comments(ticket_id: int, max_results: int = 20) -> str:
    """Get comments and internal notes on a Zendesk ticket.

    Args:
        ticket_id: The Zendesk ticket ID
        max_results: Number of comments to return (default 20, max 100)
    """
    max_results = min(max_results, 100)
    data = zen_get(f"tickets/{ticket_id}/comments", params={"per_page": max_results})

    comments = data.get("comments", [])
    if not comments:
        return f"No comments on ticket #{ticket_id}."

    lines = [f"Comments on ticket #{ticket_id} ({len(comments)}):\n"]
    for c in comments:
        author = get_user_name(c.get("author_id"))
        created = format_dt(c.get("created_at"))
        public = "Public" if c.get("public") else "Internal note"
        body = c.get("plain_body") or c.get("body") or "(empty)"

        if len(body) > 2000:
            body = body[:2000] + "\n... (truncated)"

        attachments = c.get("attachments", [])
        att_line = ""
        if attachments:
            att_names = [a.get("file_name", "file") for a in attachments]
            att_line = f"\n         Attachments: {', '.join(att_names)}"

        lines.append(
            f"--- {author} | {created} | {public} ---\n"
            f"{body}{att_line}\n"
        )

    return "\n".join(lines)


@mcp.tool()
def search_tickets(query: str, max_results: int = 25) -> str:
    """Search Zendesk tickets.

    Args:
        query: Search query. Examples:
               "Fairy Meadow" - text search
               "status:open assignee:me" - filtered search
               "subject:onboard status:pending" - subject search
               "requester:anna@example.com" - by requester
        max_results: Number of results (default 25, max 100)
    """
    max_results = min(max_results, 100)

    # Add type:ticket if not already specified
    if "type:" not in query:
        query = f"type:ticket {query}"

    data = zen_get("search", params={"query": query, "per_page": max_results, "sort_by": "updated_at", "sort_order": "desc"})

    tickets = data.get("results", [])
    if not tickets:
        return f"No tickets found for: {query}"

    lines = [f"Found {len(tickets)} tickets for: {query}\n"]
    for t in tickets:
        status_val = (t.get("status") or "unknown").upper()
        subject = t.get("subject") or "(no subject)"
        updated = format_dt(t.get("updated_at"))
        requester_name = get_user_name(t.get("requester_id"))
        ticket_id = t.get("id")

        lines.append(
            f"[{status_val}] #{ticket_id}  |  {updated}\n"
            f"         Subject: {subject}\n"
            f"         Requester: {requester_name}\n"
        )

    return "\n".join(lines)


@mcp.tool()
def add_comment(
    ticket_id: int,
    body: str,
    public: bool = False,
) -> str:
    """Add a comment or internal note to a Zendesk ticket.

    Args:
        ticket_id: The Zendesk ticket ID
        body: The comment text
        public: If true, visible to the requester. If false (default), internal note only.
    """
    data = {
        "ticket": {
            "comment": {
                "body": body,
                "public": public,
            }
        }
    }
    zen_put(f"tickets/{ticket_id}", data)
    visibility = "public reply" if public else "internal note"
    return f"Added {visibility} to ticket #{ticket_id}."


@mcp.tool()
def update_ticket(
    ticket_id: int,
    status: str = "",
    priority: str = "",
    assignee_email: str = "",
    tags_to_add: str = "",
    tags_to_remove: str = "",
) -> str:
    """Update a Zendesk ticket's properties.

    Args:
        ticket_id: The Zendesk ticket ID
        status: New status (new, open, pending, hold, solved, closed). Leave empty to keep current.
        priority: New priority (low, normal, high, urgent). Leave empty to keep current.
        assignee_email: Email of new assignee. Leave empty to keep current.
        tags_to_add: Comma-separated tags to add. Leave empty to skip.
        tags_to_remove: Comma-separated tags to remove. Leave empty to skip.
    """
    ticket_data: dict = {}
    changes = []

    if status:
        ticket_data["status"] = status
        changes.append(f"status -> {status}")

    if priority:
        ticket_data["priority"] = priority
        changes.append(f"priority -> {priority}")

    if assignee_email:
        # Look up user by email
        user_data = zen_get("search", params={"query": f"type:user email:{assignee_email}"})
        users = user_data.get("results", [])
        if not users:
            return f"No user found with email {assignee_email}"
        ticket_data["assignee_id"] = users[0]["id"]
        changes.append(f"assignee -> {assignee_email}")

    if not ticket_data and not tags_to_add and not tags_to_remove:
        return "No changes specified."

    # Handle tags separately if needed
    if tags_to_add or tags_to_remove:
        current = zen_get(f"tickets/{ticket_id}")
        current_tags = set(current["ticket"].get("tags", []))

        if tags_to_add:
            new_tags = {t.strip() for t in tags_to_add.split(",")}
            current_tags |= new_tags
            changes.append(f"added tags: {tags_to_add}")

        if tags_to_remove:
            rm_tags = {t.strip() for t in tags_to_remove.split(",")}
            current_tags -= rm_tags
            changes.append(f"removed tags: {tags_to_remove}")

        ticket_data["tags"] = list(current_tags)

    zen_put(f"tickets/{ticket_id}", {"ticket": ticket_data})
    return f"Updated ticket #{ticket_id}: {', '.join(changes)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
