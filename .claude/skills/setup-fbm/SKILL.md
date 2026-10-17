---
name: setup-fbm
description: Set up FBM (your AI fantasy basketball GM) from a fresh clone, or change its setup. Use for "set up FBM", "install the GM", "install FBM", "configure my fantasy GM", "connect my Yahoo league", "change my GM models/automation", or whenever config/settings.toml does not exist yet.
---

# Set up FBM

You are installing FBM for the user: an AI front office for one Yahoo Fantasy Basketball league (9-cat H2H).
The deterministic engine is the `fbm` CLI + MCP server; you only collect choices, run `fbm setup`, and fix
what fails. Works the same in any agent (Copilot, Claude Code, Codex, Cursor, Gemini, or another MCP agent).

## Rules for this conversation

- Ask **ONE question at a time**. Use your ask-user / multiple-choice tool if you have one; otherwise ask in chat
  and offer numbered choices with a recommended default. Keep each explanation to one or two sentences.
- **Never ask for, accept, or echo secrets** (Yahoo Client Secret, Gmail app password, tokens). Credentials are
  typed by the user into the `fbm auth` / `fbm notify setup` prompts, which store them in the OS credential store.
  If you need them run, open a terminal for the user (or tell them the exact command) and wait. If a user pastes
  a secret into chat, tell them to rotate it.
- Never invent model names: suggest examples the user's agent actually offers, and accept whatever they type.
- Re-running is safe: `fbm setup` keeps existing values unless a flag changes them.

## 0. Preflight (no questions)

1. If `config/settings.toml` exists, FBM is already set up: run `uv run fbm setup --check`, show the report, and
   ask what they want to change instead of starting over.
2. Make sure dependencies are installed: `uv sync` (install uv from https://docs.astral.sh/uv/ if missing).
   If `uv` isn't on PATH in your sandbox, say so and give the user the command to run.

## 1. Questions (one at a time, in this order)

1. **Agent + OS.** "Which AI agent are you using?" — copilot (GitHub Copilot app/CLI/VS Code), claude-code,
   codex, cursor, gemini, or generic (anything else with MCP). If it's obvious (you know which agent you are),
   state it and ask them to confirm. Detect the OS yourself if you can; otherwise ask (Windows/macOS/Linux).
2. **Models per role** (names exactly as their agent spells them; empty = agent default):
   - GM (`--gm-model`): their best frontier model — it makes the decisions.
   - Reviewer (`--reviewer-model`): ideally a **different model family** than the GM, so the review is truly
     independent. If their agent only offers one vendor, explain that the reviewer still runs as a fresh-context
     agent with no access to the GM's reasoning, and use the same family.
   - Routine (`--routine-model`): a cheap/fast model for lineup guard and health runs.
   Offer 2–3 examples from the models their agent currently lists (check its model picker / `--help` if you can).
3. **Automation** (`--automation`, then `--jobs`):
   - `none` — they open the agent and ask the GM when they want (always available).
   - `agent-app` — the agent app's own scheduled automations (only if their agent has them).
   - `scheduler` — the OS scheduler (Task Scheduler / launchd / cron) runs the agent headless (needs its CLI).
   - `engine-only` — the OS scheduler runs deterministic `fbm` jobs, no AI (cheapest; no GM judgment).
   Jobs: `data-sync`, `lineup-guard`, `daily-gm`, `weekly-retro`, `trade-scan`, `health`. Recommend
   `data-sync,lineup-guard,daily-gm,weekly-retro` for AI modes and `data-sync,health` for engine-only.
4. **Time zone** (`--timezone`, IANA like `America/Chicago`). Propose the detected one; explain Yahoo game days
   are always US/Eastern dates regardless.
5. **Yahoo access.** Explain: Yahoo's official Fantasy API needs an approved application at
   https://sports.yahoo.com/developer/access/ (can take weeks; DocuSign). Two ways to use a real league:
   (a) **now**, with their own Yahoo sign-in: `uv run fbm yahoo login` opens a browser window once; FBM then reads
   the league read-only the way Yahoo's website does (undocumented by Yahoo, may change; see docs/yahoo.md), or
   (b) the official API once approved. Until either, FBM runs a **demo league** (simulated draft over real NBA
   players), so everything works now. Ask: connect now with Yahoo sign-in / approved API / stay on demo for now.
   If approved: they create a NEW Yahoo app (Fantasy Sports Read; redirect URI `https://localhost:8080`), submit
   its Client ID at https://sports.yahoo.com/developer/application-confirmation/, then continue in step 3 below.
6. **Preferences.**
   - Proposal expiry (`--expiry-hours`, default 18; `[proposals] expiry_hours`). FBM never changes the league —
     lineups are recommendations and moves are proposals the owner approves — so there is no autonomy to choose.
   - Notifications: none or email (`--email` / `--no-email`); quiet hours (`--quiet-hours 23:00-07:00`).
   - Risk appetite (conservative / balanced / aggressive): not a setup flag — journal it later with
     `journal_append` (kind `owner`) for the season-strategy run.

## 2. Run setup

Summarize their answers in one short list, then run (one line):

```
uv run fbm setup --yes --agent <agent> --gm-model "<m>" --reviewer-model "<m>" --routine-model "<m>" --timezone <tz> --automation <mode> --jobs <a,b> --league-source demo --no-email --expiry-hours 18 [--quiet-hours 23:00-07:00] [--yahoo-approved] [--skip-data]
```

It reports each step OK / SKIP / FAIL / PENDING: prerequisites → config/settings.toml → memory/ from templates →
NBA data (one-time prior-season backfill + sync; several minutes the first time) → demo league → `fbm agent install`
→ `fbm automation install` → Yahoo status → `fbm health`. Show the report table to the user.

## 3. Fix and finish

- For each FAIL: read the detail, fix it (or give the user the exact command), then `uv run fbm setup --check`.
- Email chosen: ask the user to run `uv run fbm notify setup` in a terminal (they type the Gmail app password).
- Yahoo sign-in chosen: the user runs `uv run fbm yahoo login` in a terminal (they sign in in the browser window
  it opens; never ask for their Yahoo password), picks the league and accepts the switch. Then you run
  `uv run fbm yahoo check` and show which feeds work.
- Yahoo approved: the user runs `uv run fbm auth` (types Client ID/Secret into its prompts), then you run
  `uv run fbm league select`, `uv run fbm settings`, `uv run fbm setup --yes --league-source yahoo`,
  `uv run fbm snapshot`, `uv run fbm ids`.
- If the FBM MCP tools aren't available in this session yet, tell the user to **restart the agent in this folder**
  so it loads the MCP server, then ask: "Open the GM office". Otherwise run the GM's first steps now:
  `context_pack`, then `health_check`, and summarize what is configured and what is pending.
