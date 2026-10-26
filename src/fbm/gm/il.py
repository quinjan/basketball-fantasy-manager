"""IL activation evaluation: what does "move X from IL to active (+ drop Y), effective day D" do to this week's
matchup and to rest-of-season value? Same output shape as `evaluate_add_drop`.

Before and after share one common-random-number draw bank and the same opponent totals, so the difference reflects
the roster change rather than Monte Carlo noise. The activated player only counts from the effective ET day, and
from his return day when he is injured (ESPN return date), at his injury play probability."""

from __future__ import annotations

from datetime import date
from typing import Any

from fbm.analytics.bank import DrawBank
from fbm.analytics.il_activation import activation_phases, phase_games, validate_activation
from fbm.analytics.simulation import compare, play_probability
from fbm.analytics.week import date_range, plan_team_week, teams_playing
from fbm.gm import services as svc
from fbm.timeutil import nba_today, parse_date


def _return_date(info: dict[str, Any] | None) -> date | None:
    raw = (info or {}).get("return_date")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def evaluate_il_activation(
    activate: str,
    drop: str | None = None,
    opponent: str | None = None,
    effective: str | None = None,
    play_prob: float | None = None,
    n: int = 6000,
) -> dict[str, Any]:
    league, c, conn = svc.active_league(), svc.ctx(), svc._conn()
    me = league.my_team
    act_id = svc._resolve_on_team(activate, me)
    drop_id = svc._resolve_on_team(drop, me) if drop else None
    problems = validate_activation(me.players, act_id, drop_id, league.roster_size())
    if problems:
        raise svc.GMError("; ".join(problems))
    if play_prob is not None and not 0.0 <= play_prob <= 1.0:
        raise svc.GMError("play_prob must be between 0 and 1.")

    names = {p.player_id: p.name for p in me.players}
    day0, ws, we = svc._week_window()
    notes: list[str] = []
    eff = parse_date(effective) if effective else nba_today()
    if eff < day0:
        notes.append(f"effective {eff} is before the window start; using {day0}")
        eff = day0
    days = date_range(day0, we)
    if eff > we:
        notes.append(f"effective {eff} is after this fantasy week ({we}): no matchup effect this week")

    status = c.statuses(conn).get(act_id, "")
    info = svc._injury(act_id)
    ret = _return_date(info)
    base_prob = play_probability(status)
    if play_prob is not None:
        prob, available_from, basis = play_prob, eff, "override (play_prob)"
    elif base_prob > 0:
        prob, available_from, basis = base_prob, eff, f"injury status '{status or 'healthy'}'"
    elif ret is not None:
        prob, available_from = play_probability("GTD"), max(eff, ret)
        basis = f"status '{status}': counted from ESPN return date {ret} at the GTD play probability"
    else:
        prob, available_from, basis = 0.0, None, f"status '{status}' with no return date: assumed to miss every game"
        notes.append("no return date and status is Out, so he adds nothing this week; pass play_prob to override")

    pun = svc._strategy_punts(None)
    value = svc._value(c, pun)
    opp = svc._team(league, opponent)
    playing = teams_playing(conn, ws, we)
    models = c.models([*me.player_ids(), *opp.player_ids()], conn)
    models[act_id].play_prob = float(prob)
    models[act_id].return_date = None  # `prob` and the roster phases already encode his return
    bank = DrawBank(models, n_sims=n, seed=42)
    banked, skip = svc._live_week(league, ws)

    phases = activation_phases(me.players, act_id, drop_id, eff, available_from)
    slots = league.starting_slots()
    vmap = value.to_dict()
    back = c.return_dates(conn)
    back.pop(act_id, None)
    before_games = phase_games(phases[:1], days, playing, slots, vmap, skip, back)
    after_games = phase_games(phases, days, playing, slots, vmap, skip, back)
    _, opp_plans = plan_team_week(opp, days, playing, slots, vmap, back)
    opp_totals = bank.total(svc._remaining_games(opp, opp_plans, skip), banked.get(opp.team_key, {}))
    mine = banked.get(me.team_key, {})
    before = compare(bank.total(before_games, mine), opp_totals, league.categories)
    after = compare(bank.total(after_games, mine), opp_totals, league.categories)

    act_team = str(c.projections.loc[act_id, "team"])
    act_games = (
        [d for d in days if available_from is not None and d >= available_from and act_team in playing.get(d, set())]
        if available_from is not None
        else []
    )
    drop_lost = (
        [d for d in days if d >= eff and str(c.projections.loc[drop_id, "team"]) in playing.get(d, set())]
        if drop_id
        else []
    )
    ros_value = svc._ros_value(c, pun)
    return {
        "move": "il_activation",
        "activate": names[act_id],
        "activate_id": act_id,
        "drop": names[drop_id] if drop_id else None,
        "drop_id": drop_id,
        "effective_et": eff.isoformat(),
        "activate_injury": {
            "status": status or None,
            "return_date": ret.isoformat() if ret else None,
            "play_probability_used": svc._r(prob, 3),
            "basis": basis,
            "counts_from_et": available_from.isoformat() if available_from else None,
        },
        "ros_value_delta": svc._r(value.get(act_id, 0.0) - (value.get(drop_id, 0.0) if drop_id else 0.0)),
        "ros_value_delta_availability_adjusted": svc._r(
            ros_value.get(act_id, 0.0) - (ros_value.get(drop_id, 0.0) if drop_id else 0.0)
        ),
        "ros_g_delta_by_cat": {
            cat.label: svc._r(
                c.values.loc[act_id, f"g_{cat.key}"] - (c.values.loc[drop_id, f"g_{cat.key}"] if drop_id else 0.0)
            )
            for cat in c.cfg.categories
        },
        "punts_applied": list(pun),
        "games_left_this_week": {"activate": len(act_games), "drop_lost": len(drop_lost)},
        "games_started_this_week": {"before": len(before_games), "after": len(after_games)},
        "this_week_vs": opp.name,
        "expected_cats_before": svc._r(before.expected_score),
        "expected_cats_after": svc._r(after.expected_score),
        "p_win_majority_before": svc._r(before.p_majority),
        "p_win_majority_after": svc._r(after.p_majority),
        "cat_p_win_change": {
            b.category.label: svc._r(a.p_win - b.p_win)
            for b, a in zip(before.categories, after.categories, strict=True)
        },
        "notes": notes,
        "method": f"{n} common-random-number weeks; daily optimal lineups; activated player counts from "
        "the effective ET day (and his return date), at his injury play probability",
    }
