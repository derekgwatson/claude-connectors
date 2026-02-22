# Claude Connectors

## Briefing Workflow
- At the start of every briefing, call get_briefing_prefs() and follow stored preferences
- Prefs are stored in the remote DB (briefing_prefs table), machine-independent
- Briefing state (what's been seen/briefed) is also in the remote DB — check get_briefing_summary() first
