# Architecture

```
                 you (owner)
                     │  chat / approvals / manual Yahoo steps
                     ▼
 ┌──────────────── AI agent (your choice) ────────────────┐
 │  roles: gm · reviewer · scout     skills: agents/skills │
 └───────────────┬───────────────────────────┬────────────┘
                 │ MCP (stdio, 50 tools)     │ headless runs (optional automation)
                 ▼                           ▼
 ┌──────────────────────────── FBM engine (Python) ───────────────────────────┐
 │ mcp_server.py · cli.py (fbm) · dashboard/ (Streamlit, 14 pages)            │
 │ gm/        services, proposals, steps, memory, outcomes, health         │
 │ analytics/ valuation, simulation, lineup, streaming, waivers, trades, …    │
 │ league/    demo league · Yahoo league model · transaction history          │
 │ data/      ESPN sync      yahoo/  auth, reads, draft, ids                  │
 │ store/     SQLite         notify/ Gmail (optional)                         │
 └──────┬─────────────────────────────┬───────────────────────────┬───────────┘
        ▼                             ▼                           ▼
   ESPN public JSON             Yahoo Fantasy API           local state (gitignored)
   (schedule, box scores,       (league data, read-only)    memory/ reports/ data/ config/
    injuries, news, ADP)                                    secrets → OS credential store
```

The engine is deterministic and tested; the AI layer reasons over its outputs and never computes numbers itself.

## Repository layout

| Path | Contents |
|---|---|
| `src/fbm/` | Engine package: `cli.py` and `cli_*.py` (CLI), `mcp_server.py`, `settings.py`/`config.py`, `constants.py` (constants registry), `credentials.py` (keyring), `timeutil.py` (ET game days), `automation.py`, `setup_wizard.py`, `backup.py` |
| `src/fbm/analytics/` | Valuation (G/Z-scores), projections and players, availability, simulation, lineup, streaming, waivers, trades, punts, playoffs, opponents, draft |
| `src/fbm/data/` | ESPN and ESPN Fantasy clients, sync jobs, queries |
| `src/fbm/yahoo/` | OAuth, API client, league reads, draft, id mapping (read-only; FBM has no Yahoo write access) |
| `src/fbm/league/` | League model, demo league, Yahoo league views, transaction history |
| `src/fbm/gm/` | Services behind MCP/CLI, proposals and approvals, owner steps (FBM never changes the league), memory, outcome grading, health |
| `src/fbm/store/` | SQLite access |
| `src/fbm/notify/` | Gmail notifications |
| `src/fbm/agents/` | Renders `agents/` sources into per-agent files (`fbm agent install`) |
| `src/fbm/dashboard/` | Streamlit app |
| `agents/roles/`, `agents/skills/` | Agent-neutral role prompts and skills — the source of truth for every agent |
| `templates/` | Seeds for personal state (`templates/memory/`, `templates/reports/`) copied by `fbm setup` |
| `config/` | `*.example.toml` (committed); your `settings.toml`, `league.toml`, `constants.toml`, `yahoo_id_overrides.toml` (gitignored) |
| `memory/` | AI memory: strategy, playbook, journal, manager notes (gitignored) |
| `reports/` | Generated reports and automation logs (gitignored) |
| `data/` | SQLite database, caches, snapshots, backups (gitignored) |
| `tests/` | Test suite (`uv run pytest -q`) |
| `.github/workflows/ci.yml` | CI |
