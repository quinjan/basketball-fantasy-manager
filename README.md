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
| `fbm auth [--reset] [--code URL]` | Yahoo OAuth + league selection |
| `fbm status` | What's configured (never prints secrets) |
| `fbm league list` / `fbm league select` | Discover / choose the managed league |
| `fbm settings` | Fetch + summarize league settings |
| `fbm roster [--date YYYY-MM-DD]` | Roster for an ET game day |
| `fbm verify-write [--date]` | Confirm write scope (no-op) |
| `fbm data sync` | Daily ESPN sync: schedule, box scores, season averages, injuries, news |
| `fbm data backfill [--season 2026]` | One-time load of a past season (priors + week-to-week variance) |
| `fbm data status` / `games` / `injuries` | Inspect the local NBA store |
| `fbm rank [--season] [--punt FT%,TO] [--metric g\|z]` | 9-cat G-score / Z-score rankings |
| `fbm demo build` | Simulated 12-team draft (demo league until Yahoo access is approved) |
| `fbm lineup --demo [--date]` | Optimal daily lineup (🔒 started games stay put) + capacity-aware IL moves |
| `fbm playoffs [--top N]` | Fantasy-playoff schedule: games per team, playoff value above replacement |
| `fbm waiver PLAYER [--drop X] [--free-agent]` | Rolling-waiver decision: claim / wait / add / pass |
| `fbm constants` | Every tunable constant: value, default, unit, source (override in `config/constants.toml`) |
| `fbm sim --demo [--opponent KEY]` | Monte Carlo rest-of-week category odds |
| `fbm profile --demo` | League-wide category strength (strategy/punt basis) |
| `fbm stream [--opponent] [--adds] [--protect]` | Streaming plan: best add/drop sequence for the week |
| `fbm trade eval --partner X --give A,B --get C` | Evaluate a trade for both sides (season-level) |
| `fbm trade find [--partner X]` | Find trades that help us and are defensible for them |
| `fbm learn grade` / `calibration` / `retro` | Learning loop: grade executed moves, simulator calibration |
| `fbm draft board/start/pick/undo/recommend/mock` | Draft assistant (ESPN ADP, tiers, live picks, mock drafts) |
| `fbm dashboard [--port 8501]` | Local Streamlit dashboard (13 pages) |
| `fbm proposals list/show/approve/reject` | Owner-side proposal decisions |
| `fbm sync-memory -m "..."` | Commit + push memory/reports/config |

## Using the GM (Copilot)

- **GM Office** — a Copilot app session in this folder running the `gm` agent (Opus). Ask anything: "How's my
  matchup?", "Should I drop X for Y?", "What's pending?", "Approve P-12". Prompt catalog: see the plan's Appendix A.
- **Agents** (`.github/agents/`): `gm` (decides), `reviewer` (different model family; independently re-checks every
  proposal and records AGREE/DISAGREE/SPLIT), `scout` (news/injury digging).
- **Skills** (`.github/skills/`): `daily-gm`, `lineup-guard`, `weekly-retro-preview`, `trade-scan`,
  `season-strategy`, `approvals`, `consult`, `health`, `draft-assist`.
- **MCP server** (`.github/mcp.json` → `fbm-mcp`): 39 tools over the engine. The folder must be trusted by Copilot.
- **Approvals:** in chat with the GM, or `uv run fbm proposals list|show|approve|reject P-12`.
- **Automations** (Copilot app → Automations, all disabled until Yahoo approval): Daily GM 15:00, Lineup Guard hourly
  23:00–12:00, Weekly Retro Mon 15:30, Trade Scan Wed/Sat 16:00, Health Sun 10:00 (UTC+8).
- **Memory:** `memory/` (strategy, playbook, journal, managers) is committed via `uv run fbm sync-memory`.

## Data sources

- **Yahoo Fantasy API** (league data) — gated since mid-2026; access application pending. Read-only by default.
- **ESPN public JSON** (NBA schedule, box scores, season averages, injuries, news) and **ESPN Fantasy** (ADP,
  rankings, positional eligibility). NBA.com CDN and stats.nba.com are blocked on the owner's network.

## Analytics

- **G-scores** (Rosenof, arXiv:2307.02188): `(μ_player − μ_pool) / √(σ² + κτ²)`, τ = RMS week-to-week SD,
  κ = 2N/(2N−1). FG%/FT% use volume-weighted impact. Z-scores shown alongside.
- **Projections:** current-season per-game shrunk toward last season by games played; counting stats scaled by
  the recent-minutes trend (last 5 games vs season, damped and capped) so role changes show up early.
- **Lineups:** max games started, then value; prefers leaving players in place (fewer manual changes). Players
  whose game has tipped off are locked; IL moves respect roster capacity (blocked activations become proposals).
- **Simulation:** resamples real game lines (keeps stat correlations) × injury play-probability. Matchup odds
  include an "if the opponent streams" upper bound (the streaming planner run from their side).
- **Streaming planner:** greedy add/drop search over effective ET days within the weekly add limit; each candidate
  re-plans daily lineups and re-simulates with common random numbers; rest-of-season cost penalized.
- **Trades:** season-level (typical week vs every team) expected categories for both sides, rosters rebalanced
  (forced drops / free-agent fills); finder screens by need-weighted G-scores, confirms by simulation, and filters
  offers that cost the partner > 1.0 ranking value. Output adds playoff-schedule value, weighted up as playoffs near.
- **Playoffs:** fantasy weeks from the NBA schedule (default: last 3 weeks); games per team per playoff week and
  playoff value above replacement.
- **Rolling waivers:** claim if the ROS gain clears a bar that rises with the priority spent (priority rotates to
  last after a claim). Below the bar, wait for free agency only if no other team (ahead or behind) is likely to
  claim him; otherwise pass.
- **Constants registry:** every tunable (`fbm constants`) records value, unit, and source (measured / literature /
  heuristic); `config/constants.toml` overrides are validated.
- **Learning loop:** executed moves graded by realized production (team-week SD units); matchup predictions
  graded by Brier score with reliability buckets.
- **Draft:** punt-aware G-score board with tiers vs ESPN ADP; build-aware live recommendations; mock drafts.

## Layout

- `src/fbm/` — engine (Yahoo client, data, analytics, MCP server, CLI)
- `memory/` — AI memory (strategy, playbook, journal, manager notes) — committed
- `reports/` — generated reports — committed
- `data/` — SQLite, caches, raw dumps — **gitignored**
- `.github/agents`, `.github/skills` — Copilot custom agents and skills

## Security

Secrets live only in Windows Credential Manager (`keyring`, service `fbm`). Never commit `.env`, tokens,
or `data/`.
