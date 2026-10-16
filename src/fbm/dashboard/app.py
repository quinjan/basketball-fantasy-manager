"""FBM dashboard (Streamlit). Run: `uv run fbm dashboard`.

Every number shown comes from the same engine services the GM agent uses.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from fbm.config import MEMORY_DIR
from fbm.gm import memory
from fbm.gm import services as svc

st.set_page_config(page_title="FBM — Front Office", page_icon="🏀", layout="wide")

CAT_LABELS = ["FG%", "FT%", "3PM", "PTS", "REB", "AST", "STL", "BLK", "TO"]
CAT_KEYS = ["fg_pct", "ft_pct", "tpm", "pts", "reb", "ast", "stl", "blk", "tov"]
KEY_TO_LABEL = dict(zip(CAT_KEYS, CAT_LABELS, strict=True))
POSITIONS = ["All", "PG", "SG", "G", "SF", "PF", "F", "C"]


# ------------------------------------------------------------------ helpers


def call(fn, *args, **kwargs) -> Any:
    try:
        result = fn(*args, **kwargs)
    except Exception as err:  # show engine errors instead of crashing the page
        st.error(f"{type(err).__name__}: {err}")
        st.stop()
    if isinstance(result, dict) and "error" in result:
        st.error(result["error"])
        st.stop()
    return result


@st.cache_data(ttl=300, show_spinner="Crunching numbers…")
def cached(name: str, *args: Any) -> Any:
    return getattr(svc, name)(*args)


def color_value(v: Any) -> str:
    if not isinstance(v, (int, float)):
        return ""
    if v >= 1.0:
        return "background-color: rgba(46,160,67,0.35)"
    if v >= 0.4:
        return "background-color: rgba(46,160,67,0.15)"
    if v <= -1.0:
        return "background-color: rgba(218,54,51,0.35)"
    if v <= -0.4:
        return "background-color: rgba(218,54,51,0.15)"
    return ""


def color_prob(v: Any) -> str:
    if not isinstance(v, (int, float)):
        return ""
    if v >= 0.65:
        return "background-color: rgba(46,160,67,0.30)"
    if v <= 0.35:
        return "background-color: rgba(218,54,51,0.30)"
    return "background-color: rgba(212,167,44,0.25)"


def odds_chart(cats: dict[str, float], title: str):
    df = pd.DataFrame({"Category": list(cats), "P(win)": list(cats.values())})
    fig = px.bar(
        df,
        x="Category",
        y="P(win)",
        range_y=[0, 1],
        title=title,
        text_auto=".0%",
        color="P(win)",
        color_continuous_scale=["#da3633", "#d4a72c", "#2ea043"],
        range_color=[0, 1],
    )
    fig.add_hline(y=0.5, line_dash="dot", line_color="gray")
    fig.update_layout(height=320, coloraxis_showscale=False, margin=dict(t=40, b=10))
    return fig


def strategy_punts() -> list[str]:
    return [p for p in memory.current_strategy().get("punts", []) if p in CAT_LABELS]


# ------------------------------------------------------------------ pages


def page_overview() -> None:
    ov = call(svc.league_overview)
    if ov["source"] == "demo":
        st.warning(
            "Demo league (simulated draft over real players). Your real Yahoo league connects once Yahoo "
            "approves API access."
        )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Team", ov["my_team"])
    c2.metric("Fantasy week (ET)", f"{ov['fantasy_week']['start'][5:]} → {ov['fantasy_week']['end'][5:]}")
    strat = ov["strategy"]
    c3.metric("Strategy punts", ", ".join(strat.get("punts") or []) or "none")
    c4.metric("Open proposals", len(ov["open_proposals"]))

    sim = call(cached, "simulate_matchup_tool", None, None, 5000, False)
    st.subheader(f"This week vs {sim['opponent']}")
    m1, m2, m3 = st.columns(3)
    m1.metric("Expected categories won", f"{sim['expected_cats_won']:.2f} / 9")
    m2.metric("P(win the week)", f"{sim['p_win_majority']:.0%}")
    m3.metric("Swing categories", ", ".join(sim["swing_categories"]) or "none")
    st.plotly_chart(
        odds_chart({c["cat"]: c["p_win"] for c in sim["categories"]}, "Category win probability"),
        use_container_width=True,
    )

    left, right = st.columns(2)
    with left:
        st.subheader("Open proposals")
        for p in ov["open_proposals"]:
            st.write(f"- {p}")
        if not ov["open_proposals"]:
            st.caption("None.")
        if strat.get("notes"):
            st.subheader("Approved strategy")
            st.write(strat["notes"])
    with right:
        st.subheader("Data freshness (UTC)")
        st.dataframe(
            pd.DataFrame(sorted(ov["data_freshness"].items()), columns=["Job", "Last run"]),
            hide_index=True,
            use_container_width=True,
        )
        y = ov["yahoo"]
        st.caption(f"Yahoo: {y['status']} · write access: {'yes' if y['write_enabled'] else 'no'}")


def page_matchup() -> None:
    ov = call(svc.league_overview)
    teams = {t["name"]: t["key"] for t in ov["teams"] if t["key"] != ov["my_team_key"]}
    c1, c2 = st.columns([3, 1])
    opp_name = c1.selectbox("Opponent", list(teams))
    n = c2.select_slider("Simulations", [2000, 5000, 10000, 20000], value=10000)
    sim = call(cached, "simulate_matchup_tool", teams[opp_name], None, n, False)
    st.caption(
        f"Window (ET) {sim['window_et']} · games started {sim['games_started']['me']} vs "
        f"{sim['games_started']['opp']} · {sim['method']}"
    )
    m1, m2 = st.columns(2)
    m1.metric("Expected categories won", f"{sim['expected_cats_won']:.2f}")
    m2.metric("P(win the week)", f"{sim['p_win_majority']:.0%}")
    df = pd.DataFrame(sim["categories"]).rename(
        columns={"cat": "Cat", "me_mean": "Me", "opp_mean": "Opp", "p_win": "Win", "p_tie": "Tie", "p_loss": "Loss"}
    )
    styled = df.style.map(color_prob, subset=["Win"]).format({"Win": "{:.0%}", "Tie": "{:.0%}", "Loss": "{:.0%}"})
    st.dataframe(styled, hide_index=True, use_container_width=True)
    st.plotly_chart(
        odds_chart({c["cat"]: c["p_win"] for c in sim["categories"]}, "P(win) by category"), use_container_width=True
    )


def page_roster() -> None:
    r = call(svc.my_roster)
    st.caption(f"{r['team']} · ET {r['date_et']} · value uses punts: {', '.join(r['punts_applied']) or 'none'}")
    rows = [
        {
            "Slot": p["slot"],
            "Player": p["player"],
            "NBA": p["nba"],
            "Pos": "/".join(p["positions"]),
            "Status": p["status"],
            "Game today": "✓" if p["game_today"] else "",
            "Games left": p["games_left_this_week"],
            "Value": p["value"],
            **p["g_by_cat"],
        }
        for p in r["players"]
    ]
    df = pd.DataFrame(rows)
    value_cols = ["Value", *[c for c in CAT_LABELS if c in df.columns]]
    st.dataframe(
        df.style.map(color_value, subset=value_cols).format(precision=2),
        hide_index=True,
        use_container_width=True,
        height=520,
    )
    lu = call(svc.optimize_lineup)
    with st.expander(f"Optimal lineup today ({lu['starters_with_games']} starters with games)"):
        st.write(lu["changes"] or "Lineup already optimal.")
        if lu["il_moves"]:
            st.write("IL moves:", lu["il_moves"])


def page_players() -> None:
    c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
    punts = c1.multiselect("Punt categories", CAT_LABELS, default=strategy_punts(), key="pl_punts")
    pos = c2.selectbox("Position", POSITIONS)
    avail = c3.toggle("Free agents only", value=False)
    top = c4.number_input("Rows", 10, 300, 60, step=10)
    r = call(cached, "rankings", int(top), ",".join(punts), None if pos == "All" else pos, avail)
    df = pd.DataFrame(r["players"]).rename(columns={"games_left_this_week": "games_left"})
    if df.empty:
        st.info("No players match.")
        return
    st.dataframe(
        df.drop(columns=["id"]).style.map(color_value, subset=["value"]).format(precision=2),
        hide_index=True,
        use_container_width=True,
        height=520,
    )
    name = st.selectbox("Player card", [""] + list(df["player"]))
    if name:
        card = call(svc.player_card, name)
        a, b = st.columns(2)
        a.json(
            {
                k: card[k]
                for k in (
                    "player",
                    "nba",
                    "position",
                    "fantasy_owner",
                    "g_total",
                    "rank_g",
                    "injury",
                    "games_left_this_week",
                    "projection_basis",
                )
            }
        )
        b.plotly_chart(
            px.bar(
                x=list(card["g_by_cat"]),
                y=list(card["g_by_cat"].values()),
                labels={"x": "", "y": "G-score"},
                title="G-score by category",
            ),
            use_container_width=True,
        )
        st.write("Per-game projection:", card["per_game_projection"])
        if card["recent_news"]:
            st.write("Recent news (untrusted):")
            for item in card["recent_news"]:
                st.write(f"- {item['published'][:10]} — {item['headline']}")


def page_streaming() -> None:
    st.caption(
        "Best add/drop sequence for the rest of this week within the add limit (analysis only — moves still "
        "go through proposals)."
    )
    c1, c2 = st.columns([3, 1])
    protect = c1.text_input("Never drop (comma-separated names)", "")
    adds = c2.number_input("Adds available (blank = auto)", min_value=0, max_value=10, value=None)
    if st.button("Plan streams", type="primary"):
        st.session_state["stream"] = call(svc.streaming_plan, None, adds, 4, protect)
    r = st.session_state.get("stream")
    if not r:
        return
    st.caption(
        f"{r['window_et']} vs {r['opponent']} · adds {r['adds_available']} available · punts "
        f"{', '.join(r['punts_applied']) or 'none'} · droppable: {', '.join(r['droppable_considered'])}"
    )
    base, after = r["baseline"], r["after_plan"]
    m1, m2, m3 = st.columns(3)
    m1.metric(
        "Expected cats", f"{after['expected_cats']:.2f}", f"{after['expected_cats'] - base['expected_cats']:+.2f}"
    )
    m2.metric(
        "P(win week)",
        f"{after['p_win_majority']:.0%}",
        f"{(after['p_win_majority'] - base['p_win_majority']) * 100:+.0f} pts",
    )
    m3.metric("Games started", r["games_started"]["after"], r["games_started"]["after"] - r["games_started"]["before"])
    if r["moves"]:
        st.dataframe(
            pd.DataFrame(r["moves"]).drop(columns=["add_id", "drop_id"]), hide_index=True, use_container_width=True
        )
    else:
        st.info("No move clears the minimum gain — hold.")
    for note in r["notes"]:
        st.caption(f"• {note}")


def show_trade(t: dict[str, Any]) -> None:
    me, them = t["me"], t["them"]
    st.markdown(
        f"**Give** {', '.join(me['gives'])} → **{them['team']}** · **Get** {', '.join(me['gets'])} · "
        f"verdict: `{t['verdict']}`"
    )
    rows = [
        {
            "Side": s["team"],
            "Exp cats before": s["expected_cats_before"],
            "after": s["expected_cats_after"],
            "Δ": s["expected_cats_delta"],
            "Ranking value Δ": s["ranking_value_delta"],
            "Forced drops": ", ".join(s["forced_drops"]),
            "FA fills": ", ".join(s["free_agent_fills"]),
        }
        for s in (me, them)
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    deltas = {KEY_TO_LABEL.get(k, k): v for k, v in me["cat_win_rate_delta"].items()}
    fig = px.bar(
        x=list(deltas),
        y=list(deltas.values()),
        labels={"x": "", "y": "Δ win rate"},
        title="My category win-rate change",
    )
    fig.update_layout(height=260, margin=dict(t=40, b=10))
    st.plotly_chart(fig, use_container_width=True)
    for note in t["notes"]:
        st.caption(f"• {note}")


def page_trades() -> None:
    league = call(svc.active_league)
    others = {t.name: t for t in league.teams if t.team_key != league.my_team_key}
    tab_find, tab_eval = st.tabs(["Trade finder", "Evaluate a trade"])
    with tab_find:
        partner = st.selectbox("Partner", ["Whole league", *others], key="tf_partner")
        if st.button("Find trades", type="primary"):
            key = None if partner == "Whole league" else others[partner].team_key
            st.session_state["found"] = call(svc.find_trades_tool, key, 2, 2, 6)
        found = st.session_state.get("found")
        if found:
            if not found["trades"]:
                st.info("No trades found that help us and are defensible for them.")
            for t in found["trades"]:
                with st.container(border=True):
                    show_trade(t)
    with tab_eval:
        partner = st.selectbox("Partner", list(others), key="te_partner")
        team = others[partner]
        c1, c2 = st.columns(2)
        give = c1.multiselect("I give", [p.name for p in league.my_team.players])
        get = c2.multiselect("I get", [p.name for p in team.players])
        if st.button("Evaluate", type="primary", disabled=not (give and get)):
            show_trade(call(svc.evaluate_trade_tool, team.team_key, ",".join(give), ",".join(get)))


def page_league() -> None:
    r = call(cached, "league_profile_tool", 4000)
    teams = r["teams"]
    mat = pd.DataFrame(
        [{KEY_TO_LABEL[k]: v for k, v in t["cat_win_rate"].items()} for t in teams],
        index=[("★ " if t["mine"] else "") + t["team"] for t in teams],
    )
    fig = px.imshow(
        mat,
        text_auto=".0%",
        color_continuous_scale=["#da3633", "#f6f8fa", "#2ea043"],
        zmin=0,
        zmax=1,
        aspect="auto",
        title="Category win rate vs the league (typical week)",
    )
    fig.update_layout(height=520, coloraxis_showscale=False)
    st.plotly_chart(fig, use_container_width=True)
    summary = pd.DataFrame(
        [{"Team": t["team"], "Expected cats": t["expected_cats"], "P(win week)": t["p_win_week"]} for t in teams]
    )
    st.dataframe(
        summary.style.format({"P(win week)": "{:.0%}", "Expected cats": "{:.2f}"}),
        hide_index=True,
        use_container_width=True,
    )
    st.caption(r["method"])


def page_proposals() -> None:
    status = st.radio("Show", ["open", "all"], horizontal=True)
    r = call(svc.list_proposals, None if status == "all" else "open", 50)
    if not r["proposals"]:
        st.info("No proposals.")
    for p in r["proposals"]:
        with st.expander(f"{p['ref']} · {p['status']} · {p['title']}", expanded=p["status"] == "pending_approval"):
            st.write(p["summary"])
            st.markdown(f"**Rationale:** {p['rationale']}")
            if p.get("review_verdict"):
                st.markdown(f"**Review ({p['review_model']}):** `{p['review_verdict']}`")
                st.write(p.get("review_notes") or "")
            if p.get("owner_quote"):
                st.markdown(f"**Owner:** “{p['owner_quote']}”")
            st.caption(f"Created {p['created_at']} · expires {p['expires_at']} (UTC)")
            if p["status"] == "pending_approval":
                note = st.text_input("Your note (recorded verbatim)", key=f"note_{p['id']}")
                a, b = st.columns(2)
                if a.button("Approve & execute", key=f"ap_{p['id']}", type="primary"):
                    call(svc.record_owner_decision, p["ref"], True, note or "Approved via dashboard")
                    res = call(svc.execute_proposal, p["ref"])
                    st.success(f"{res['ref']} → {res['status']}")
                    st.json(res.get("execution") or {})
                if b.button("Reject", key=f"rj_{p['id']}"):
                    call(svc.record_owner_decision, p["ref"], False, note or "Rejected via dashboard")
                    st.warning(f"{p['ref']} rejected")
            if p["status"] == "awaiting_manual":
                for step in (p.get("execution") or {}).get("manual_steps", []):
                    st.write(step)
                if st.button("I did it in Yahoo", key=f"md_{p['id']}"):
                    call(svc.mark_manual_done, p["ref"], "Confirmed via dashboard")
                    st.success("Marked done")
            with st.popover("Details"):
                st.json(call(svc.get_proposal, p["ref"]))


def page_learning() -> None:
    st.subheader("Decision grades")
    g = call(svc.grade_decisions_tool, 60)
    if g["decisions"]:
        cols = ("ref", "title", "window_et", "games_counted", "delta", "grade")
        st.dataframe(
            pd.DataFrame([{k: d[k] for k in cols} for d in g["decisions"]]), hide_index=True, use_container_width=True
        )
    else:
        st.caption("No executed moves to grade yet.")
    st.caption(g["scale"])
    st.subheader("Simulator calibration")
    cal = call(svc.prediction_calibration)
    if not cal.get("category_forecasts"):
        st.caption(cal.get("note", "No graded predictions yet."))
        return
    a, b, c = st.columns(3)
    a.metric("Brier score", cal["brier"])
    b.metric("Coin-flip baseline", cal["brier_coinflip_baseline"])
    c.metric("Skill vs coin flip", f"{cal['skill_vs_coinflip']:.0%}")
    rel = pd.DataFrame(cal["reliability"])
    fig = px.scatter(
        rel,
        x="mean_forecast",
        y="observed",
        size="n",
        range_x=[0, 1],
        range_y=[0, 1],
        title="Reliability (points on the diagonal = well calibrated)",
    )
    fig.add_shape(type="line", x0=0, y0=0, x1=1, y1=1, line=dict(dash="dot", color="gray"))
    st.plotly_chart(fig, use_container_width=True)


def draft_live_tab() -> None:
    with st.form("start_draft"):
        c1, c2, c3, c4 = st.columns(4)
        teams = c1.number_input("Teams", 4, 20, 12)
        slot = c2.number_input("My slot", 1, 20, 1)
        rounds = c3.number_input("Rounds", 5, 20, 13)
        punts = c4.multiselect("Punts", CAT_LABELS)
        if st.form_submit_button("Start / restart draft"):
            call(svc.draft_start, int(teams), int(slot), int(rounds), ",".join(punts))
    try:
        rec = svc.draft_recommend()
    except FileNotFoundError:
        st.info("No draft in progress.")
        return
    if rec.get("done"):
        st.success("Draft complete: " + ", ".join(rec["my_team"]))
        return
    who = "**YOUR PICK**" if rec["is_my_pick"] else f"team {rec['on_clock']} on the clock"
    st.markdown(f"Pick **{rec['overall']}** (round {rec['round']}) — {who} · my next picks {rec['my_next_picks']}")
    st.dataframe(pd.DataFrame(rec["recommendations"]).drop(columns=["id"]), hide_index=True, use_container_width=True)
    if rec["position_needs"]:
        st.warning("Position needs: " + ", ".join(rec["position_needs"]))
    c1, c2, c3 = st.columns([3, 1, 1])
    name = c1.text_input("Player picked (by the team on the clock)")
    if c2.button("Record pick", type="primary", disabled=not name):
        call(svc.draft_pick, name)
        st.rerun()
    if c3.button("Undo last"):
        call(svc.draft_undo)
        st.rerun()
    if rec["my_team"]:
        st.write("My team:", ", ".join(f"{p['overall']}. {p['player']}" for p in rec["my_team"]))


def page_draft() -> None:
    tab_board, tab_live, tab_mock = st.tabs(["Board", "Live draft", "Mock drafts"])
    with tab_board:
        c1, c2, c3 = st.columns([3, 1, 1])
        punts = c1.multiselect("Punt categories", CAT_LABELS, key="db_punts")
        pos = c2.selectbox("Position", POSITIONS, key="db_pos")
        top = c3.number_input("Rows", 20, 400, 150, step=10, key="db_top")
        r = call(cached, "draft_board", int(top), ",".join(punts), None if pos == "All" else pos, True)
        df = pd.DataFrame(r["players"]).drop(columns=["id"])
        df["positions"] = df["positions"].map("/".join)
        st.dataframe(
            df.style.map(color_value, subset=["value"]).format(precision=2),
            hide_index=True,
            use_container_width=True,
            height=560,
        )
        st.caption(r["notes"])
    with tab_live:
        draft_live_tab()
    with tab_mock:
        c1, c2, c3 = st.columns(3)
        slot = c1.number_input("My slot", 1, 20, 1, key="mk_slot")
        punts = c2.multiselect("Punts", CAT_LABELS, key="mk_punts")
        runs = c3.number_input("Runs", 1, 10, 3, key="mk_runs")
        if st.button("Run mock drafts", type="primary"):
            r = call(svc.draft_mock, int(slot), 12, 13, ",".join(punts), int(runs))
            st.metric("Average expected categories won", f"{r['avg_expected_cats']:.2f}")
            for run in r["runs"]:
                st.markdown(
                    f"**Run {run['run']}** — {run['my_expected_cats']:.2f} expected cats, rank {run['rank_of_teams']}"
                )
                st.caption(" · ".join(run["my_picks"]))
            st.caption(
                "Optimistic by design (others draft by ADP; the model grades its own picks). Compare "
                "strategies relative to each other."
            )


def page_memory() -> None:
    tabs = st.tabs(["Strategy", "Playbook", "Journal", "Managers"])
    with tabs[0]:
        st.json(memory.current_strategy())
        path = MEMORY_DIR / "strategy.md"
        st.markdown(path.read_text(encoding="utf-8") if path.exists() else "")
    with tabs[1]:
        path = MEMORY_DIR / "playbook.md"
        st.markdown(path.read_text(encoding="utf-8") if path.exists() else "_empty_")
    with tabs[2]:
        st.markdown(memory.recent_journal(days=7, limit=40000) or "_no entries_")
    with tabs[3]:
        for name, text in memory.manager_notes(4000).items():
            with st.expander(name):
                st.markdown(text)


PAGES = {
    "Overview": page_overview,
    "Matchup": page_matchup,
    "Roster": page_roster,
    "Players": page_players,
    "Streaming": page_streaming,
    "Trades": page_trades,
    "League": page_league,
    "Proposals": page_proposals,
    "Learning": page_learning,
    "Draft": page_draft,
    "Memory": page_memory,
}


def main() -> None:
    with st.sidebar:
        st.title("🏀 FBM")
        st.caption("AI front office · 9-cat H2H")
        page = st.radio("Page", list(PAGES), label_visibility="collapsed")
        if st.button("Refresh NBA data"):
            with st.spinner("Syncing ESPN…"):
                res = call(svc.refresh_data)
            st.cache_data.clear()
            st.success("; ".join(res["jobs"])[:300])
        if st.button("Clear cached results"):
            st.cache_data.clear()
        st.divider()
        st.caption("NBA data: ESPN. Fantasy data provided by Yahoo Fantasy (when connected).")
    st.header(page)
    PAGES[page]()


main()
