# Architecture

```
                 you (owner)
                     │  chat / approvals / manual Yahoo steps
                     ▼
 ┌──────────────── AI agent (your choice) ────────────────┐
 │  roles: gm · reviewer · scout     skills: agents/skills │
 └───────────────┬───────────────────────────┬────────────┘
                 │ MCP (stdio, 60 tools)     │ headless runs (optional automation)
                 ▼                           ▼
 ┌──────────────────────────── FBM engine (Python) ───────────────────────────┐
 │ mcp_server.py · cli.py (fbm) · dashboard/ (Streamlit)                       │
 │ decision layer  gm/ choosers · decisions · replay · odds_reliability ·     │
 │                     policy · policy_tuning · engine_ht · services ·        │
 │                     proposals · steps · memory · outcomes · health         │
 │ analytics/ ht_odds, category_weights, signals, simulation, lineup,         │
 │            streaming, waivers, trades, punts, valuation, …                 │
 │ data layer hashtag/ importer · health · store    data/ ESPN sync           │
 │            yahoo/ auth, reads, lineups+finals, ids    league/  store/      │
 └──────┬──────────────────┬────────────────────┬───────────────┬────────────┘
        ▼                  ▼                    ▼               ▼
  Hashtag Basketball   ESPN public JSON    Yahoo Fantasy     local state (gitignored)
  Premium (required    (schedule, box      (league data,     memory/ reports/ data/ config/
  for a real league;   scores, injuries,   daily lineups,    secrets → OS credential store
  projections, H2H     news)               final results;
  totals, waiver wire,                     read-only)
  trade ideas, notes)
```

The engine is deterministic and tested; the AI layer reasons over its outputs and never computes numbers itself.

## Data layer vs decision layer

- **Data layer.** With `[hashtag] enabled = true`, Hashtag Basketball Premium (HT) is the only projection source:
  `fbm.hashtag.importer.run_import` pulls the projection views (ROS, BLEND7/14/30, STREAM7/14/30), the H2H Planner
  weekly totals, league sync, waiver wire (run with FBM's category multipliers from
  `fbm.analytics.category_weights`), team ranks, player notes, Next Man Up and (on demand) Trade Finder into the
  `hashtag_*` tables; every page logs to `hashtag_import_log`. `fbm.hashtag.health` turns that log into the
  suggestion gate (no fallback: unhealthy → `HT_UNHEALTHY`, no move proposals). ESPN still supplies schedule, box
  scores, injuries and news; Yahoo supplies the league, daily lineups and final weekly results
  (`fbm.yahoo.lineups`). With HT disabled (demo league, tests) the ESPN-based engine produces projections.
- **Decision layer.** Every suggestion path builds options, picks the recommendation with the shared rule
  `fbm.gm.choosers.choose` under the policy settings (`fbm.gm.policy`), and logs it (`fbm.gm.decisions`, via
  `fbm.gm.engine_ht`). `fbm.gm.replay` grades every option on real box scores + Yahoo finals;
  `fbm.gm.odds_reliability` grades the official weekly odds (`fbm.analytics.ht_odds`); `fbm.gm.policy_tuning`
  re-runs the rule with candidate settings and suggests `policy` proposals, which only the owner can approve.

## Repository layout

| Path | Contents |
|---|---|
| `src/fbm/` | Engine package: `cli.py` and `cli_*.py` (CLI), `mcp_server.py`, `settings.py`/`config.py`, `constants.py` (constants registry), `credentials.py` (keyring), `timeutil.py` (ET game days), `automation.py`, `setup_wizard.py`, `backup.py` |
| `src/fbm/analytics/` | Valuation (G/Z-scores), projections and players (HT views in Hashtag mode; ESPN engine otherwise), official odds (`ht_odds`), HT category multipliers (`category_weights`), early signals, availability, simulation, lineup, streaming, waivers, trades, punts, playoffs, opponents |
| `src/fbm/hashtag/` | Hashtag Basketball client (owner's browser session), page parsers, HT→ESPN id matching, daily importer, read API (`store.py`), health gate (`health.py`); CLI `cli_hashtag.py` (`fbm hashtag`) |
| `src/fbm/data/` | ESPN and ESPN Fantasy clients, sync jobs, queries |
| `src/fbm/yahoo/` | Access (official-API OAuth or the owner's Yahoo web session, `session.py`), API client, league reads, daily lineups and final matchup results (`lineups.py`), id mapping, feed check (`coverage.py`) (read-only; FBM has no Yahoo write access) |
| `src/fbm/league/` | League model, demo league, Yahoo league views, transaction history |
| `src/fbm/gm/` | Services behind MCP/CLI (`services.py`, `hashtag_services.py`, `decision_services.py`, `policy_services.py`), HT-mode plumbing (`engine_ht.py`), decision rule (`choosers.py`), decision log (`decisions.py`), replay grading (`replay.py`), odds reliability, policy settings and tuning (`policy.py`, `policy_tuning.py`), proposals and approvals, owner steps (FBM never changes the league), memory, outcome grading, health |
| `src/fbm/store/` | SQLite access |
| `src/fbm/notify/` | Gmail notifications |
| `src/fbm/agents/` | Renders `agents/` sources into per-agent files (`fbm agent install`) |
| `src/fbm/dashboard/` | Streamlit app |
| `agents/roles/`, `agents/skills/` | Agent-neutral role prompts and skills — the source of truth for every agent |
| `templates/` | Seeds for personal state (`templates/memory/`, `templates/reports/`) copied by `fbm setup` |
| `config/` | `*.example.toml` (committed); your `settings.toml`, `league.toml`, `constants.toml`, `yahoo_id_overrides.toml`, `hashtag_id_overrides.toml` (gitignored) |
| `memory/` | AI memory: strategy, playbook, journal, manager notes (gitignored) |
| `reports/` | Generated reports and automation logs (gitignored) |
| `data/` | SQLite database, caches, snapshots, backups (gitignored) |
| `tests/` | Test suite (`uv run pytest -q`) |
| `.github/workflows/ci.yml` | CI |
