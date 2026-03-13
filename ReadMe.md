NCAA Indoor Track & Field Live Team Scoring Tracker — Project Spec
Overview
Build a live team scoring tracker for the NCAA Division I Indoor Track & Field Championships (or any future FlashResults-based meet). The system scrapes live results every 5 minutes, runs multiple scoring projection layers, and serves a Streamlit dashboard. It is designed as a two-repo GitHub architecture: one private repo for scraping code, one public repo for data.
The project has three components:
`pre_meet.json` — a static file built once from the official start list PDF before the meet
`scrape.py` — a Python scraper that runs on a GitHub Actions cron and writes `live.json`
`app.py` — a Streamlit app that reads `live.json` and renders the dashboard (no scraping)
---
Repository Architecture
Private code repo (`ncaa-indoor-2026`)
```
scraper/
    scrape.py          # main scraper
.github/
    workflows/
        scrape.yml     # GitHub Actions cron workflow
data/
    pre_meet.json      # built once from PDF start list — committed here and mirrored
    live.json          # written by scraper every 5 min
app.py                 # Streamlit dashboard
requirements.txt
```
Public data repo (`trackfieldstats/ncaa-indoor-2026-data`)
```
data/
    live.json          # pushed here after every scrape run
    pre_meet.json      # static copy — app reads from here
```
The Streamlit app reads both JSON files from the public data repo via `raw.githubusercontent.com`. The scraper pushes `live.json` (and optionally `pre_meet.json`) to the public repo after every run using a GitHub token.
---
Scoring Rules
These rules are used in all projection calculations:
Place	Points
1st	10
2nd	8
3rd	6
4th	5
5th	4
6th	3
7th	2
8th	1
Ties: points for tied places are summed and split evenly (e.g. two teams tied for 2nd each get (8+6)/2 = 7.0)
Relays: scored identically to individual events
Multi-events (Heptathlon / Pentathlon): seeded by season-best total points descending; top 8 overall finishers score team points. Sub-events (60m split, LJ split, etc.) are completely ignored for team scoring.
Prelim events: the full prelim entry list is used for pre-meet seed projections; top 8 seeds score
Field events: higher mark = better (sort descending for ranking)
All other events: lower time = better (sort ascending)
Season Best (SB) is used as the seed mark throughout
---
pre_meet.json Schema
Built once from the official PDF start list. Never changes after the meet starts.
```json
{
  "women": {
    "events": [
      {
        "event_name": "Women 60 Meter Dash",
        "entries": [
          { "name": "Athlete Name", "team": "Team Name", "seed": "7.08" }
        ]
      }
    ],
    "premeet_projections": {
      "Illinois": 57.5,
      "BYU": 41.0
    }
  },
  "men": {
    "events": [...],
    "premeet_projections": { ... }
  }
}
```
Key rules for pre_meet.json
`event_name` must match the exact FlashResults event name format (e.g. `"Women 60 Meter Dash"`, `"Men Distance Medley"`)
`team` values must match the exact team name strings that FlashResults uses — these become the canonical team names everywhere in the system
`premeet_projections` is computed by seed-ranking each event's entries and awarding top-8 points. This is frozen forever once built.
Multi-events (Pentathlon, Heptathlon): entries use SB total points as the seed, sorted descending
---
Team Name Normalization
This is a critical source of bugs. The golden rule:
> `pre_meet.json` is built using the exact team name strings FlashResults displays. Therefore `_normalize_team()` in the scraper should almost never need to translate anything — the names already match.
The only legitimate entries in `_TEAM_NAME_MAP` are genuine format variants FlashResults uses inconsistently:
```python
_TEAM_NAME_MAP = {
    "Miami (FL)":           "Miami (Fla.)",       # FlashResults uses both
    "North Carolina State": "NC State",            # FlashResults uses both
    "Eastern Carolina":     "East Carolina",       # FlashResults uses both
    "Washington State":     "Washington St.",      # FlashResults uses both
}
```
Do not add abbreviations like `"Kansas State" -> "KS State"`. If the pre_meet.json uses the full name, the map must not shorten it.
---
scrape.py — Architecture
High-level flow (runs every 5 minutes)
```
1. Load pre_meet.json
2. Load existing live.json (for timeline continuity)
3. Fetch FlashResults meet index page → parse event list
4. For each event: check if it's a final, prelim, or multi-event
5. For completed finals: scrape results
6. For upcoming finals: pre-fetch start lists and cache on event object
7. Compute all scoring layers
8. Write live.json
9. Push live.json to public data repo
```
Event URL pattern on FlashResults
Meet index: `https://flashresults.com/2026_Meets/Indoor/03-13_NCAA/index.htm`
FlashResults event URL patterns:
Results: `<MEET_URL>/005-1_compiled.htm` (number varies per event)
Start list: `<MEET_URL>/005-1_start.htm`
Prelim results: `<MEET_URL>/005-1.htm` (heat/section page)
Multi-event totals: `<MEET_URL>/005-1_Scores.htm`
The scraper parses the index page to discover event links dynamically.
Event detection logic
From the index page link text:
`round_type = "final"` if link contains "Final" or is a standalone distance event
`round_type = "prelim"` if link contains "Prelim"
`is_multi_event = True` if event name contains "heptathlon", "pentathlon", or "decathlon"
Multi-event sub-events (e.g. "60 M - Heptathlon") must be detected and excluded from team scoring — only the overall totals page counts
Start list caching
Before computing projections, pre-fetch all start lists for upcoming finals and cache them on the event dict:
```python
event["start_list_entries"] = scrape_start_list(event)
# entries: [{"name": str, "team": str, "seed": str}, ...]
```
Start list HTML uses `<a class="openStats" stats-name="AthleteFirstLast|TeamName">` tags. Parse `stats-name`, normalize the team, read the SB mark from the next sibling `<td>`.
---
Scoring Computation Layers
All four layers are computed per-gender in each scrape run:
1. `actual` — points scored so far
Sum PLACE_POINTS for all completed finals. Ties split points.
2. `seed_projection` — actual + seed-based forecast for remaining events
For each upcoming final:
Use `prelim_results` if prelims have run (actual qualifiers)
Else use `start_list_entries` if available
Else fall back to `pre_meet.json` entries ← critical for pre-meet accuracy
Then seed-rank entries and project top-8 points.
3. `ceiling` — actual + optimistic ceiling
Same logic as seed_projection, but considers top-12 seeds. Any team with a top-12 seed gets "upside" credit. Always falls back to pre_meet.json entries.
4. `mc_forecast` + `win_probability` + `top4_probability` — Monte Carlo simulation
Run N=2000 iterations per gender. In each iteration, for each upcoming event, sample finishers by perturbing seed ranks with Gaussian noise (σ ≈ 2.0 places). Accumulate team totals across all events, add to actual. Count win and top-4 outcomes across iterations.
Always use: `start_list_entries or _get_premeet_entries(pre_meet, gender, event_name)` — never skip an event due to missing data.
---
live.json Schema
Written by the scraper every 5 minutes.
```json
{
  "meta": {
    "last_updated": "2026-03-13T19:45:00Z",
    "meet_url": "https://flashresults.com/2026_Meets/Indoor/03-13_NCAA",
    "scrape_ok": true,
    "status": "live"
  },
  "meet_name": "NCAA Division I 2026 Indoor Championships",
  "venue": "Randal Tyson Track Center",
  "dates": ["2026-03-13", "2026-03-14"],
  "_known_finals": ["Women 60 Meter Dash", "Men Shot Put"],
  "women": {
    "team_scores": [
      {
        "team": "Georgia",
        "actual": 16.0,
        "seed_projection": 42.9,
        "ceiling": 88.0,
        "mc_forecast": 39.1,
        "premeet_projection": 35.5,
        "win_probability": 36.6,
        "top4_probability": 88.0,
        "events_scored": ["Women 60 Meter Dash (1=10.0pt)"]
      }
    ],
    "completed_finals": 3,
    "total_finals": 17,
    "events": [
      {
        "event_name": "Women 60 Meter Dash",
        "gender": "Women",
        "status": "final",
        "round_type": "final",
        "day": "Saturday",
        "start_time": "4:30 PM",
        "results": [
          {"place": 1, "name": "Alicia Burnett", "team": "Ole Miss", "mark": "7.08"}
        ],
        "seed_projection": {"Ole Miss": 10.0, "Georgia": 8.0}
      }
    ],
    "timeline": [
      {"event": "Women 60 Meter Dash", "winner": "Alicia Burnett", "team": "Ole Miss",
       "mark": "7.08", "timestamp": "2026-03-14T21:32:00Z", "points_awarded": {"Ole Miss": 10.0}}
    ],
    "leverage": [...],
    "scenarios": {
      "Georgia": {
        "team": "Georgia",
        "current": 16.0,
        "scenario_a": 35.5,
        "scenario_b": 88.0,
        "scenario_c": 0.0,
        "breakdown": [
          {
            "event": "Women 200 Meter Dash",
            "athletes": [
              {"athlete": "Adaejah Hodge", "seed_mark": "22.32", "proj_place": 1, "seed_pts": 10.0}
            ],
            "scenario_a_pts": 10.0,
            "scenario_b_pts": 10.0,
            "scenario_c_pts": 0,
            "swing_athletes": []
          }
        ]
      }
    },
    "variance": {}
  },
  "men": { ... }
}
```
`_known_finals`
A list of event names the scraper has already confirmed as completed. Used to skip re-scraping finished events on subsequent runs. Reset to `[]` at meet start.
`scenarios` per team
`scenario_a` = seed projection (expected)
`scenario_b` = ceiling (everything goes right)
`scenario_c` = floor (currently always 0 — actual only, no upside)
`breakdown` = per-event detail with seeded athletes, projected places, and swing athletes (ranks 9-12 who could make finals)
---
GitHub Actions Workflow (`scrape.yml`)
```yaml
name: Scrape Live Results
on:
  schedule:
    - cron: '*/5 * * * *'   # every 5 minutes
  workflow_dispatch:         # manual trigger

jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python scraper/scrape.py
        env:
          GITHUB_TOKEN: ${{ secrets.DATA_REPO_TOKEN }}
      - name: Push live.json to public data repo
        run: |
          git config user.name "github-actions"
          git config user.email "actions@github.com"
          # push data/live.json to trackfieldstats/ncaa-indoor-2026-data
```
The scraper itself handles the push to the public data repo using the `GITHUB_TOKEN` environment variable and the GitHub API (or git push).
---
app.py — Streamlit Dashboard
Pure renderer. Never scrapes. Reads `live.json` and `pre_meet.json` from the public data repo via HTTPS.
Tabs
Leaderboard — team scores table with columns: Rank, Team, Actual, Seed Proj, Premeet Proj, MC Forecast, Win%, Top-4%
Events — list of all events with status (scheduled / in progress / final), results for completed events, seed projections for upcoming
Team Detail — select a team, see their scenario breakdown per event, swing athletes, projected places
Timeline — chronological feed of completed events with winner, mark, and points awarded
Leverage — which upcoming events have the most impact on the championship outcome
Auto-refresh
Refresh every 300 seconds using `st.rerun()` with `time.sleep()` or Streamlit's built-in rerun mechanism.
Color scheme
Dark theme (`#0d1117` background, `#f0c040` gold accent for leaders).
---
Event Schedule Reference (NCAA Indoor 2026)
Friday, March 13
Time	Event
4:00 PM	Men Weight Throw (F)
4:45 PM	Men Pole Vault (F), Women Long Jump (F)
5:05 PM	Women 1 Mile Prelims
5:20 PM	Men 1 Mile Prelims
5:35 PM	Women 60m Prelims
5:45 PM	Men 60m Prelims
5:55 PM	Women 400m Prelims
6:10 PM	Men 400m Prelims
6:25 PM	Women 800m Prelims
6:35 PM	Men 800m Prelims
6:45 PM	Women 60mH Prelims
6:55 PM	Men 60mH Prelims
7:00 PM	Men Long Jump (F)
7:05 PM	Women 5000m (F)
7:25 PM	Men 5000m (F)
7:30 PM	Women Weight Throw (F)
7:45 PM	Women 200m Prelims
8:00 PM	Men 200m Prelims
8:15 PM	Women DMR (F)
8:30 PM	Men DMR (F)
Multi-events Friday: Women Pentathlon (60m Hurdles, High Jump, Shot Put, Long Jump, 800 m), Men Heptathlon (60m, LJ, SP, HJ)
Saturday, March 14
Time	Event
1:00 PM	Men Shot Put (F)
1:45 PM	Men High Jump (F), Women High Jump (F)
2:30 PM	Women Triple Jump (F)
2:45 PM	Women Shot Put (F)
4:00 PM	Women Pole Vault (F)
4:10 PM	Women 1 Mile (F)
4:20 PM	Men 1 Mile (F)
4:30 PM	Women 60m (F)
4:40 PM	Men 60m (F)
4:50 PM	Women 400m (F)
5:00 PM	Men 400m (F), Men Triple Jump (F)
5:10 PM	Women 800m (F)
5:20 PM	Men 800m (F)
5:30 PM	Women 200m (F)
5:40 PM	Men 200m (F)
5:50 PM	Women 60mH (F)
6:00 PM	Men 60mH (F)
6:10 PM	Women 3000m (F)
6:25 PM	Men 3000m (F)
6:40 PM	Women 4x400 (F)
6:55 PM	Men 4x400 (F)
Multi-events Saturday: Men Heptathlon continues (60mH, PV, 1000m)
---
Known Bugs to Avoid
1. `compute_seed_projection` must fall back to pre_meet.json
When start list pages aren't live yet, `start_list_entries` will be empty. The function must fall back:
```python
entries = event.get("start_list_entries") or _get_premeet_entries(pre_meet, gender, event["event_name"])
```
Without this, all seed projections are 0 pre-meet. The ceiling and MC functions already do this — seed projection must too.
2. `_TEAM_NAME_MAP` must not abbreviate full names
If `pre_meet.json` is built using FlashResults full names (which it should be), the map must not translate `"Kansas State"` → `"KS State"` or similar. That breaks `premeet_proj.get(team, 0)` lookups, silently zeroing out premeet projections for those teams. Only add entries for genuine FlashResults format inconsistencies (see Team Name Normalization section above).
3. Multi-event sub-events must be excluded from team scoring
FlashResults lists individual sub-events (e.g. "Women 60 M - Pentathlon") as separate links. These must be detected and skipped entirely — only the `_Scores.htm` totals page counts for team points.
4. DMR / relay event name normalization
FlashResults may display "4X400 M Relay" or "4x400 m relay" — normalize to a canonical form for matching against pre_meet.json event names. Similarly "DMR" and "Distance Medley Relay" and "distance medley" should all resolve to the same event.
5. `_known_finals` must be reset before the meet
Before the first scrape of a new meet, `live.json` must have `"_known_finals": []`. If stale finals from a previous meet are in this list, events will never be re-scraped.
---
Pre-Meet Checklist
Before the meet starts, verify:
[ ] `MEET_URL` in `scrape.py` is set to the correct FlashResults URL
[ ] `pre_meet.json` has been built from the official start list PDF and committed
[ ] `live.json` has been reset: `_known_finals: []`, `scrape_ok: false`, `status: "meet_not_started"`
[ ] Heptathlon `_Scores.htm` URL pattern confirmed on FlashResults (may vary by meet)
[ ] GitHub Actions `scrape.yml` is enabled in the repo
[ ] `DATA_REPO_TOKEN` secret is set in the code repo with write access to the public data repo
[ ] Streamlit app `GITHUB_RAW_BASE` secret points to the correct public data repo
---
Requirements
```
requests
beautifulsoup4
streamlit
plotly
```
Python 3.11+. No database — all state lives in `live.json`.
