"""NCAA Indoor Track & Field Live Team Scoring Tracker — Streamlit Dashboard

Pure renderer: never scrapes. Reads live.json and pre_meet.json from the
public data repo every 300 seconds and updates the display.
"""

import os
import time
from typing import Any

import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# ─── Configuration ────────────────────────────────────────────────────────────
# Base URL for the public data repo on raw.githubusercontent.com.
# Override with the GITHUB_RAW_BASE environment variable or Streamlit secret.
_DEFAULT_RAW_BASE = (
    "https://raw.githubusercontent.com/trackfieldData/ncaa-live-data-copy/main/data"
)
try:
    _RAW_BASE = st.secrets.get("GITHUB_RAW_BASE", _DEFAULT_RAW_BASE)
except Exception:  # noqa: BLE001
    _RAW_BASE = os.environ.get("GITHUB_RAW_BASE", _DEFAULT_RAW_BASE)

LIVE_JSON_URL = f"{_RAW_BASE}/live.json"
PRE_MEET_JSON_URL = f"{_RAW_BASE}/pre_meet.json"

REFRESH_SECONDS = 300
PLACE_POINTS = {1: 10, 2: 8, 3: 6, 4: 5, 5: 4, 6: 3, 7: 2, 8: 1}

# ─── Dark theme palette ───────────────────────────────────────────────────────
BG = "#0d1117"
SURFACE = "#161b22"
GOLD = "#f0c040"
GREEN = "#3fb950"
TEXT = "#c9d1d9"
BORDER = "#30363d"

# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="NCAA Indoor T&F 2026 — Live Team Scores",
    page_icon="🏟️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── Global CSS ───────────────────────────────────────────────────────────────
st.markdown(
    f"""
    <style>
    html, body, [data-testid="stApp"] {{
        background-color: {BG};
        color: {TEXT};
    }}
    [data-testid="stSidebar"] {{
        background-color: {SURFACE};
    }}
    h1, h2, h3, h4 {{ color: {GOLD}; }}
    .stTabs [data-baseweb="tab"] {{
        color: {TEXT};
        background: {SURFACE};
        border-radius: 4px 4px 0 0;
    }}
    .stTabs [aria-selected="true"] {{
        color: {GOLD} !important;
        border-bottom: 2px solid {GOLD} !important;
    }}
    .metric-card {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 8px;
    }}
    .rank-1 {{ color: {GOLD}; font-weight: 700; }}
    .event-pill {{
        display: inline-block;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.75rem;
        margin-right: 4px;
    }}
    .pill-final {{ background: #1a472a; color: #3fb950; }}
    .pill-scheduled {{ background: #1c2333; color: #8b949e; }}
    .pill-live {{ background: #3d2200; color: {GOLD}; }}
    </style>
    """,
    unsafe_allow_html=True,
)


# ─── Data loading ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=REFRESH_SECONDS)
def load_json(url: str) -> dict:
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Could not load {url}: {exc}")
        return {}


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _gender_data(live: dict, gender: str) -> dict:
    return live.get(gender.lower(), {})


def _team_scores(live: dict, gender: str) -> list[dict]:
    return _gender_data(live, gender).get("team_scores", [])


def _events(live: dict, gender: str) -> list[dict]:
    return _gender_data(live, gender).get("events", [])


def _timeline(live: dict, gender: str) -> list[dict]:
    return _gender_data(live, gender).get("timeline", [])


def _leverage(live: dict, gender: str) -> list[dict]:
    return _gender_data(live, gender).get("leverage", [])


def _scenarios(live: dict, gender: str) -> dict[str, dict]:
    return _gender_data(live, gender).get("scenarios", {})


# ─── Tab renderers ────────────────────────────────────────────────────────────
def render_leaderboard(live: dict, gender: str) -> None:
    """Leaderboard tab: sortable team scores table."""
    scores = _team_scores(live, gender)
    if not scores:
        st.info("No scores yet — waiting for results.")
        return

    gd = _gender_data(live, gender)
    completed = gd.get("completed_finals", 0)
    total = gd.get("total_finals", 0)

    col1, col2, col3 = st.columns(3)
    col1.metric("Events Completed", f"{completed} / {total}")
    top = scores[0] if scores else {}
    col2.metric("Leader", top.get("team", "—"), f"{top.get('actual', 0):.0f} pts")
    col3.metric("MC Leader", top.get("team", "—"), f"{top.get('mc_forecast', 0):.0f} pts projected")

    st.markdown("---")

    rows = []
    for rank, s in enumerate(scores, 1):
        rows.append(
            {
                "Rank": rank,
                "Team": s["team"],
                "Actual": s["actual"],
                "Seed Proj": s["seed_projection"],
                "Pre-Meet Proj": s["premeet_projection"],
                "MC Forecast": s["mc_forecast"],
                "Win %": f"{s['win_probability']:.1f}%",
                "Top-4 %": f"{s['top4_probability']:.1f}%",
            }
        )

    import pandas as pd  # noqa: PLC0415

    df = pd.DataFrame(rows)

    # Highlight the top team row in gold
    def highlight_leader(row: Any) -> list[str]:
        if row["Rank"] == 1:
            return [f"color: {GOLD}; font-weight: bold"] * len(row)
        return [""] * len(row)

    st.dataframe(
        df.style.apply(highlight_leader, axis=1),
        use_container_width=True,
        hide_index=True,
    )

    # Bar chart: seed projections
    import pandas as pd  # noqa: F811,PLC0415

    top20 = scores[:20]
    fig = go.Figure(
        [
            go.Bar(
                name="Actual",
                x=[s["team"] for s in top20],
                y=[s["actual"] for s in top20],
                marker_color=GREEN,
            ),
            go.Bar(
                name="Projected (Seed)",
                x=[s["team"] for s in top20],
                y=[s["seed_projection"] - s["actual"] for s in top20],
                marker_color="#388bfd",
            ),
        ]
    )
    fig.update_layout(
        barmode="stack",
        paper_bgcolor=BG,
        plot_bgcolor=SURFACE,
        font_color=TEXT,
        legend=dict(font=dict(color=TEXT)),
        xaxis=dict(tickangle=-45),
        title="Team Scores — Actual + Projected Remainder",
        title_font_color=GOLD,
        height=420,
    )
    st.plotly_chart(fig, use_container_width=True)


def render_events(live: dict, gender: str) -> None:
    """Events tab: status and results / seed projections per event."""
    ev_list = _events(live, gender)
    if not ev_list:
        st.info("Event list not yet available.")
        return

    filter_col, _ = st.columns([2, 4])
    status_filter = filter_col.selectbox(
        "Filter by status", ["All", "Final", "Scheduled / Upcoming"]
    )

    for ev in ev_list:
        if ev.get("is_multi_sub_event"):
            continue
        status = ev.get("status", "scheduled")
        if status_filter == "Final" and status != "final":
            continue
        if status_filter == "Scheduled / Upcoming" and status == "final":
            continue

        pill_class = "pill-final" if status == "final" else "pill-scheduled"
        pill_label = "✅ Final" if status == "final" else "⏳ Scheduled"

        with st.expander(
            f"{ev['event_name']}  "
            f"<span class='event-pill {pill_class}'>{pill_label}</span>",
            expanded=(status == "final"),
        ):
            if status == "final":
                results = ev.get("results", [])
                if results:
                    import pandas as pd  # noqa: PLC0415

                    st.dataframe(
                        pd.DataFrame(results)[["place", "name", "team", "mark"]],
                        use_container_width=True,
                        hide_index=True,
                    )
            else:
                proj = ev.get("seed_projection", {})
                if proj:
                    import pandas as pd  # noqa: PLC0415

                    proj_rows = sorted(proj.items(), key=lambda x: -x[1])
                    st.caption("Seed Projection")
                    st.dataframe(
                        pd.DataFrame(proj_rows, columns=["Team", "Projected Pts"]),
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.caption("No seed data available yet.")


def render_team_detail(live: dict, gender: str) -> None:
    """Team Detail tab: per-team scenario breakdown."""
    scores = _team_scores(live, gender)
    if not scores:
        st.info("No team data yet.")
        return

    team_names = [s["team"] for s in scores]
    selected = st.selectbox("Select a team", team_names)

    team_score = next((s for s in scores if s["team"] == selected), None)
    if not team_score:
        return

    sc = _scenarios(live, gender).get(selected, {})

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Actual", f"{team_score['actual']:.1f}")
    c2.metric("Seed Proj", f"{team_score['seed_projection']:.1f}")
    c3.metric("Ceiling", f"{team_score['ceiling']:.1f}")
    c4.metric("MC Forecast", f"{team_score['mc_forecast']:.1f}")
    c5.metric("Pre-Meet Proj", f"{team_score['premeet_projection']:.1f}")

    st.markdown("---")

    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader("Win & Top-4 Probability")
        fig = go.Figure(
            go.Indicator(
                mode="gauge+number",
                value=team_score.get("win_probability", 0),
                title={"text": "Win Probability %", "font": {"color": TEXT}},
                gauge={
                    "axis": {"range": [0, 100]},
                    "bar": {"color": GOLD},
                    "bgcolor": SURFACE,
                    "bordercolor": BORDER,
                },
                number={"suffix": "%", "font": {"color": GOLD}},
            )
        )
        fig.update_layout(paper_bgcolor=BG, font_color=TEXT, height=260)
        st.plotly_chart(fig, use_container_width=True)

    with col_right:
        st.subheader("Scenario Summary")
        st.markdown(
            f"""
            | Scenario | Points |
            |---|---|
            | **Current (Actual)** | {sc.get('current', 0):.1f} |
            | **Scenario A (Seed Proj)** | {sc.get('scenario_a', 0):.1f} |
            | **Scenario B (Ceiling)** | {sc.get('scenario_b', 0):.1f} |
            | **Scenario C (Floor)** | {sc.get('scenario_c', 0):.1f} |
            """
        )

    # Per-event breakdown
    st.subheader("Event Breakdown")
    breakdown = sc.get("breakdown", [])
    if not breakdown:
        st.caption("No upcoming events for this team.")
    else:
        for ev_detail in breakdown:
            with st.expander(ev_detail["event"]):
                for ath in ev_detail.get("athletes", []):
                    st.markdown(
                        f"**{ath['athlete']}** — Seed: {ath['seed_mark']} | "
                        f"Proj place: {ath['proj_place']} | "
                        f"Pts: {ath['seed_pts']:.0f}"
                    )
                if ev_detail.get("swing_athletes"):
                    st.caption("Swing athletes (9-12 seeds who could make finals):")
                    for sw in ev_detail["swing_athletes"]:
                        st.markdown(f"  • {sw['athlete']} ({sw['seed_mark']})")
                c1, c2, c3 = st.columns(3)
                c1.metric("Scenario A", f"{ev_detail['scenario_a_pts']:.0f} pts")
                c2.metric("Scenario B", f"{ev_detail['scenario_b_pts']:.0f} pts")
                c3.metric("Scenario C", "0 pts")


def render_timeline(live: dict, gender: str) -> None:
    """Timeline tab: chronological feed of completed events."""
    tl = _timeline(live, gender)
    if not tl:
        st.info("No events completed yet.")
        return

    for entry in reversed(tl):
        pts = entry.get("points_awarded", {})
        pts_str = ", ".join(f"{t}: {p:.0f}pt" for t, p in sorted(pts.items(), key=lambda x: -x[1]))
        st.markdown(
            f"""
            <div class="metric-card">
                <strong style="color:{GOLD}">{entry.get('event', '')}</strong><br/>
                🥇 <b>{entry.get('winner', '')}</b> ({entry.get('team', '')}) — {entry.get('mark', '')}<br/>
                <small>Points awarded: {pts_str}</small><br/>
                <small style="color:#8b949e">{entry.get('timestamp', '')}</small>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_leverage(live: dict, gender: str) -> None:
    """Leverage tab: which upcoming events matter most."""
    lev = _leverage(live, gender)
    if not lev:
        st.info("No upcoming events or leverage data available.")
        return

    import pandas as pd  # noqa: PLC0415

    rows = []
    for item in lev:
        rows.append(
            {
                "Event": item["event"],
                "Points at Stake": item["total_points"],
                "Top 5 Seeded Teams": ", ".join(item.get("top_teams", [])),
                "# Entries": item.get("n_entries", 0),
            }
        )
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Horizontal bar chart
    fig = px.bar(
        df,
        x="Points at Stake",
        y="Event",
        orientation="h",
        color_discrete_sequence=[GOLD],
        title="Points at Stake per Remaining Event",
    )
    fig.update_layout(
        paper_bgcolor=BG,
        plot_bgcolor=SURFACE,
        font_color=TEXT,
        title_font_color=GOLD,
        yaxis=dict(autorange="reversed"),
        height=max(300, 30 * len(rows)),
    )
    st.plotly_chart(fig, use_container_width=True)


# ─── Main layout ─────────────────────────────────────────────────────────────
def main() -> None:
    live = load_json(LIVE_JSON_URL)
    _pre_meet = load_json(PRE_MEET_JSON_URL)  # loaded for context; not used directly in renderer

    meta = live.get("meta", {})
    meet_name = live.get("meet_name", "NCAA Indoor Track & Field Championships")
    last_updated = meta.get("last_updated", "—")
    scrape_ok = meta.get("scrape_ok", False)
    status = meta.get("status", "unknown")

    # ── Header ──
    col_title, col_status = st.columns([3, 1])
    with col_title:
        st.title(f"🏟️ {meet_name}")
        st.caption(f"{live.get('venue', '')}  ·  {', '.join(live.get('dates', []))}")
    with col_status:
        st.markdown(
            f"""
            <div class="metric-card" style="text-align:right">
                <small>Status: <b style="color:{'#3fb950' if scrape_ok else '#f85149'}">{status.replace('_', ' ').title()}</b></small><br/>
                <small>Updated: {last_updated}</small>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ── Gender selector + Tabs ──
    gender = st.radio("Gender", ["Women", "Men"], horizontal=True)

    tab_lb, tab_ev, tab_team, tab_tl, tab_lev = st.tabs(
        ["🏆 Leaderboard", "📋 Events", "🔍 Team Detail", "📰 Timeline", "⚖️ Leverage"]
    )

    with tab_lb:
        render_leaderboard(live, gender)
    with tab_ev:
        render_events(live, gender)
    with tab_team:
        render_team_detail(live, gender)
    with tab_tl:
        render_timeline(live, gender)
    with tab_lev:
        render_leverage(live, gender)

    # ── Auto-refresh footer ──
    st.markdown("---")
    st.caption(
        f"Auto-refreshing every {REFRESH_SECONDS // 60} minutes. "
        "Data sourced from FlashResults via the NCAA Indoor scraper."
    )

    # Auto-refresh using Streamlit's rerun mechanism
    time.sleep(REFRESH_SECONDS)
    st.rerun()


if __name__ == "__main__":
    main()
