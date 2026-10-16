# Agents

FBM is agent-neutral. The canonical prompts live in `agents/`; `uv run fbm agent install --agent <name>` renders them
into the format your agent reads. Generated files are gitignored (except committed copies of the `setup-fbm` skill,
so any agent can find setup in a fresh clone). Edit sources in `agents/`, then re-run the install.

## Roles and skills

| Role (`agents/roles/`) | Job | Model role |
|---|---|---|
| `gm` | Decides; drafts proposals; talks to you | `gm` — your best frontier model |
| `reviewer` | Independently re-checks each proposal; records AGREE / DISAGREE / SPLIT | `reviewer` — ideally a different model family |
| `scout` | News and injury digging | `routine` — a cheap, fast model |

Skills (`agents/skills/*/SKILL.md`) are scripted procedures:

- Scheduled: `daily-gm`, `lineup-guard`, `weekly-retro-preview`, `trade-scan`, `health`
- On demand: `consult`, `lineup`, `matchup`, `waivers`, `stream-plan`, `trade-lab`, `player`, `scout`, `playoffs`,
  `league-intel`, `approvals`, `memory`, `settings`, `season-strategy`, `draft-assist`
- First run: `setup-fbm`

Models are set per role in `config/settings.toml` (`[agent] gm_model`, `reviewer_model`, `routine_model`) or with
`fbm setup --gm-model … --reviewer-model … --routine-model …`. Use names exactly as your agent spells them; empty
means the agent's default.

### Reviewer caveat

Independent review is strongest when the reviewer is a **different model family** from the GM. Claude Code, Codex
CLI and Gemini CLI run only one vendor's models, so there the reviewer runs as a fresh-context agent with an
adversarial rubric and no access to the GM's reasoning — a weaker check. The generated reviewer file says so.

## Per-agent details

| Agent | Files written | Roles become | Skills folder |
|---|---|---|---|
| `copilot` (Copilot app, CLI, VS Code) | `.github/mcp.json`, `.vscode/mcp.json` | `.github/agents/<role>.agent.md` (with `model`) | `.github/skills/` |
| `claude-code` | `.mcp.json`, `CLAUDE.md` (imports `AGENTS.md`) | `.claude/agents/<role>.md` (with tool allow-list and `model`) | `.claude/skills/` |
| `codex` | `.codex/config.toml` (managed MCP block) | skills `fbm-gm`, `fbm-reviewer`, `fbm-scout` | `.agents/skills/` |
| `cursor` | `.cursor/mcp.json`, `.cursor/rules/fbm.mdc` | `.cursor/agents/<role>.md` (`model` or `inherit`) | `.agents/skills/` |
| `gemini` | `.gemini/settings.json`, `GEMINI.md` (imports `AGENTS.md`) | skills `fbm-gm`, `fbm-reviewer`, `fbm-scout` | `.agents/skills/` |
| `generic` | Nothing — prints the stdio MCP server config and instructions to add by hand | — | — |

Check the install with `uv run fbm agent status` (lists files and verifies the MCP server starts and lists its tools).

## Automation

Choose a mode with `fbm setup --automation <mode>` (stored in `[automation] mode`), then `fbm automation install`.
Use `--dry-run` to see exactly what would be installed.

| Mode | What runs | Agents |
|---|---|---|
| `none` | Nothing scheduled | all |
| `agent-app` | FBM prints prompts and schedules for you to paste into your agent app's scheduler (e.g. Copilot app Automations) | agents whose app supports scheduled prompts |
| `scheduler` | The OS scheduler (Task Scheduler on Windows, launchd on macOS, cron on Linux) runs your agent's headless CLI | `copilot`, `claude-code`, `codex`, `cursor`, `gemini` |
| `engine-only` | The OS scheduler runs deterministic `fbm` commands — no AI, no AI cost | all |

### Jobs (times are US/Eastern; converted to your machine's local time)

| Job | When (ET) | Skill (AI modes) | Model role | Engine-only commands |
|---|---|---|---|---|
| `data-sync` | daily 03:00 | — | routine | `fbm data sync` |
| `lineup-guard` | hourly 11:00–22:00 | `lineup-guard` | routine | `fbm lineup` |
| `daily-gm` | daily 04:00 | `daily-gm` | gm | `fbm snapshot`, `fbm lineup` |
| `weekly-retro` | Mon 05:00 | `weekly-retro-preview` | gm | `fbm learn retro` |
| `trade-scan` | Tue & Fri 10:00 | `trade-scan` | gm | — (AI only) |
| `health` | daily 03:30 | `health` | routine | `fbm health` |

`uv run fbm automation jobs` prints the authoritative catalog. Each run logs to `reports/automation/`. Scheduled
jobs only run while your computer is on.

### Headless commands and safety flags (`scheduler` mode)

Scheduled AI runs are **read-only plus FBM tools**: they can analyze, journal and draft proposals, but can never
approve one — approval always needs your words.

| Agent | Command FBM schedules |
|---|---|
| `copilot` | `copilot -p <prompt> -s --no-ask-user --allow-tool=fbm --deny-tool=shell --deny-tool=write` |
| `claude-code` | `claude -p <prompt> --allowedTools=mcp__fbm,Read,Grep,Glob --disallowedTools=Bash,Edit,Write,NotebookEdit` |
| `codex` | `codex exec --sandbox read-only [--model M] <prompt>` |
| `cursor` | `cursor-agent -p --output-format text --approve-mcps [--model M] <prompt>` (print mode without `--force` only proposes file changes) |
| `gemini` | `gemini --approval-mode default --allowed-mcp-server-names fbm [--model M] -p <prompt>` |

`--model <name>` is added (for every agent) when a model is configured for the job's role.

## Switching agents

```sh
uv run fbm agent uninstall              # removes only files FBM generated for the current agent
uv run fbm setup --agent <new-agent>    # writes settings and generates the new agent's files
```

If you use `scheduler` automation, re-run `uv run fbm automation install` afterwards so jobs call the new agent.
