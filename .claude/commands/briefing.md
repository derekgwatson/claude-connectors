Run a briefing across all channels. Adapts automatically based on time of day.

## Mode detection
- Check get_briefing_summary() timestamps against local time (AEDT, UTC+11)
- **First run of the day** (last briefed before today local time): full morning briefing
- **Subsequent runs** (already briefed today): quick incremental check

## Full morning briefing
1. Call get_briefing_prefs() and follow any stored preferences
2. Call get_briefing_summary() to see what's stale
3. Call get_followups() to check for outstanding follow-ups — surface these first
4. For each stale channel, call get_channel_state() then query the channel for new items since last briefed
5. Present a concise summary grouped by channel, highlighting anything that needs action
6. Archive/cleanup per prefs (newsletters, Spiceworks, Zendesk notification emails, etc.)
7. After presenting, mark all channels as briefed using the appropriate mark_*_briefed tools
8. If any items need a reply or follow-up from the user, offer to add them as follow-ups
9. Check if any new items across channels appear related (same customer, same topic, same job). If so, suggest grouping them into a request using create_request and link_item. Also check if any new items relate to existing open requests (use list_requests or search_requests) and offer to link them.

## Quick incremental check (mid-day)
1. Call get_briefing_prefs() and follow any stored preferences
2. Call get_briefing_summary() to see what's changed since last run
3. Call get_followups() — flag any that are aging
4. For each channel with activity since last run, get new items only
5. Present a short summary — just what's new, skip the full ceremony
6. Skip bulk newsletter/notification cleanup (that's a morning task)
7. Mark channels as briefed
8. Link new items to existing requests where relevant

## Request status change cleanup (applies any time)
When the user changes a request's status (closes, marks pending, etc.), automatically:
1. **Gmail** — archive any emails linked to that request
2. **Zendesk** — check if the linked ticket status matches. If not, warn the user and ask before changing it (e.g. "ticket #658950 is still open in Zendesk — want me to solve it?")
3. **GChat/SMS** — no archive action needed, skip silently
Do this inline without being asked — it's part of closing/updating a request.
