# Claude Connectors - Status & TODOs

## DONE: Briefing State Server - 404 Errors (2026-02-23)

**Resolved:** Server is back up and responding as of 2026-02-23. Prefs and summary endpoints working.

## DONE: Migrate Email Cleanup Prefs to Cloud DB (2026-02-23)

**Completed:** All email cleanup prefs migrated to cloud DB via `set_briefing_pref`. Now available from any machine.

## Setup: /briefing Slash Command (updated 2026-02-27)

The `/briefing` command lives at `.claude/commands/briefing.md` in this repo. It auto-detects whether it's the first run of the day or a mid-day check-in based on local time (AEDT).

### Per-machine setup:
1. Clone this repo and ensure `.mcp.json` servers are working (gmail, zendesk, gchat, briefing)
2. Ensure `.env` files exist for each connector (API keys, OAuth tokens)
3. Run `/briefing` from within the project directory

### Modes (auto-detected):
- **Morning (first run of the day):** Full briefing with channel sweep, newsletter/notification cleanup, follow-up review, request linking
- **Mid-day (subsequent runs):** Quick incremental check — just what's new since last run, skip bulk cleanup

### Request cleanup:
When a request status changes (closed, pending, etc.), automatically:
- Archive linked Gmail emails
- Warn if linked Zendesk ticket status doesn't match (ask before changing)
- GChat/SMS — no archive action needed

## TODO: GChat Connector - Add send_message capability (2026-02-23)

**Priority:** Normal

The GChat MCP connector is currently read-only. Need to add a `send_message` tool so we can reply to DMs and spaces directly from the briefing workflow.

### What's needed:
- [ ] Add `send_message(space_name, text)` tool to the GChat connector
- [ ] Check Google Chat API scopes — may need `chat.messages.create` permission
- [ ] Update OAuth credentials if additional scopes are required
- [ ] Test in a DM and a Space to confirm formatting works correctly

## TODO: Fiona Fabric Tickets - Fix Zendesk Routing (2026-02-23)

**Priority:** Normal

When Fiona creates "New Curtain Fabric" tickets in Zendesk, they're likely landing in the Showroom group by default. Lisa has been manually reassigning them to Derek. Need to fix so they route to IT/Derek automatically.

### Options:
- [ ] **Option A (Zendesk side):** Add a trigger in Zendesk that matches on the `new-curtain-fabric` tag or `fiona` tag and reassigns to the correct group
- [ ] **Option B (Fiona side):** Update the Fiona bot to create the Zendesk ticket directly in Derek's group

### Notes:
- Fiona tickets have tags: `fiona`, `new-curtain-fabric`
- Current group ID on tickets: `360000876251` (verify which group this is)
