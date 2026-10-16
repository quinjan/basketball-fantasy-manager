# FBM — AI Front Office for Yahoo Fantasy Basketball (9-cat H2H)

A local, AI-run front office for one Yahoo Fantasy Basketball league (H2H Categories, 9-cat).
A deterministic Python engine (Yahoo data, valuations, simulations) is exposed to GitHub Copilot as tools;
Copilot agents (Opus GM + cross-model reviewer) make decisions on a schedule via Copilot app Automations,
auto-handle lineups, and ask for approval before add/drops, waiver claims, and trades.

> Fantasy data provided by Yahoo Fantasy.

## Setup

1. Install [uv](https://docs.astral.sh/uv/) and run `uv sync`.
2. Create a Yahoo Developer app (Fantasy Sports **Read/Write**, redirect URI `https://localhost:8080`).
3. `uv run fbm auth` — paste Client ID/Secret (stored in Windows Credential Manager), approve in browser,
   paste the redirected URL, pick your league.
4. `uv run fbm settings` — pull league settings into `memory/league-settings.md`.
5. `uv run fbm verify-write` — prove write access with a harmless no-op lineup re-save.

## Commands

| Command | Purpose |
|---|---|
| `fbm auth [--reset]` | Yahoo OAuth + league selection |
| `fbm status` | What's configured (never prints secrets) |
| `fbm league list` / `fbm league select` | Discover / choose the managed league |
| `fbm settings` | Fetch + summarize league settings |
| `fbm roster [--date YYYY-MM-DD]` | Roster for an ET game day |
| `fbm verify-write [--date]` | Confirm write scope (no-op) |

## Layout

- `src/fbm/` — engine (Yahoo client, data, analytics, MCP server, CLI)
- `memory/` — AI memory (strategy, playbook, journal, manager notes) — committed
- `reports/` — generated reports — committed
- `data/` — SQLite, caches, raw dumps — **gitignored**
- `.github/agents`, `.github/skills` — Copilot custom agents and skills

## Security

Secrets live only in Windows Credential Manager (`keyring`, service `fbm`). Never commit `.env`, tokens,
or `data/`.
