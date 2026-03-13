# AGENTS.md — NCAA Live Data Copy (Public Data Repo)

## What This Repo Is

This is the **public data repository** for the NCAA Division I Indoor Track & Field Championships live team scoring tracker. It holds two JSON data files that the Streamlit dashboard reads via `raw.githubusercontent.com`.

The scraper (in a separate private repo) pushes updates to `data/live.json` every 5 minutes during the meet.

## Repository Contents

| File | Description |
|------|-------------|
| `data/live.json` | Live meet data: scores, results, projections, Monte Carlo forecasts. Updated every 5 min. |
| `data/pre_meet.json` | Static pre-meet seed data built from the official start list PDF. Never changes after meet starts. |
| `ReadMe.md` | Full project specification — primary reference for schema, scoring rules, and architecture. |

## Scoring System (Summary)

Places 1–8 score 10, 8, 6, 5, 4, 3, 2, 1 points respectively. Ties split the combined points evenly. Relays score the same as individual events. Multi-event (Heptathlon/Pentathlon) sub-events are excluded — only overall totals count.

## Common Agent Tasks

### Updating pre_meet.json
- Build from official start list PDF
- Use **exact** FlashResults team name strings (these are canonical)
- Compute `premeet_projections` by seed-ranking each event and awarding top-8 points
- Commit once; do not modify after the meet starts

### Resetting live.json before a new meet
Set the following fields:
```json
{
  "_known_finals": [],
  "meta": { "scrape_ok": false, "status": "meet_not_started" }
}
```

### Fixing team name issues
- Only map names that FlashResults genuinely uses inconsistently
- Never abbreviate: `"Kansas State"` must not become `"KS State"`
- Names in `pre_meet.json` are the source of truth

## Validation

- Always validate JSON is well-formed (`python -m json.tool data/live.json`)
- There is no test suite or build system in this repo
- Do not add code files, scripts, or new top-level directories

## Related Repos

- **Private code repo** (`ncaa-indoor-2026`): `scraper/scrape.py`, `app.py`, GitHub Actions workflow
- **This public data repo**: data files only, consumed by the Streamlit app
