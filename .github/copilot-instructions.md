# Copilot Instructions

## Project Overview

This is the **public data repository** for the NCAA Division I Indoor Track & Field Championships live team scoring tracker. It stores JSON data files pushed here by the private scraper repo after every 5-minute scrape run.

The full system consists of two repos:
- **Private code repo** (`ncaa-indoor-2026`): Contains `scraper/scrape.py`, GitHub Actions workflow, and `app.py` (Streamlit dashboard)
- **This public data repo**: Contains `data/live.json` and `data/pre_meet.json` — read by the Streamlit app via `raw.githubusercontent.com`

## Repository Structure

```
data/
    live.json       # written by scraper every 5 min, read by app.py
    pre_meet.json   # built once from PDF start list, static throughout meet
ReadMe.md           # full project spec — read this first for detailed context
```

## Key Architecture Decisions

- **No code runs here** — this repo is purely a data store
- `pre_meet.json` is built **once** before the meet from the official start list PDF and never changes
- `live.json` is overwritten every 5 minutes by the scraper in the private repo
- The Streamlit app reads both files via HTTPS from this public repo

## JSON Schemas

### pre_meet.json
```json
{
  "women": {
    "events": [{"event_name": "Women 60 Meter Dash", "entries": [{"name": "...", "team": "...", "seed": "7.08"}]}],
    "premeet_projections": {"TeamName": 57.5}
  },
  "men": { "events": [...], "premeet_projections": {...} }
}
```

### live.json
```json
{
  "meta": {"last_updated": "ISO8601", "meet_url": "...", "scrape_ok": true, "status": "live"},
  "meet_name": "...", "venue": "...", "dates": ["YYYY-MM-DD"],
  "_known_finals": ["Women 60 Meter Dash"],
  "women": {
    "team_scores": [{"team": "...", "actual": 0, "seed_projection": 0, "ceiling": 0, "mc_forecast": 0,
                     "premeet_projection": 0, "win_probability": 0, "top4_probability": 0, "events_scored": []}],
    "completed_finals": 0, "total_finals": 17,
    "events": [{"event_name": "...", "gender": "Women", "status": "final|scheduled",
                "round_type": "final|prelim", "results": [{"place": 1, "name": "...", "team": "...", "mark": "..."}]}],
    "timeline": [], "leverage": [], "scenarios": {}, "variance": {}
  },
  "men": {}
}
```

## Scoring Rules

| Place | Points |
|-------|--------|
| 1st   | 10     |
| 2nd   | 8      |
| 3rd   | 6      |
| 4th   | 5      |
| 5th   | 4      |
| 6th   | 3      |
| 7th   | 2      |
| 8th   | 1      |

- **Ties**: tied-place points are summed and split evenly
- **Relays**: scored identically to individual events
- **Multi-events** (Heptathlon/Pentathlon): only overall totals count — sub-events are excluded
- **Field events**: higher mark = better (sort descending); **all other events**: lower time = better

## Critical Rules for data files

1. **Team name canonicality**: `pre_meet.json` must use the exact team name strings FlashResults displays. These become the canonical names everywhere.
2. **`_known_finals` must be `[]`** before the meet starts — stale entries prevent re-scraping events.
3. **`live.json` reset state**: before meet, set `scrape_ok: false`, `status: "meet_not_started"`, `_known_finals: []`
4. **`event_name` values** must match exact FlashResults format (e.g. `"Women 60 Meter Dash"`, `"Men Distance Medley"`)

## When Modifying Data Files

- Validate JSON is well-formed before committing
- Do not add fields not defined in the schema above
- Do not rename or reorder top-level keys (`women`/`men`, `meta`, `_known_finals`)
- `premeet_projections` in `pre_meet.json` is computed once and must not be recalculated manually

## No Build / Test Pipeline

This repo contains only data files (JSON) and documentation. There is no build system, test suite, or linter to run. Validate changes by checking JSON syntax only.
