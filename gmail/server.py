"""Gmail MCP Server for Claude Code.

Connects to Gmail via OAuth2 and exposes tools for reading and managing emails.
"""

import base64
import json
import logging
import os
import re
import sys
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s", stream=sys.stderr)
logger = logging.getLogger("mcp.gmail")

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
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]


def get_gmail_service():
    """Authenticate and return a Gmail API service instance."""
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
                    "Download it from Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def decode_body(payload: dict) -> str:
    """Extract plain text body from a Gmail message payload."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    # Multipart - recurse into parts
    parts = payload.get("parts", [])

    # Prefer text/plain
    for part in parts:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")

    # Fall back to text/html, strip tags
    for part in parts:
        if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
            html = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text).strip()
            return text

    # Recurse into nested multipart
    for part in parts:
        result = decode_body(part)
        if result:
            return result

    return "(no readable body)"


def get_header(headers: list, name: str) -> str:
    """Get a header value by name from a list of Gmail headers."""
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def format_date(date_str: str) -> str:
    """Format an email date string to something readable."""
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return date_str


# --- MCP Server ---
mcp = FastMCP("gmail-agent")


@mcp.tool()
def list_emails(
    max_results: int = 20,
    query: str = "is:inbox",
    unread_only: bool = False,
) -> str:
    """List emails from Gmail.

    Args:
        max_results: Number of emails to return (default 20, max 50)
        query: Gmail search query (default "is:inbox"). Examples:
               "is:unread", "from:someone@example.com", "subject:invoice",
               "newer_than:1d", "is:inbox category:primary"
        unread_only: If true, only show unread emails
    """
    max_results = min(max_results, 50)
    if unread_only:
        query = f"{query} is:unread"

    service = get_gmail_service()
    results = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()

    messages = results.get("messages", [])
    if not messages:
        return "No emails found matching that query."

    output_lines = []
    for msg_info in messages:
        msg = service.users().messages().get(
            userId="me", id=msg_info["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"]
        ).execute()

        headers = msg.get("payload", {}).get("headers", [])
        labels = msg.get("labelIds", [])
        is_unread = "UNREAD" in labels

        from_val = get_header(headers, "From")
        subject = get_header(headers, "Subject") or "(no subject)"
        date = format_date(get_header(headers, "Date"))
        snippet = msg.get("snippet", "")

        status = "[UNREAD]" if is_unread else "[read]  "
        output_lines.append(
            f"{status} {date}  |  From: {from_val}\n"
            f"         Subject: {subject}\n"
            f"         Preview: {snippet}\n"
            f"         ID: {msg_info['id']}\n"
        )

    header = f"Found {len(messages)} emails (query: {query}):\n"
    return header + "\n".join(output_lines)


@mcp.tool()
def read_email(message_id: str) -> str:
    """Read the full content of a specific email.

    Args:
        message_id: The Gmail message ID (from list_emails results)
    """
    service = get_gmail_service()
    msg = service.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()

    headers = msg.get("payload", {}).get("headers", [])
    from_val = get_header(headers, "From")
    to_val = get_header(headers, "To")
    subject = get_header(headers, "Subject") or "(no subject)"
    date = format_date(get_header(headers, "Date"))
    labels = msg.get("labelIds", [])

    body = decode_body(msg.get("payload", {}))

    # Truncate very long emails
    if len(body) > 10000:
        body = body[:10000] + "\n\n... (truncated - email was very long)"

    return (
        f"From: {from_val}\n"
        f"To: {to_val}\n"
        f"Date: {date}\n"
        f"Subject: {subject}\n"
        f"Labels: {', '.join(labels)}\n"
        f"\n--- Body ---\n\n{body}"
    )


@mcp.tool()
def search_emails(
    query: str,
    max_results: int = 10,
) -> str:
    """Search emails using Gmail's search syntax.

    Args:
        query: Gmail search query. Examples:
               "from:boss@company.com newer_than:7d"
               "subject:invoice has:attachment"
               "is:unread category:updates"
               "filename:pdf newer_than:30d"
        max_results: Number of results to return (default 10, max 50)
    """
    return list_emails(max_results=max_results, query=query)


@mcp.tool()
def get_inbox_summary() -> str:
    """Get a summary of the current inbox state - unread counts by category."""
    service = get_gmail_service()

    categories = {
        "Unread (Primary)": "is:unread category:primary",
        "Unread (Updates)": "is:unread category:updates",
        "Unread (Promotions)": "is:unread category:promotions",
        "Unread (Social)": "is:unread category:social",
        "Unread (Forums)": "is:unread category:forums",
        "Unread (Total)": "is:unread",
        "Starred": "is:starred",
    }

    lines = ["Inbox Summary:", ""]
    for label, query in categories.items():
        results = service.users().messages().list(
            userId="me", q=query, maxResults=1
        ).execute()
        count = results.get("resultSizeEstimate", 0)
        lines.append(f"  {label}: {count}")

    return "\n".join(lines)


@mcp.tool()
def mark_as_read(message_id: str) -> str:
    """Mark an email as read.

    Args:
        message_id: The Gmail message ID
    """
    service = get_gmail_service()
    service.users().messages().modify(
        userId="me", id=message_id,
        body={"removeLabelIds": ["UNREAD"]}
    ).execute()
    return f"Email {message_id} marked as read."


@mcp.tool()
def archive_email(message_id: str) -> str:
    """Archive an email (remove from inbox but keep in All Mail).

    Args:
        message_id: The Gmail message ID
    """
    service = get_gmail_service()
    service.users().messages().modify(
        userId="me", id=message_id,
        body={"removeLabelIds": ["INBOX"]}
    ).execute()
    return f"Email {message_id} archived."


@mcp.tool()
def trash_email(message_id: str) -> str:
    """Move an email to trash.

    Args:
        message_id: The Gmail message ID
    """
    service = get_gmail_service()
    service.users().messages().trash(userId="me", id=message_id).execute()
    return f"Email {message_id} moved to trash."


if __name__ == "__main__":
    mcp.run(transport="stdio")
