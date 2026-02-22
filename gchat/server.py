"""Google Chat MCP Server for Claude Code.

Connects to Google Chat via OAuth2 and exposes tools for reading
DMs, group chats, and space messages.

Requires a Google Workspace account — the Chat API is not available
to personal Gmail accounts.

Setup:
  1. Enable "Google Chat API" in your Google Cloud Console project
  2. Configure a Chat app in the API settings (name, avatar, description)
  3. Add Chat scopes to your OAuth consent screen
  4. Copy or symlink credentials.json from your gmail/ directory
  5. Run this server — it will open a browser for OAuth consent on first run
"""

import os
from datetime import datetime, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from mcp.server.fastmcp import FastMCP

# --- Config ---
PROJECT_DIR = Path(__file__).parent
CREDENTIALS_FILE = PROJECT_DIR / "credentials.json"
TOKEN_FILE = PROJECT_DIR / "token.json"
SCOPES = [
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/chat.memberships.readonly",
]


def get_chat_service():
    """Authenticate and return a Google Chat API service instance."""
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"credentials.json not found at {CREDENTIALS_FILE}. "
                    "Copy it from your gmail/ directory or download from Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(creds.to_json())

    return build("chat", "v1", credentials=creds)


def format_dt(dt_str: str) -> str:
    """Format an ISO datetime string to something readable."""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_str


def space_label(space: dict) -> str:
    """Return a human-readable label for a space."""
    space_type = space.get("spaceType", "SPACE")
    name = space.get("displayName", "")

    if space_type == "DIRECT_MESSAGE":
        return name if name else "Direct Message"
    elif space_type == "GROUP_CHAT":
        return name if name else "Group Chat"
    else:
        return name if name else "Space"


# --- MCP Server ---
mcp = FastMCP("google-chat")


@mcp.tool()
def list_spaces(
    space_type: str = "",
    max_results: int = 50,
) -> str:
    """List Google Chat spaces, DMs, and group chats you belong to.

    Args:
        space_type: Filter by type: "DIRECT_MESSAGE", "GROUP_CHAT", "SPACE", or "" for all.
        max_results: Number of spaces to return (default 50, max 200)
    """
    max_results = min(max_results, 200)
    service = get_chat_service()

    filter_str = ""
    if space_type:
        filter_str = f'spaceType = "{space_type}"'

    all_spaces = []
    page_token = None

    while len(all_spaces) < max_results:
        page_size = min(max_results - len(all_spaces), 100)
        kwargs = {"pageSize": page_size}
        if filter_str:
            kwargs["filter"] = filter_str
        if page_token:
            kwargs["pageToken"] = page_token

        result = service.spaces().list(**kwargs).execute()
        spaces = result.get("spaces", [])
        all_spaces.extend(spaces)

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    if not all_spaces:
        return "No spaces found."

    lines = [f"Found {len(all_spaces)} space(s):\n"]
    for sp in all_spaces:
        st = sp.get("spaceType", "?")
        label = space_label(sp)
        last_active = format_dt(sp.get("lastActiveTime", ""))
        resource_name = sp.get("name", "")

        type_tag = {"DIRECT_MESSAGE": "[DM]", "GROUP_CHAT": "[Group]", "SPACE": "[Space]"}.get(st, f"[{st}]")
        active_str = f"  Last active: {last_active}" if last_active else ""

        lines.append(
            f"  {type_tag} {label}\n"
            f"         Name: {resource_name}{active_str}\n"
        )

    return "\n".join(lines)


@mcp.tool()
def list_messages(
    space_name: str,
    max_results: int = 25,
    order_by: str = "createTime DESC",
    filter: str = "",
) -> str:
    """List messages in a Google Chat space.

    Args:
        space_name: The space resource name (e.g. "spaces/AAAA1234")
        max_results: Number of messages to return (default 25, max 200)
        order_by: Sort order — "createTime DESC" (newest first) or "createTime ASC" (oldest first)
        filter: Optional filter string. Examples:
                'createTime > "2025-01-01T00:00:00Z"'
                'thread.name = "spaces/SPACE_ID/threads/THREAD_ID"'
    """
    max_results = min(max_results, 200)
    service = get_chat_service()

    all_messages = []
    page_token = None

    while len(all_messages) < max_results:
        page_size = min(max_results - len(all_messages), 100)
        kwargs = {
            "parent": space_name,
            "pageSize": page_size,
            "orderBy": order_by,
        }
        if filter:
            kwargs["filter"] = filter
        if page_token:
            kwargs["pageToken"] = page_token

        result = service.spaces().messages().list(**kwargs).execute()
        messages = result.get("messages", [])
        all_messages.extend(messages)

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    if not all_messages:
        return f"No messages found in {space_name}."

    lines = [f"Found {len(all_messages)} message(s) in {space_name}:\n"]
    for msg in all_messages:
        sender = msg.get("sender", {})
        sender_name = sender.get("displayName", "Unknown")
        sender_type = sender.get("type", "")
        created = format_dt(msg.get("createTime", ""))
        text = msg.get("text", "(no text)")
        resource_name = msg.get("name", "")

        # Show if it's a thread reply
        thread_tag = " [reply]" if msg.get("threadReply") else ""

        # Truncate long messages in the listing
        if len(text) > 300:
            text = text[:300] + "..."

        type_tag = f" ({sender_type})" if sender_type == "BOT" else ""

        lines.append(
            f"  [{created}] {sender_name}{type_tag}{thread_tag}\n"
            f"  {text}\n"
            f"  ID: {resource_name}\n"
        )

    return "\n".join(lines)


@mcp.tool()
def read_message(message_name: str) -> str:
    """Read the full content of a specific Google Chat message.

    Args:
        message_name: The message resource name (e.g. "spaces/AAAA/messages/BBBB")
    """
    service = get_chat_service()
    msg = service.spaces().messages().get(name=message_name).execute()

    sender = msg.get("sender", {})
    sender_name = sender.get("displayName", "Unknown")
    sender_type = sender.get("type", "HUMAN")
    created = format_dt(msg.get("createTime", ""))
    updated = format_dt(msg.get("lastUpdateTime", ""))
    text = msg.get("text", "(no text)")
    thread_name = msg.get("thread", {}).get("name", "")
    space_name = msg.get("space", {}).get("name", "")

    # Attachments
    attachments = msg.get("attachment", [])
    attachment_lines = ""
    if attachments:
        att_strs = []
        for att in attachments:
            att_name = att.get("name", "attachment")
            content_name = att.get("contentName", "")
            att_strs.append(content_name or att_name)
        attachment_lines = f"Attachments: {', '.join(att_strs)}\n"

    # Reactions
    reactions = msg.get("emojiReactionSummaries", [])
    reaction_lines = ""
    if reactions:
        rxn_strs = []
        for rxn in reactions:
            emoji = rxn.get("emoji", {}).get("unicode", "?")
            count = rxn.get("reactionCount", 0)
            rxn_strs.append(f"{emoji} x{count}")
        reaction_lines = f"Reactions: {' '.join(rxn_strs)}\n"

    # Truncate very long messages
    if len(text) > 10000:
        text = text[:10000] + "\n\n... (truncated - message was very long)"

    return (
        f"From: {sender_name} ({sender_type})\n"
        f"Space: {space_name}\n"
        f"Thread: {thread_name}\n"
        f"Created: {created}\n"
        f"Updated: {updated}\n"
        f"{attachment_lines}"
        f"{reaction_lines}"
        f"\n--- Message ---\n\n{text}"
    )


@mcp.tool()
def list_members(
    space_name: str,
    max_results: int = 100,
) -> str:
    """List members of a Google Chat space.

    Args:
        space_name: The space resource name (e.g. "spaces/AAAA1234")
        max_results: Number of members to return (default 100, max 500)
    """
    max_results = min(max_results, 500)
    service = get_chat_service()

    all_members = []
    page_token = None

    while len(all_members) < max_results:
        page_size = min(max_results - len(all_members), 100)
        kwargs = {"parent": space_name, "pageSize": page_size}
        if page_token:
            kwargs["pageToken"] = page_token

        result = service.spaces().members().list(**kwargs).execute()
        members = result.get("memberships", [])
        all_members.extend(members)

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    if not all_members:
        return f"No members found in {space_name}."

    lines = [f"Found {len(all_members)} member(s) in {space_name}:\n"]
    for m in all_members:
        member = m.get("member", {})
        display_name = member.get("displayName", "Unknown")
        member_type = member.get("type", "HUMAN")
        role = m.get("role", "ROLE_MEMBER")
        state = m.get("state", "JOINED")

        role_tag = " [Manager]" if role == "ROLE_MANAGER" else ""
        type_tag = f" (Bot)" if member_type == "BOT" else ""
        state_tag = f" ({state})" if state != "JOINED" else ""

        lines.append(f"  {display_name}{type_tag}{role_tag}{state_tag}")

    return "\n".join(lines)


@mcp.tool()
def find_dm(user: str) -> str:
    """Find a direct message space with a specific user.

    Args:
        user: The user identifier — email address or user ID (e.g. "users/123456" or "users/jane@example.com")
    """
    service = get_chat_service()

    # Normalise — accept bare email or users/ prefix
    if not user.startswith("users/"):
        user = f"users/{user}"

    try:
        space = service.spaces().findDirectMessage(name=user).execute()
    except Exception as e:
        error_str = str(e)
        if "404" in error_str or "not found" in error_str.lower():
            return f"No direct message found with {user}."
        raise

    label = space_label(space)
    resource_name = space.get("name", "")
    last_active = format_dt(space.get("lastActiveTime", ""))

    return (
        f"DM with: {label}\n"
        f"Space name: {resource_name}\n"
        f"Last active: {last_active}\n"
        f"\nUse list_messages(space_name=\"{resource_name}\") to read messages."
    )


@mcp.tool()
def get_space(space_name: str) -> str:
    """Get details about a specific Google Chat space.

    Args:
        space_name: The space resource name (e.g. "spaces/AAAA1234")
    """
    service = get_chat_service()
    space = service.spaces().get(name=space_name).execute()

    display_name = space.get("displayName", "")
    space_type = space.get("spaceType", "?")
    threading = space.get("spaceThreadingState", "")
    history = space.get("spaceHistoryState", "")
    created = format_dt(space.get("createTime", ""))
    last_active = format_dt(space.get("lastActiveTime", ""))
    details = space.get("spaceDetails", {})
    description = details.get("description", "")
    guidelines = details.get("guidelines", "")
    uri = space.get("spaceUri", "")

    type_tag = {"DIRECT_MESSAGE": "Direct Message", "GROUP_CHAT": "Group Chat", "SPACE": "Space"}.get(space_type, space_type)

    lines = [
        f"Name: {space.get('name', '')}",
        f"Display name: {display_name}" if display_name else None,
        f"Type: {type_tag}",
        f"Threading: {threading}" if threading else None,
        f"History: {history}" if history else None,
        f"Created: {created}" if created else None,
        f"Last active: {last_active}" if last_active else None,
        f"Description: {description}" if description else None,
        f"Guidelines: {guidelines}" if guidelines else None,
        f"Link: {uri}" if uri else None,
    ]

    return "\n".join(line for line in lines if line is not None)


if __name__ == "__main__":
    mcp.run(transport="stdio")
