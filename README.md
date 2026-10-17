# FBM — AI Front Office for Yahoo Fantasy Basketball

[![CI](https://github.com/quinjan/basketball-fantasy-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/quinjan/basketball-fantasy-manager/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)

> **Get your own copy:** click **Use this template** (a clean copy under your account), **Fork** (if you want to
> send improvements back), or simply `git clone` it. Your personal league data never lives in the repo, so any of
> the three works.

FBM turns the AI agent you already use (GitHub Copilot, Claude Code, Codex, Cursor, Gemini CLI, or any MCP-capable
agent) into a general manager for **one** Yahoo Fantasy Basketball league. A deterministic Python engine does all
the math — player values, projections, matchup simulations, lineups, streaming, waivers, trades — and exposes it to
your agent as tools. The AI reasons over those numbers, drafts proposals, has them independently reviewed, and asks
for your approval. FBM never changes your league: it gives you insights, recommendations and proposals, and you
make every move in Yahoo yourself. Everything runs on your machine.

> Fantasy data provided by Yahoo Fantasy. Not affiliated with Yahoo, ESPN, or the NBA.

## Who it's for

- Yahoo Fantasy Basketball managers in a **head-to-head categories** league who want daily, data-driven help.
- People comfortable running a few terminal commands and using an AI coding agent.
- Anyone who wants to read, audit, or extend a fantasy-basketball analytics engine.

## What it does

- **Values players** with G-scores (a variance-aware improvement on Z-scores) and punt-aware rankings.
- **Projects players** from this season, ESPN's preseason projection (else last season), recent form, minutes
  trends, and injury status, blended with ESPN's current projection at a weight measured from graded forecasts.
- **Catches early signals**: buy-low / sell-high, new starters and minutes spikes, next-man-up opportunities when a
  key player is out, and ESPN projection revisions — refreshed by every data sync and graded afterwards.
- **Simulates your matchup** (Monte Carlo, including stats already banked this week) for per-category win odds.
- **Recommends the daily lineup** and IL moves with exact steps, respecting tip-off locks.
- **Plans streaming** adds/drops within your weekly add limit.
- **Decides rolling waivers**: claim now, wait for free agency, or pass.
- **Evaluates and finds trades** for both sides; compares punt builds; looks ahead to the fantasy playoffs.
- **Scouts opponents** from league transactions.
- **Learns**: grades past decisions and signals, checks how well-calibrated its simulations are, and measures
  whether FBM's engine or ESPN projects better.
- **Drafts**: board, live recommendations (with live Yahoo draft sync), and mock drafts.
- **Runs on a schedule** (optional) and keeps a local memory of strategy, playbook, and journal.

Interfaces: an MCP server (54 tools), the `fbm` CLI, and a local Streamlit dashboard (14 pages).

## What it doesn't do (limitations)

- **League formats:** Yahoo NBA only; H2H Categories or H2H Most Categories; standard 9-cat (FG%, FT%, 3PM, PTS,
  REB, AST, STL, BLK, TO) or a subset of them; redraft; rolling waivers.
- **Not supported:** points or rotisserie leagues; extra or different categories (e.g. DD, TD, OREB, A/T, FTM);
  FAAB bidding; keeper/dynasty valuation; more than one league per install; ESPN, Sleeper, Fantrax, or other
  platforms.
- **FBM never changes your league.** It has no Yahoo write access by design. Lineups and approved moves come back as
  exact steps you perform in Yahoo yourself.
- **Yahoo API access is gated.** Since mid-2026 every user must apply for access, which reportedly takes weeks.
  Meanwhile you can connect with your own Yahoo sign-in (`fbm yahoo login`, read-only; an undocumented route Yahoo
  may change), or run on a realistic demo league (see [docs/yahoo.md](docs/yahoo.md)).
- **Unofficial data sources.** NBA data comes from public, undocumented ESPN endpoints that may change or break.
- **Platforms:** developed and tested on Windows. macOS and Linux run in CI but are less battle-tested.
- **No guarantees.** Projections, simulations, and AI judgement can be wrong. You make the final call.

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- git
- An MCP-capable AI agent (see [Supported agents](#supported-agents))
- For your real league: a Yahoo Fantasy Basketball league, plus either approved Yahoo Fantasy Sports API access or
  your Yahoo sign-in via `fbm yahoo login` (the demo league needs neither)

## Quick start

```sh
git clone https://github.com/quinjan/basketball-fantasy-manager.git
cd basketball-fantasy-manager
uv sync
```

Then either:

- **Let your agent do it:** open your AI agent in this folder and ask it to **"set up FBM"**. It follows the
  `setup-fbm` skill, asks one question at a time (agent, models, time zone, automation), and runs the setup for you.
- **Do it yourself:** `uv run fbm setup` (interactive; re-run any time, or `uv run fbm setup --check` to verify).

Setup writes your local settings, seeds your memory folder, loads NBA data, generates your agent's files, and runs
health checks. **The demo league works immediately.** Connect your Yahoo league now with `uv run fbm yahoo login`
(your Yahoo sign-in, read-only) or later once API access is approved — see [docs/yahoo.md](docs/yahoo.md).

**Open the GM office:** start a session with your agent in this folder using the `gm` role and talk to it:
"How's my matchup?", "Should I drop X for Y?", "What's pending?", "Approve P-12". Or browse the dashboard with
`uv run fbm dashboard`.

## Supported agents

`uv run fbm agent install --agent <name>` generates everything from the agent-neutral sources in `agents/`.

| Agent | What `fbm agent install` writes | Automation options | Cross-family reviewer |
|---|---|---|---|
| GitHub Copilot (app / CLI / VS Code) — `copilot` | `.github/mcp.json`, `.vscode/mcp.json`, `.github/agents/*.agent.md`, `.github/skills/` | agent-app (Copilot app Automations), scheduler (`copilot -p`), engine-only | Yes |
| Claude Code — `claude-code` | `.mcp.json`, `CLAUDE.md`, `.claude/agents/`, `.claude/skills/` | scheduler (`claude -p`), engine-only | No (fresh-context Claude reviewer) |
| OpenAI Codex CLI — `codex` | `.codex/config.toml`, `.agents/skills/` (roles as `fbm-*` skills) | scheduler (`codex exec`), engine-only | No (fresh-context OpenAI reviewer) |
| Cursor — `cursor` | `.cursor/mcp.json`, `.cursor/rules/fbm.mdc`, `.cursor/agents/`, `.agents/skills/` | scheduler (`cursor-agent -p`), engine-only | Yes |
| Gemini CLI — `gemini` | `.gemini/settings.json`, `GEMINI.md`, `.agents/skills/` (roles as `fbm-*` skills) | scheduler (`gemini`), engine-only | No (fresh-context Gemini reviewer) |
| Any other MCP agent — `generic` | Nothing; prints the MCP server config and instructions to set up by hand | engine-only, or your agent's own scheduler | Depends on your agent |

Any agent can also use `agent-app` mode if its app supports scheduled prompts, or `none`. Details, safety flags,
and switching agents: [docs/agents.md](docs/agents.md).

## How decisions work

FBM produces three kinds of output — nothing is automatic:

- **Insights:** read-only analysis (rankings, matchup odds, simulations, opponent intel, health).
- **Recommendations:** the daily lineup / IL plan with exact steps. You set it in Yahoo; no approval needed. Tell
  the GM it's done (or `uv run fbm lineup --done`).
- **Proposals:** add/drop, waiver claims, trades and strategy changes:

```
engine numbers → GM proposal → reviewer verdict → your approval → re-validation → exact steps → you do it in Yahoo → you confirm
```

- **Engine numbers:** every stat, projection, and probability comes from an engine tool. The AI never does math in
  its head.
- **GM** (your best model) drafts a proposal. A **reviewer** checks it independently — ideally a different model
  family; on single-vendor agents it is a fresh-context reviewer of the same family, which is a weaker check.
- **You approve** in chat (your exact words are recorded) or with `uv run fbm proposals approve P-12`. Unapproved
  proposals expire.
- **Re-validation:** after approval the move is checked again against the current league state, then FBM gives
  you the exact steps (`fbm proposals approve` prints them; `uv run fbm proposals steps P-12` shows them again).
  Strategy proposals only change FBM's memory and are recorded directly.
- **You confirm** when it's done (`uv run fbm proposals done P-12 --note "..."`); the next Yahoo snapshot verifies it.
  In the demo league, your confirmation is what updates the demo roster.
- **Scheduled runs** only produce insights, recommendations and proposals; they never act or approve.

## Privacy & security

- Your personal state stays on your machine and is gitignored: `memory/` (strategy, playbook, journal, manager
  notes), `reports/`, `data/` (database, caches, snapshots, backups), and `config/settings.toml` plus other
  personal config. `uv run fbm backup` zips it to a local folder.
- Secrets (Yahoo keys and tokens, optional Gmail app password) live in your OS credential store via `keyring`
  (Windows Credential Manager, macOS Keychain, Linux Secret Service) — never in files.
- FBM sends nothing anywhere except: requests to Yahoo and ESPN for data, optional Gmail notifications, and what
  your chosen AI agent sends to its own model provider.

See [SECURITY.md](SECURITY.md) to report a vulnerability.

## Costs

FBM itself is free. AI usage is billed through your agent's plan; scheduled AI jobs use more of it (`engine-only`
automation uses none). Scheduled jobs only run while your computer is on.

## Updating

```sh
git pull
uv sync
uv run fbm agent install   # refresh generated agent files
uv run fbm setup --check
```

Your personal state is gitignored, so pulls don't conflict with it. Want a change in the core engine or prompts?
Open a pull request (see [CONTRIBUTING.md](CONTRIBUTING.md)).

## Documentation

- [docs/commands.md](docs/commands.md) — every CLI command
- [docs/mcp-tools.md](docs/mcp-tools.md) — every MCP tool
- [docs/analytics.md](docs/analytics.md) — how the numbers are made
- [docs/agents.md](docs/agents.md) — per-agent setup, roles, models, automation
- [docs/yahoo.md](docs/yahoo.md) — getting Yahoo access and connecting your league
- [docs/architecture.md](docs/architecture.md) — how the pieces fit together
- [AGENTS.md](AGENTS.md) — operating rules every AI agent follows in this repo

## Contributing

Bug reports, league-format requests, and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) first;
run `uv run pytest -q` and `uv run ruff check src tests` before submitting.

## License

[MIT](LICENSE)

## Disclaimer

FBM is a personal, non-commercial project, not affiliated with or endorsed by Yahoo, ESPN, or the NBA. Use it in
line with Yahoo's API terms and each data source's terms. It comes with no warranty: projections and AI
recommendations can be wrong, and you are responsible for your roster decisions.
