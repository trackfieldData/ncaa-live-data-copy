#!/usr/bin/env python3
"""NCAA Indoor Track & Field Live Team Scoring Tracker — Scraper

Runs every 5 minutes via GitHub Actions. Scrapes FlashResults, computes
four scoring layers (actual, seed_projection, ceiling, mc_forecast), and
writes live.json. Also pushes live.json to the public data repo.
"""

import base64
import json
import os
import random
import re
from collections import defaultdict
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# ─── Configuration ────────────────────────────────────────────────────────────
MEET_URL = os.environ.get(
    "MEET_URL",
    "https://flashresults.com/2026_Meets/Indoor/03-13_NCAA",
)
MEET_NAME = "NCAA Division I 2026 Indoor Championships"
VENUE = "Randal Tyson Track Center"
DATES = ["2026-03-13", "2026-03-14"]

# Public data repo that live.json is pushed to after every scrape
DATA_REPO = os.environ.get("DATA_REPO", "trackfieldData/ncaa-live-data-copy")
DATA_BRANCH = os.environ.get("DATA_BRANCH", "main")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PRE_MEET_FILE = os.path.join(_SCRIPT_DIR, "..", "data", "pre_meet.json")
LIVE_JSON_FILE = os.path.join(_SCRIPT_DIR, "..", "data", "live.json")

# ─── Scoring constants ────────────────────────────────────────────────────────
PLACE_POINTS: dict[int, int] = {1: 10, 2: 8, 3: 6, 4: 5, 5: 4, 6: 3, 7: 2, 8: 1}

# Events where a higher mark is better (sort descending for ranking)
_FIELD_KEYWORDS = frozenset(
    {
        "shot put",
        "weight throw",
        "long jump",
        "triple jump",
        "high jump",
        "pole vault",
        "heptathlon",
        "pentathlon",
        "decathlon",
    }
)

MC_ITERATIONS = 2000
MC_SIGMA = 2.0  # Gaussian σ in rank units

# ─── Team name normalization ──────────────────────────────────────────────────
# Only genuine FlashResults format inconsistencies belong here.
# Do NOT abbreviate full names (e.g. "Kansas State" → "KS State") — that
# silently zeroes out premeet_projections for those teams.
_TEAM_NAME_MAP: dict[str, str] = {
    "Miami (FL)": "Miami (Fla.)",
    "North Carolina State": "NC State",
    "Eastern Carolina": "East Carolina",
    "Washington State": "Washington St.",
}


def _normalize_team(team: str) -> str:
    return _TEAM_NAME_MAP.get(team.strip(), team.strip())


# ─── Utility helpers ──────────────────────────────────────────────────────────
def _is_field_event(event_name: str) -> bool:
    """Return True if higher mark = better (field/multi-events)."""
    name_lower = event_name.lower()
    return any(kw in name_lower for kw in _FIELD_KEYWORDS)


def _parse_seed_float(seed: str) -> float:
    """Convert a seed string ('7.08', '1:59.12', '4635') to a comparable float.

    For time strings (containing ':'), returns total seconds so that a
    lower value sorts as better (ascending). For bare floats / integers,
    returns the numeric value directly.
    """
    if not seed:
        return 0.0
    s = seed.strip()
    try:
        if ":" in s:
            parts = s.split(":")
            if len(parts) == 2:
                return float(parts[0]) * 60 + float(parts[1])
            if len(parts) == 3:
                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _seed_rank(entries: list[dict], event_name: str) -> list[dict]:
    """Return entries sorted by seed — descending for field events, ascending otherwise."""
    reverse = _is_field_event(event_name)
    return sorted(entries, key=lambda e: _parse_seed_float(e.get("seed", "")), reverse=reverse)


def _normalize_event_name(raw: str) -> str:
    """Canonicalize relay / DMR event names so they match pre_meet.json keys."""
    n = raw.strip()
    # 4×400 relay variants
    n = re.sub(r"(?i)4\s*[xX×]\s*400\s*[mM]\.?\s*[Rr]elay", "4x400 Meter Relay", n)
    n = re.sub(r"(?i)4\s*[xX×]\s*400(?!\s*[mM])", "4x400 Meter Relay", n)
    # DMR variants
    n = re.sub(r"(?i)distance\s+medley(\s+relay)?", "Distance Medley Relay", n)
    n = re.sub(r"(?i)\bDMR\b", "Distance Medley Relay", n)
    return n


def _is_multi_sub_event(event_name: str) -> bool:
    """Return True for individual sub-events of multi-events.

    Examples of sub-events: "60 M - Heptathlon", "Women LJ - Pentathlon"
    Examples of totals pages (NOT sub-events): "Men Heptathlon", "Women Pentathlon"
    The distinguishing feature is the ' - ' separator between sub-event name and
    multi-event name.
    """
    name_lower = event_name.lower()
    is_multi = any(k in name_lower for k in ("heptathlon", "pentathlon", "decathlon"))
    if not is_multi:
        return False
    # The totals/scores page event name does NOT contain ' - '
    return " - " in event_name


# ─── Pre-meet data helpers ────────────────────────────────────────────────────
def _get_premeet_entries(pre_meet: dict, gender: str, event_name: str) -> list[dict]:
    """Return the entries list from pre_meet.json for a given gender + event."""
    gender_key = gender.lower()
    for ev in pre_meet.get(gender_key, {}).get("events", []):
        if ev.get("event_name") == event_name:
            return ev.get("entries", [])
    return []


# ─── Points awarding ──────────────────────────────────────────────────────────
def _award_points(results: list[dict]) -> dict[str, float]:
    """Award place points from a results list; ties split evenly.

    Args:
        results: list of dicts with at least 'place' (int) and 'team' (str).

    Returns:
        {team: points} for places 1-8.
    """
    by_place: dict[int, list[str]] = defaultdict(list)
    for r in results:
        place = r.get("place")
        if isinstance(place, int) and 1 <= place <= 8:
            by_place[place].append(r["team"])

    team_pts: dict[str, float] = defaultdict(float)
    for place, teams in sorted(by_place.items()):
        n = len(teams)
        pts_sum = sum(PLACE_POINTS.get(place + j, 0) for j in range(n))
        pts_each = pts_sum / n
        for t in teams:
            team_pts[t] += pts_each
    return dict(team_pts)


# ─── Seed-based projection helpers ────────────────────────────────────────────
def _project_top8_points(entries: list[dict], event_name: str) -> dict[str, float]:
    """Project top-8 points from entries based on seed rank.

    Handles seed ties: athletes with the same seed share the tied places.
    """
    ranked = _seed_rank(entries, event_name)
    team_pts: dict[str, float] = defaultdict(float)

    i = 0
    place = 1
    while i < len(ranked) and place <= 8:
        # Count how many share the same seed (ties)
        cur_seed = ranked[i].get("seed", "")
        j = i + 1
        while j < len(ranked) and ranked[j].get("seed", "") == cur_seed:
            j += 1
        tie_count = j - i
        pts_sum = sum(PLACE_POINTS.get(place + k, 0) for k in range(tie_count))
        pts_each = pts_sum / tie_count
        for k in range(tie_count):
            if place + k <= 8:
                team_pts[ranked[i + k]["team"]] += pts_each
        i = j
        place += tie_count

    return dict(team_pts)


# ─── Scoring layers ────────────────────────────────────────────────────────────
def compute_actual(gender_events: list[dict]) -> dict[str, float]:
    """Sum points from all completed finals."""
    totals: dict[str, float] = defaultdict(float)
    for ev in gender_events:
        if ev.get("status") == "final" and not ev.get("is_multi_sub_event"):
            for team, pts in _award_points(ev.get("results", [])).items():
                totals[team] += pts
    return dict(totals)


def compute_seed_projection(
    gender_events: list[dict],
    pre_meet: dict,
    gender: str,
    actual: dict[str, float],
) -> dict[str, float]:
    """Actual + seed-based forecast for remaining events.

    Entry priority per event (spec bug #1):
      prelim_results → start_list_entries → pre_meet.json entries
    Never skip an event due to missing data.
    """
    totals = dict(actual)
    for ev in gender_events:
        if ev.get("status") == "final" or ev.get("is_multi_sub_event"):
            continue
        entries = (
            ev.get("prelim_results")
            or ev.get("start_list_entries")
            or _get_premeet_entries(pre_meet, gender, ev["event_name"])
        )
        if not entries:
            continue
        for team, pts in _project_top8_points(entries, ev["event_name"]).items():
            totals[team] = totals.get(team, 0.0) + pts
    return totals


def compute_ceiling(
    gender_events: list[dict],
    pre_meet: dict,
    gender: str,
    actual: dict[str, float],
) -> dict[str, float]:
    """Actual + optimistic ceiling (considers top-12 seeds).

    Any team with a top-12 seed gets upside credit (best possible place = 1).
    """
    totals = dict(actual)
    for ev in gender_events:
        if ev.get("status") == "final" or ev.get("is_multi_sub_event"):
            continue
        entries = ev.get("start_list_entries") or _get_premeet_entries(
            pre_meet, gender, ev["event_name"]
        )
        if not entries:
            continue
        ranked = _seed_rank(entries, ev["event_name"])
        top12 = ranked[:12]
        # Award points optimistically: best place for each top-12 team
        seen: set[str] = set()
        place = 1
        for entry in top12:
            team = entry["team"]
            if team not in seen and place <= 8:
                totals[team] = totals.get(team, 0.0) + PLACE_POINTS.get(place, 0)
                seen.add(team)
                place += 1
    return totals


def compute_monte_carlo(
    gender_events: list[dict],
    pre_meet: dict,
    gender: str,
    actual: dict[str, float],
    n_iter: int = MC_ITERATIONS,
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """Monte Carlo simulation; returns (mc_forecast, win_probability, top4_probability).

    For each iteration, perturbs seed ranks with Gaussian noise (σ ≈ 2.0) and
    accumulates team totals. Never skips an event due to missing data.
    """
    upcoming = [
        ev
        for ev in gender_events
        if ev.get("status") != "final" and not ev.get("is_multi_sub_event")
    ]

    all_teams: set[str] = set(actual.keys())
    event_entry_lists: list[tuple[str, list[dict]]] = []
    for ev in upcoming:
        entries = ev.get("start_list_entries") or _get_premeet_entries(
            pre_meet, gender, ev["event_name"]
        )
        if entries:
            ranked = _seed_rank(entries, ev["event_name"])
            event_entry_lists.append((ev["event_name"], ranked))
            for e in ranked:
                all_teams.add(e["team"])

    all_teams_list = sorted(all_teams)

    if not event_entry_lists:
        # No upcoming events — MC equals actual
        n = len(all_teams_list)
        win_p = {t: round(100.0 / n, 1) if n else 0.0 for t in all_teams_list}
        top4_p = {t: round(min(100.0, 400.0 / n), 1) if n else 0.0 for t in all_teams_list}
        return dict(actual), win_p, top4_p

    win_counts: dict[str, int] = defaultdict(int)
    top4_counts: dict[str, int] = defaultdict(int)
    mc_totals: dict[str, float] = defaultdict(float)

    for _ in range(n_iter):
        iter_totals: dict[str, float] = dict(actual)
        for _event_name, ranked in event_entry_lists:
            if not ranked:
                continue
            n = len(ranked)
            # Perturb ranks with Gaussian noise then re-sort
            noisy = sorted(range(n), key=lambda i: i + random.gauss(0, MC_SIGMA))
            finishers = [ranked[i] for i in noisy[:8]]
            for place, entry in enumerate(finishers, 1):
                pts = PLACE_POINTS.get(place, 0)
                iter_totals[entry["team"]] = iter_totals.get(entry["team"], 0.0) + pts

        for team, pts in iter_totals.items():
            mc_totals[team] += pts

        sorted_iter = sorted(iter_totals.items(), key=lambda x: -x[1])
        if sorted_iter:
            win_counts[sorted_iter[0][0]] += 1
            for team, _ in sorted_iter[:4]:
                top4_counts[team] += 1

    mc_forecast = {t: round(mc_totals.get(t, 0.0) / n_iter, 1) for t in all_teams_list}
    win_prob = {t: round(100.0 * win_counts.get(t, 0) / n_iter, 1) for t in all_teams_list}
    top4_prob = {t: round(100.0 * top4_counts.get(t, 0) / n_iter, 1) for t in all_teams_list}
    return mc_forecast, win_prob, top4_prob


# ─── FlashResults scraping ────────────────────────────────────────────────────
def _fetch(url: str, timeout: int = 15) -> str | None:
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NCAAScorer/1.0)"},
        )
        resp.raise_for_status()
        return resp.text
    except (requests.RequestException, OSError) as exc:
        print(f"[WARN] fetch failed: {url} — {exc}")
        return None


def fetch_event_list(meet_url: str) -> list[dict]:
    """Parse the FlashResults index page and return a list of event dicts."""
    html = _fetch(f"{meet_url}/index.htm")
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    events: list[dict] = []

    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        link_text: str = a.get_text(strip=True)

        # Only process links that look like event result/score pages
        if not re.search(r"\d{3}-\d+", href):
            continue

        is_compiled = "_compiled.htm" in href.lower()
        is_scores = "_scores.htm" in href.lower()
        is_start = "_start.htm" in href.lower()
        is_prelim_heat = bool(
            re.search(r"\d{3}-\d+\.htm$", href, re.IGNORECASE)
        ) and not is_compiled and not is_scores

        # Skip start list links (fetched separately on demand)
        if is_start:
            continue

        link_lower = link_text.lower()

        # Determine round type
        if "prelim" in link_lower or "heat" in link_lower or is_prelim_heat:
            round_type = "prelim"
        else:
            round_type = "final"

        # Detect multi-events and their sub-events (spec bug #3)
        is_multi = any(k in link_lower for k in ("heptathlon", "pentathlon", "decathlon"))
        is_sub = _is_multi_sub_event(link_text)

        # Determine gender
        if link_lower.startswith("women") or " women" in link_lower:
            gender = "Women"
        elif link_lower.startswith("men") or " men" in link_lower:
            gender = "Men"
        else:
            gender = "Unknown"

        event_name = _normalize_event_name(link_text)

        url_match = re.search(r"(\d{3}-\d+)", href)
        if not url_match:
            continue
        event_num = url_match.group(1)

        events.append(
            {
                "event_name": event_name,
                "gender": gender,
                "round_type": round_type,
                "is_multi_event": is_multi and not is_sub,
                "is_multi_sub_event": is_sub,
                "event_num": event_num,
                "compiled_url": f"{meet_url}/{event_num}_compiled.htm",
                "start_url": f"{meet_url}/{event_num}_start.htm",
                "scores_url": f"{meet_url}/{event_num}_Scores.htm" if is_multi else None,
                "prelim_url": f"{meet_url}/{event_num}.htm",
                "status": "scheduled",
                "results": [],
                "prelim_results": [],
                "start_list_entries": [],
            }
        )

    return events


def scrape_results(event: dict) -> list[dict]:
    """Scrape compiled or scores page for a completed event."""
    url = (
        event.get("scores_url") or event["compiled_url"]
        if event.get("is_multi_event")
        else event["compiled_url"]
    )
    html = _fetch(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []

    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        place_text = cells[0].get_text(strip=True)
        try:
            place = int(place_text)
        except ValueError:
            continue
        if place > 8:
            continue

        stats_link = row.find("a", class_="openStats")
        if stats_link and stats_link.get("stats-name"):
            parts = stats_link["stats-name"].split("|")
            name = parts[0].strip()
            team = _normalize_team(parts[1]) if len(parts) > 1 else ""
        else:
            name = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            team = _normalize_team(cells[2].get_text(strip=True)) if len(cells) > 2 else ""

        mark = cells[-1].get_text(strip=True) if cells else ""

        if not team:
            continue

        results.append({"place": place, "name": name, "team": team, "mark": mark})

    return results


def scrape_start_list(event: dict) -> list[dict]:
    """Fetch a start list page and return entries with name, team, seed."""
    html = _fetch(event["start_url"])
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict] = []

    for a in soup.find_all("a", class_="openStats"):
        stats_name = a.get("stats-name", "")
        if not stats_name:
            continue
        parts = stats_name.split("|")
        name = parts[0].strip()
        team = _normalize_team(parts[1]) if len(parts) > 1 else ""

        # Seed mark is in the next sibling <td>
        seed = ""
        parent_td = a.find_parent("td")
        if parent_td:
            next_td = parent_td.find_next_sibling("td")
            if next_td:
                seed = next_td.get_text(strip=True)

        if team:
            entries.append({"name": name, "team": team, "seed": seed})

    return entries


def scrape_prelim_qualifiers(event: dict) -> list[dict]:
    """Parse prelim heat pages and return qualifiers (rows marked Q or q)."""
    html = _fetch(event["prelim_url"])
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    qualifiers: list[dict] = []

    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        row_text = " ".join(c.get_text(strip=True) for c in cells)
        if "Q" not in row_text and "q" not in row_text:
            continue

        stats_link = row.find("a", class_="openStats")
        if stats_link and stats_link.get("stats-name"):
            parts = stats_link["stats-name"].split("|")
            name = parts[0].strip()
            team = _normalize_team(parts[1]) if len(parts) > 1 else ""
        else:
            name = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            team = _normalize_team(cells[2].get_text(strip=True)) if len(cells) > 2 else ""

        mark = cells[-1].get_text(strip=True) if cells else ""
        if team:
            qualifiers.append({"name": name, "team": team, "seed": mark})

    return qualifiers


# ─── Timeline helpers ─────────────────────────────────────────────────────────
def _build_timeline_entry(event: dict) -> dict | None:
    results = event.get("results", [])
    winner = next((r for r in results if r.get("place") == 1), None)
    if not winner:
        return None
    return {
        "event": event["event_name"],
        "winner": winner.get("name", ""),
        "team": winner.get("team", ""),
        "mark": winner.get("mark", ""),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "points_awarded": _award_points(results),
    }


# ─── Leverage computation ─────────────────────────────────────────────────────
def compute_leverage(
    gender_events: list[dict], pre_meet: dict, gender: str
) -> list[dict]:
    """Return upcoming events sorted by total points at stake."""
    leverage = []
    for ev in gender_events:
        if ev.get("status") == "final" or ev.get("is_multi_sub_event"):
            continue
        entries = ev.get("start_list_entries") or _get_premeet_entries(
            pre_meet, gender, ev["event_name"]
        )
        if not entries:
            continue
        ranked = _seed_rank(entries, ev["event_name"])
        top8_teams = list(dict.fromkeys(e["team"] for e in ranked[:8]))
        leverage.append(
            {
                "event": ev["event_name"],
                "total_points": sum(PLACE_POINTS.values()),
                "top_teams": top8_teams[:5],
                "n_entries": len(entries),
            }
        )
    return sorted(leverage, key=lambda x: -x["total_points"])


# ─── Scenario computation ─────────────────────────────────────────────────────
def compute_scenarios(
    gender_events: list[dict],
    pre_meet: dict,
    gender: str,
    actual: dict[str, float],
    premeet_proj: dict[str, float],
) -> dict[str, dict]:
    """Compute per-team scenario_a/b/c breakdowns for the Team Detail tab."""
    all_teams = set(actual.keys()) | set(premeet_proj.keys())
    scenarios: dict[str, dict] = {}

    for team in sorted(all_teams):
        current = actual.get(team, 0.0)
        breakdown = []
        scenario_a_total = current
        scenario_b_total = current

        for ev in gender_events:
            if ev.get("status") == "final" or ev.get("is_multi_sub_event"):
                continue
            event_name = ev["event_name"]
            entries = (
                ev.get("prelim_results")
                or ev.get("start_list_entries")
                or _get_premeet_entries(pre_meet, gender, event_name)
            )
            if not entries:
                continue

            ranked = _seed_rank(entries, event_name)
            team_entries = [e for e in ranked if e["team"] == team]
            if not team_entries:
                continue

            athletes = []
            scenario_a_pts = 0.0
            for entry in team_entries:
                proj_place = next(
                    (i + 1 for i, e in enumerate(ranked) if e["name"] == entry["name"]),
                    None,
                )
                seed_pts = (
                    PLACE_POINTS.get(proj_place, 0) if proj_place and proj_place <= 8 else 0
                )
                athletes.append(
                    {
                        "athlete": entry["name"],
                        "seed_mark": entry.get("seed", ""),
                        "proj_place": proj_place,
                        "seed_pts": seed_pts,
                    }
                )
                scenario_a_pts += seed_pts

            # Ceiling: best possible place for this team = 1st
            scenario_b_pts = PLACE_POINTS[1]

            # Swing athletes: seeds 9-12 who could bump into finals
            swing = [
                {"athlete": e["name"], "seed_mark": e.get("seed", "")}
                for e in ranked[8:12]
                if e["team"] == team
            ]

            breakdown.append(
                {
                    "event": event_name,
                    "athletes": athletes,
                    "scenario_a_pts": scenario_a_pts,
                    "scenario_b_pts": scenario_b_pts,
                    "scenario_c_pts": 0,
                    "swing_athletes": swing,
                }
            )
            scenario_a_total += scenario_a_pts
            scenario_b_total += scenario_b_pts

        scenarios[team] = {
            "team": team,
            "current": current,
            "scenario_a": round(scenario_a_total, 1),
            "scenario_b": round(scenario_b_total, 1),
            "scenario_c": 0.0,
            "breakdown": breakdown,
        }
    return scenarios


# ─── Per-gender orchestration ─────────────────────────────────────────────────
def compute_gender_data(
    all_events: list[dict],
    pre_meet: dict,
    gender: str,
    premeet_proj: dict[str, float],
    existing_timeline: list[dict],
) -> dict:
    """Compute all scoring layers and build the gender section of live.json."""
    gender_events = [e for e in all_events if e.get("gender") == gender]

    actual = compute_actual(gender_events)
    seed_proj = compute_seed_projection(gender_events, pre_meet, gender, actual)
    ceiling = compute_ceiling(gender_events, pre_meet, gender, actual)
    mc_forecast, win_prob, top4_prob = compute_monte_carlo(
        gender_events, pre_meet, gender, actual
    )

    # Collect all teams seen in any scoring dict
    all_teams: set[str] = set()
    for d in (actual, seed_proj, ceiling, mc_forecast, premeet_proj):
        all_teams |= set(d.keys())

    team_scores = []
    for team in sorted(all_teams):
        scored_events = []
        for ev in gender_events:
            if ev.get("status") == "final" and not ev.get("is_multi_sub_event"):
                pts = _award_points(ev.get("results", []))
                if team in pts:
                    scored_events.append(f"{ev['event_name']} ({pts[team]:.1f}pt)")
        team_scores.append(
            {
                "team": team,
                "actual": round(actual.get(team, 0.0), 1),
                "seed_projection": round(seed_proj.get(team, actual.get(team, 0.0)), 1),
                "ceiling": round(ceiling.get(team, actual.get(team, 0.0)), 1),
                "mc_forecast": round(mc_forecast.get(team, actual.get(team, 0.0)), 1),
                "premeet_projection": round(premeet_proj.get(team, 0.0), 1),
                "win_probability": round(win_prob.get(team, 0.0), 1),
                "top4_probability": round(top4_prob.get(team, 0.0), 1),
                "events_scored": scored_events,
            }
        )
    team_scores.sort(key=lambda x: -x["seed_projection"])

    # Build new timeline entries for freshly completed events
    existing_events = {e["event"] for e in existing_timeline}
    new_timeline_entries = []
    for ev in gender_events:
        if ev.get("status") == "final" and not ev.get("is_multi_sub_event"):
            if ev["event_name"] not in existing_events:
                entry = _build_timeline_entry(ev)
                if entry:
                    new_timeline_entries.append(entry)
    merged_timeline = existing_timeline + new_timeline_entries

    completed = sum(
        1
        for ev in gender_events
        if ev.get("status") == "final" and not ev.get("is_multi_sub_event")
    )
    total = sum(
        1
        for ev in gender_events
        if ev.get("round_type") == "final" and not ev.get("is_multi_sub_event")
    )

    # Build seed_projection per event (for Events tab)
    for ev in gender_events:
        if ev.get("status") != "final" and not ev.get("is_multi_sub_event"):
            entries = (
                ev.get("prelim_results")
                or ev.get("start_list_entries")
                or _get_premeet_entries(pre_meet, gender, ev["event_name"])
            )
            ev["seed_projection"] = (
                _project_top8_points(entries, ev["event_name"]) if entries else {}
            )
        elif ev.get("status") == "final":
            ev["seed_projection"] = _award_points(ev.get("results", []))

    leverage = compute_leverage(gender_events, pre_meet, gender)
    scenarios = compute_scenarios(
        gender_events, pre_meet, gender, actual, premeet_proj
    )

    return {
        "team_scores": team_scores,
        "completed_finals": completed,
        "total_finals": total,
        "events": gender_events,
        "timeline": merged_timeline,
        "leverage": leverage,
        "scenarios": scenarios,
        "variance": {},
    }


# ─── GitHub push ──────────────────────────────────────────────────────────────
def push_to_github(
    content: str,
    path: str,
    repo: str,
    token: str,
    branch: str = "main",
    message: str = "chore: update live.json",
) -> None:
    """Push file content to a GitHub repo using the Contents API."""
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    params = {"ref": branch}

    # Retrieve current SHA so we can update (not create) the file
    sha = None
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    if resp.status_code == 200:
        sha = resp.json().get("sha")

    payload: dict = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(url, headers=headers, json=payload, timeout=15)
    if resp.status_code in (200, 201):
        print(f"[INFO] Pushed {path} to {repo}")
    else:
        print(f"[WARN] GitHub push failed ({resp.status_code}): {resp.text[:300]}")


# ─── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    # 1. Load pre_meet.json
    try:
        with open(PRE_MEET_FILE) as f:
            pre_meet = json.load(f)
    except FileNotFoundError:
        print("[ERROR] pre_meet.json not found — using empty scaffold")
        pre_meet = {
            "women": {"events": [], "premeet_projections": {}},
            "men": {"events": [], "premeet_projections": {}},
        }

    # 2. Load existing live.json (for timeline continuity and _known_finals)
    try:
        with open(LIVE_JSON_FILE) as f:
            existing_live = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing_live = {"_known_finals": []}

    known_finals: list[str] = existing_live.get("_known_finals", [])
    existing_women_timeline: list[dict] = existing_live.get("women", {}).get("timeline", [])
    existing_men_timeline: list[dict] = existing_live.get("men", {}).get("timeline", [])

    # 3. Fetch event list from FlashResults index page
    print(f"[INFO] Fetching event list from {MEET_URL}/index.htm")
    events = fetch_event_list(MEET_URL)

    if not events:
        print("[WARN] No events from FlashResults — building skeleton from pre_meet.json")
        for gender_key in ("women", "men"):
            gender_label = gender_key.capitalize()
            for ev in pre_meet.get(gender_key, {}).get("events", []):
                name = ev["event_name"]
                is_multi = any(k in name.lower() for k in ("heptathlon", "pentathlon"))
                events.append(
                    {
                        "event_name": name,
                        "gender": gender_label,
                        "round_type": "final",
                        "is_multi_event": is_multi,
                        "is_multi_sub_event": False,
                        "event_num": "",
                        "compiled_url": "",
                        "start_url": "",
                        "scores_url": None,
                        "prelim_url": "",
                        "status": "scheduled",
                        "results": [],
                        "prelim_results": [],
                        "start_list_entries": [],
                    }
                )

    # 4–6. For each event: determine status, scrape results or start lists
    for ev in events:
        if ev.get("is_multi_sub_event"):
            continue

        event_name = ev["event_name"]

        # Restore already-known finals from previous scrape run (_known_finals cache)
        if event_name in known_finals:
            ev["status"] = "final"
            # Restore results from the previous live.json snapshot
            for gender_key in ("women", "men"):
                for old_ev in existing_live.get(gender_key, {}).get("events", []):
                    if old_ev.get("event_name") == event_name:
                        ev["results"] = old_ev.get("results", [])
                        break
            continue

        if ev.get("round_type") == "final" and ev.get("compiled_url"):
            results = scrape_results(ev)
            if results:
                ev["status"] = "final"
                ev["results"] = results
                known_finals.append(event_name)
                print(f"[INFO] Final scraped: {event_name} ({len(results)} results)")
            else:
                # Not yet final — try to get start list for projections
                if ev.get("start_url"):
                    ev["start_list_entries"] = scrape_start_list(ev)
                    if ev["start_list_entries"]:
                        print(f"[INFO] Start list cached: {event_name} ({len(ev['start_list_entries'])} entries)")
        elif ev.get("round_type") == "prelim" and ev.get("prelim_url"):
            qualifiers = scrape_prelim_qualifiers(ev)
            if qualifiers:
                ev["prelim_results"] = qualifiers
                print(f"[INFO] Prelim qualifiers: {event_name} ({len(qualifiers)} qualifiers)")

    # 7. Compute scoring layers for each gender
    women_premeet = pre_meet.get("women", {}).get("premeet_projections", {})
    men_premeet = pre_meet.get("men", {}).get("premeet_projections", {})

    women_data = compute_gender_data(
        events, pre_meet, "Women", women_premeet, existing_women_timeline
    )
    men_data = compute_gender_data(
        events, pre_meet, "Men", men_premeet, existing_men_timeline
    )

    # 8. Write live.json
    live_json = {
        "meta": {
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "meet_url": MEET_URL,
            "scrape_ok": True,
            "status": "live",
        },
        "meet_name": MEET_NAME,
        "venue": VENUE,
        "dates": DATES,
        "_known_finals": known_finals,
        "women": women_data,
        "men": men_data,
    }

    with open(LIVE_JSON_FILE, "w") as f:
        json.dump(live_json, f, indent=2)
    print(f"[INFO] live.json written ({len(known_finals)} known finals)")

    # 9. Push live.json to public data repo
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        push_to_github(
            json.dumps(live_json, indent=2),
            "data/live.json",
            DATA_REPO,
            token,
            branch=DATA_BRANCH,
        )
    else:
        print("[INFO] GITHUB_TOKEN not set — skipping push to data repo")


if __name__ == "__main__":
    main()
