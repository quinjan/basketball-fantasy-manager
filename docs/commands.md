# CLI commands

Run every command as `uv run fbm <command>` from the repo root. `uv run fbm <command> --help` shows all options.
Dates are **US/Eastern** NBA game days (`YYYY-MM-DD`).

## Setup and maintenance

| Command | Purpose |
|---|---|
| `fbm setup` | Set up or re-check FBM: settings, memory, NBA data, agent files, automation, health. Safe to re-run. Flags: `--agent`, `--gm-model`, `--reviewer-model`, `--routine-model`, `--timezone`, `--automation`, `--jobs`, `--league-source demo\|yahoo`, `--email/--no-email`, `--quiet-hours`, `--expiry-hours`, `--yahoo-approved`, `--skip-data`, `--yes`/`-y` (non-interactive), `--check` (verify only, writes nothing) |
| `fbm status` | What is configured (never prints secrets) |
| `fbm health` | Health checks (OK/WARN/FAIL): data freshness, Yahoo access/snapshot, Hashtag Basketball gate, stale proposals, missed runs, storage. Exits 1 on any FAIL |
| `fbm constants` | Every tunable engine constant (value, kind, source); override in `config/constants.toml` |
| `fbm backup [--dest DIR]` | Zip `memory/`, `reports/`, settings, league config and the NBA database to a local folder (default `data/backups/`; no secrets) |
| `fbm dashboard [--port 8501]` | Launch the local Streamlit dashboard |

## AI agent wiring (`fbm agent`)

| Command | Purpose |
|---|---|
| `fbm agent install [--agent NAME] [--dry-run]` | Generate your agent's MCP config, role agents, skills and instructions file. NAME: `copilot`, `claude-code`, `codex`, `cursor`, `gemini`, `generic` |
| `fbm agent status [--timeout 120]` | Show installed agent files and check the MCP server starts and lists its tools |
| `fbm agent uninstall [--agent NAME]` | Remove only the files FBM generated (tracked in `data/agent_install.json`) |
| `fbm agent sync-setup-skill` | Maintainers: copy `agents/skills/setup-fbm/SKILL.md` into the committed per-agent copies |
| `fbm tool [NAME] [key=value ...] [--stdin] [--list]` | Call any fbm MCP tool from the shell: same validation and JSON output as the MCP server. Fallback when an agent session can't reach the server. String arguments stay raw; others parse as JSON; `--stdin` reads a JSON object of arguments. Exits 1 on a tool error |

## Automation (`fbm automation`)

| Command | Purpose |
|---|---|
| `fbm automation jobs` | Job catalog: ET schedule, skill, model role, engine-only commands |
| `fbm automation run JOB [--mode scheduler\|engine-only]` | Run one job now (what every scheduler calls). Logs to `reports/automation/` |
| `fbm automation install [--mode MODE] [--jobs a,b] [--dry-run]` | Install scheduled jobs for the mode (`none`, `agent-app`, `scheduler`, `engine-only`) |
| `fbm automation list` | List installed FBM scheduler entries |
| `fbm automation remove [--jobs a,b]` | Remove FBM scheduler entries (never touches other entries) |

Jobs: `data-sync`, `lineup-guard`, `daily-gm`, `weekly-retro`, `trade-scan`, `health`. See [agents.md](agents.md#automation).

## Yahoo connection and league reads

| Command | Purpose |
|---|---|
| `fbm yahoo login [--paste] [--browser B] [--league-key KEY]` | Connect with your own Yahoo sign-in (no API approval needed; read-only web session kept in the OS credential store), pick your league, optionally switch FBM to it. See [yahoo.md](yahoo.md) |
| `fbm yahoo check [--date D]` | Read every Yahoo feed FBM uses (plus extras) and report which work, via the active route |
| `fbm yahoo status` | Which Yahoo access route is active (web session or official API); never prints secrets |
| `fbm yahoo logout` | Forget the web session (FBM falls back to the official API) |
| `fbm auth [--reset] [--league-key KEY]` | Connect to the official Yahoo API (one-time browser consent), then pick your league. Also accepts `--code URL` and `--scope` |
| `fbm league list` | Your Yahoo NBA leagues for the current season |
| `fbm league select [--league-key KEY]` | Choose which league FBM manages |
| `fbm settings` | Fetch and summarize your league settings from Yahoo |
| `fbm roster [--date D]` | Your roster and selected positions for an ET game day |
| `fbm snapshot` | Re-read the Yahoo league now (rosters, scoreboard, standings, waivers, ownership) and sync new league transactions |
| `fbm standings` | League standings |
| `fbm scoreboard` | This week's live category totals for every matchup (yours first) |
| `fbm transactions [--count N] [--team X]` | Recent league transactions |
| `fbm offers` | Your pending trades (incoming offers evaluated) and waiver claims; accept/reject manually in Yahoo |
| `fbm intel [--team X] [--sync]` | Opponent intel: manager activity, streaming, trades, builds, best trade partners |
| `fbm ids` | Yahoo players not mapped to ESPN ids, and how to fix them (`config/yahoo_id_overrides.toml`) |

## Hashtag Basketball Premium (`fbm hashtag`)

FBM's data layer for a real league (required: a Hashtag Basketball Premium subscription with the Yahoo league
imported into its premium league tools), active when `[hashtag] enabled = true` in `config/settings.toml`
(`league_id` = the Yahoo league id; environment variable `FBM_HASHTAG_ENABLED=0|1` overrides `enabled`). The demo
league runs on FBM's built-in ESPN engine instead. While HT is enabled but unhealthy, FBM makes no suggestions (no
fallback). HT content is personal-use only.

| Command | Purpose |
|---|---|
| `fbm hashtag login` | Sign in to Hashtag Basketball Premium once in your own browser (FBM never sees the password; the session goes to the OS credential store) |
| `fbm hashtag logout` | Forget the Hashtag sign-in (HT then reports unhealthy: no suggestions while enabled) |
| `fbm hashtag status` | HT health (the suggestion gate), the latest import of each page, unmatched players; never prints cookies |
| `fbm hashtag import [--pages a,b]` | Import HT now (projections, H2H planner, league sync, waiver wire, ranks, notes, ...); `--pages` limits it, e.g. `projections,h2h_planner,league_sync` |
| `fbm hashtag check` | Signed in? League visible on Hashtag? Does every page FBM reads still parse? Unmatched ids (fix in `config/hashtag_id_overrides.toml`). Read-only |

## NBA data (`fbm data`)

| Command | Purpose |
|---|---|
| `fbm data sync` | Daily sync: schedule status, new box scores, season averages, injuries, news |
| `fbm data backfill [--season YYYY] [--workers N]` | One-time load of a completed season (priors and week-to-week variance) |
| `fbm data status` | What's in the local NBA store |
| `fbm data games [--date D]` | NBA games on an ET date |
| `fbm data injuries [--team T]` | Current ESPN injury report |

## Analysis

| Command | Purpose |
|---|---|
| `fbm rank [--season] [--top N] [--punt FT%,TO] [--metric g\|z] [--team] [--min-gp] [--csv]` | Rank players by 9-cat value (G-score or Z-score) |
| `fbm lineup [--date D] [--done]` | Recommended lineup for an ET game day with exact steps (tip-off locks, IL moves first); `--done` = you set it in Yahoo (demo league: updates the demo roster) |
| `fbm sim [--start] [--opponent KEY] [--n N] [--demo]` | Monte Carlo the rest of this week: per-category win odds |
| `fbm profile [--demo] [--n N]` | League-wide category strength per team |
| `fbm stream [--opponent] [--adds] [--protect]` | Streaming plan: best add/drop sequence within the add limit |
| `fbm waiver PLAYER [--drop X] [--free-agent]` | Rolling waivers: claim now, wait, add, or pass |
| `fbm punts [--scenarios "FT%;3PM,FT%"] [--moves N] [--top N]` | Punt-scenario evaluator |
| `fbm playoffs [--scope] [--top N]` | Fantasy-playoff schedule outlook |
| `fbm trade eval --partner X --give A,B --get C` | Evaluate a trade for both sides |
| `fbm trade find [--partner X] [--limit N]` | Find trades that help you and are defensible for them |

## Proposals (`fbm proposals`)

| Command | Purpose |
|---|---|
| `fbm proposals list [--status S]` | List proposals |
| `fbm proposals show REF` | One proposal in full |
| `fbm proposals approve REF [--note]` | Approve a reviewed proposal; prints the exact steps to make the move in Yahoo |
| `fbm proposals reject REF` | Reject a reviewed proposal |
| `fbm proposals steps REF` | Show the steps again (re-validates an approved proposal first) |
| `fbm proposals done REF [--note "..."]` | Confirm you made the move in Yahoo (demo league: applies it) |

## Learning loop (`fbm learn`)

| Command | Purpose |
|---|---|
| `fbm learn grade [--days N]` | Grade completed moves by realized production |
| `fbm learn calibration` | Simulator calibration (Brier score vs coin flip, reliability) |
| `fbm learn retro [--week-start D]` | Retro data for a fantasy week (JSON) |
| `fbm learn signals` | Grade finished early signals; hit rate by kind and the ownership move after each signal (JSON) |

Decision replay, odds reliability and policy settings have no dedicated CLI commands: use the MCP tools or
`fbm tool replay_grade`, `fbm tool decisions_log`, `fbm tool odds_reliability`, `fbm tool policy_settings`,
`fbm tool policy_suggestions`. Policy changes go through a `policy` proposal (`fbm proposals approve`).

FBM has no draft commands (retired): use Hashtag Basketball's Draft Tracker. `fbm learn projections` is retired too
(Hashtag owns projection accuracy).

## Demo league and notifications

| Command | Purpose |
|---|---|
| `fbm demo build [--seed] [--my-pick]` | Simulate a 12-team snake draft from current projections and save it as the demo league |
| `fbm notify setup` | Store your Gmail address and App Password in the OS credential store |
| `fbm notify test` | Send a test email |
| `fbm notify outbox [--limit N]` | Recent notifications and their delivery status |
