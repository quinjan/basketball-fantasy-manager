# AI hosts for the FBM GM: Codex, GitHub Copilot, Claude Code

Research for [#4](https://github.com/quinjan/basketball-fantasy-manager/issues/4) (map [#1](https://github.com/quinjan/basketball-fantasy-manager/issues/1), "Wayfinder: spec for FBM v2").
Compiled 2026-11-05 from primary sources (official docs, official repos/changelogs). All web content was treated as
untrusted data. Docs sites show no "last updated" stamps, so sources are cited as *accessed 2026-11-05* plus any
version/changelog date shown.

> **Fast-churn warning.** Model names, context windows, compaction thresholds, CLI versions and preview/stable labels
> change on a scale of weeks. Versions at research time: Codex CLI v0.166.0 (2026-10-29), Copilot CLI v1.0.101
> (2026-10-20), Claude Code v2.1.318. Rows marked ⚡ are the ones most likely to be stale; re-check before building on
> them. Doc hosts themselves moved recently (`developers.openai.com/codex` → `learn.chatgpt.com/docs`,
> `docs.claude.com/.../claude-code` → `code.claude.com/docs`).

## Question

For Codex (CLI), GitHub Copilot (CLI / app) and Claude Code: sub-agent support and how it's invoked; skill and
instruction-file formats and what loads every turn vs on demand; context window sizes and compaction; shell /
tool-calling model; web search / fetch; per-sub-agent model selection. What is the common denominator FBM can design
for, and what fallback is needed where a capability is missing?

## Short answer

All three hosts now converge on the same five primitives: **AGENTS.md** (always loaded), **Agent Skills / SKILL.md**
(description always loaded, body on demand), **custom sub-agents with a fresh context that return only a final
message**, a **shell tool**, and a **headless CLI** (`codex exec`, `copilot -p`, `claude -p`). FBM can design its v2
architecture on those alone. The real differences are (1) where files live and in what format, (2) how delegation is
*triggered* (Codex only delegates when told to), (3) whether sub-agents see AGENTS.md, (4) tool-output truncation
(only Codex and Claude document it), and (5) **cross-model-family sub-agents, which only Copilot supports natively**.
For (5) the fallback is shelling out to another vendor's headless CLI, and failing that a same-family fresh-context
challenger flagged as weaker.

## Capability matrix

| Capability | Codex CLI | GitHub Copilot (CLI / app / VS Code) | Claude Code |
|---|---|---|---|
| **Sub-agents** | Native, stable, on by default (`spawn_agent`, `send_input`, `wait_agent`, `close_agent`; `[agents]` config) [1][2] | Native: custom agents run as sub-agents via the `agent` tool (aliases `Task`, `custom-agent`); built-in Explore/Task/General-purpose/Code-review/Research/Rubber-duck; `/fleet` for parallel [13][15][17][19] | Native: `Agent` tool (renamed from `Task` in v2.1.63, alias kept); background by default [34][35] |
| Sub-agent definition | `.codex/agents/*.toml` (project), `~/.codex/agents/*.toml`; required `name`, `description`, `developer_instructions`; optional `model`, `model_reasoning_effort`, `skills.config` [2] | `.github/agents/*.agent.md` (repo), `~/.copilot/agents/`, org `.github-private`; YAML `description` (required), `name`, `tools`, `model`, `disable-model-invocation`, `user-invocable`; body ≤ 30,000 chars [15][16][17] | `.claude/agents/*.md`, `~/.claude/agents/`, managed, `--agents` JSON, plugins; YAML `name`, `description`, `tools`, `disallowedTools`, `model`, `permissionMode`, `maxTurns`, `skills`, `mcpServers`, `background`, `omitClaudeMd`, `effort`, `autoCompactWindow`… [34] |
| How delegation is triggered | **Only when the user asks or when AGENTS.md / skill instructions request it** — not autonomous [2] | Natural language ("use the X agent"), `/agent`, `--agent=X`, main agent calling the `agent` tool; `disable-model-invocation` blocks auto-use [15][17][18] | Automatic by description match ("use proactively"), explicit `Agent` call, `@agent` mention, `--agent` for the whole session [34] |
| Fresh context / what returns | Own "agent thread"; parent gets a summary; consolidated response when several run [2] | "Own context window"; reports results back; intermediate work stays out of the main context [13][14][16] | Own context window; "parent doesn't see the subagent's intermediate tool calls or outputs, only that final result" [34][35] |
| Sub-agent sees project instructions? | Not documented | **No by default** — repository custom instructions are included for the main agent, not custom sub-agents (opt-in setting) [13] | Yes by default (CLAUDE.md); `omitClaudeMd` opts out [34] |
| Sub-agent permissions | Inherits parent sandbox/approval/provider/MCP; can't override [2] | Per-agent `tools` allow-list [17] | Per-agent `tools`/`disallowedTools`/`permissionMode` [34] |
| **Root instruction file** | `AGENTS.md`, concatenated root→cwd, **capped at 32 KiB** (`project_doc_max_bytes`); `AGENTS.override.md`; does not read `.github/copilot-instructions.md` [1][7] | `.github/copilot-instructions.md`, `.github/instructions/*.instructions.md` (`applyTo`), **and AGENTS.md / CLAUDE.md / GEMINI.md**; combined without a general precedence order [23][24][25] | `CLAUDE.md` (+ `@imports`, max 4 hops); **reads AGENTS.md natively since v2.1.277** — default mode reads AGENTS.md only if no CLAUDE.md/CLAUDE.local.md exists [38] |
| **Skills** (agentskills.io SKILL.md) | `.agents/skills` (cwd, parents, repo root), `~/.agents/skills`; implicit (description) or `$skill` / `/skills`; descriptions budgeted by `skills.max_context_tokens` (2 % of window, ≤ 10k tokens) [8] | `.github/skills`, `.claude/skills`, **or** `.agents/skills`; `~/.copilot/skills`, `~/.agents/skills`; `name`, `description`, `allowed-tools` [21][22] | `.claude/skills` (+ nested), `~/.claude/skills`, plugins; `description`, `disable-model-invocation`, `user-invocable`, `allowed-tools`, `model`, `context: fork` (+ `agent`) [36] |
| **Every turn vs on demand** | Every turn: AGENTS.md chain (≤ 32 KiB), skill name+description list. On demand: skill body [1][8] | Every turn: custom instructions + AGENTS.md etc., skill descriptions. On demand: skill body ("without adding them to every interaction") [21][26] | Every turn: CLAUDE.md (or AGENTS.md), auto memory (first 200 lines / 25 KB), MCP tool names, skill descriptions. On demand: skill body; after compaction bodies re-injected ≤ 5k tokens each / 25k total [36][37][38] |
| ⚡ Default model / window | GPT-6.1 Sol recommended; **500K-token** window in Codex [3] | User-selected (`/model`, `--model`, `COPILOT_MODEL`), multi-vendor (Claude, GPT…); README still says default Claude Sonnet 4.5 (likely stale); window = underlying model's, not separately documented [18][27] | Fable 5.1 / Opus / Sonnet / Haiku 5.5: **1M tokens**; older 4.6 models 200K [39] |
| ⚡ Compaction | Auto at `model_auto_compact_token_limit` (model default if unset) + optional turn-end % (`model_post_turn_compact_threshold_percent`); `/compact`; custom compact prompt [1][9] | Auto at **95 %** of the token limit, in background ("virtually infinite sessions"); `/compact`; `/context` shows usage [28] | Auto at the model limit (~967K for native-1M models, 200K for older); `/autocompact`, `autoCompactWindow` (100K–1M, also per sub-agent); `/compact <focus>`; `DISABLE_AUTO_COMPACT` [37][39][40] |
| **Shell / tools** | Shell tool in sandbox `read-only` / `workspace-write` (network off by default) / `danger-full-access`; approvals `on-request` / `never` / granular; MCP client [1][10][11] | Shell + file tools; per-tool allow/deny (`--allow-tool=shell(cmd)`, `--deny-tool`), `--allow-all`; `/sandbox enable`; MCP client (`~/.copilot/mcp-config.json`, GitHub MCP built in) [15][28][30] | Bash/PowerShell tools; permission modes `default`/`acceptEdits`/`plan`/`auto` (default ≥ v2.1.283)/`dontAsk`/`bypassPermissions`; OS sandbox on macOS/Linux/WSL2 only — **unsandboxed on native Windows**; MCP client [41][42][43] |
| Tool-output truncation | `tool_output_token_limit` (history budget per tool output); per-MCP-tool `output_token_limit` [1] | **Not documented** | `MAX_MCP_OUTPUT_TOKENS` (25k default, MCP only); > 50,000-char results saved to a file [40] |
| **Headless** | `codex exec` (`--json` JSONL events, `-o` last message, `--output-schema`, `-m`, `--sandbox`) [5][6] | `copilot -p` (`--output-format json`, `--agent`, `--model`, `--allow-tool`) [18][32] | `claude -p` (`--output-format json/stream-json`, `--allowedTools`, `--model`, `--agent`) [44] |
| **Web search / fetch** | Hosted `web_search`, on by default but **`cached` mode** by default (OpenAI index); `live` via `--search` or `web_search="live"`; no separate fetch tool [12] | `web` tool alias (`WebSearch`, `WebFetch`), backed by `github-mcp-server/web_search`; default-on for the main session not explicitly documented [17][29] | `WebSearch` + `WebFetch`, on by default, permission-gated, per-domain rules [35][42] |
| **Per-sub-agent model** | Yes: custom-agent `model` / `model_reasoning_effort`, spawn-time override, `[agents].default_subagent_model`; provider is inherited → **same provider only** [2] | Yes: `model:` frontmatter, **cross-vendor** (Claude, GPT, …) in one session [17][33]; observed first-hand in the Copilot app on 2026-11-05: sub-agent calls accept a per-call model across Claude and GPT families | Yes: `model: sonnet|opus|haiku|fable|<id>|inherit`, `CLAUDE_CODE_SUBAGENT_MODEL`; **Anthropic models only** [34][39] |
| Different-family challenger | Shell out: `claude -p --model …` or `copilot -p --model …` from the shell tool (DIY); or a Responses-API-compatible `model_providers` entry in a user profile [4] | Native: a `challenger.agent.md` with `model:` set to the other family | Shell out: `codex exec -m …` or `copilot -p --model …` via Bash (DIY, not a documented feature) [35][42] |

## Common denominator FBM can design for

1. **One root instruction file: `AGENTS.md`.** Read natively by Codex, Copilot and Claude Code (≥ v2.1.277; FBM's
   generated `CLAUDE.md` → `@AGENTS.md` stub covers older versions). Loaded every turn; Codex silently truncates the
   chain at 32 KiB. It must be the *small* constitution, not the manual.
2. **Skills in SKILL.md format** with a precise `name` + `description`. On every host only the description is
   resident; the body loads when invoked. Twenty skill descriptions are cheap; skill bodies are free until used.
3. **Named sub-agents with a fresh context that return one final message.** All three have them; the file format
   differs (`.agent.md` / `.md` with YAML / `.toml`), so FBM keeps rendering from one canonical source as it does now.
4. **Explicit delegation instructions.** Because Codex delegates only when instructions say so, every tool-heavy
   skill should say literally "spawn the `<name>` sub-agent with this brief and wait for its digest". That phrasing
   also works on Copilot and Claude Code, so it is the portable form.
5. **Shell as the tool transport** (`uv run fbm tool …` → JSON). All three have a shell tool and allow-lists; no MCP
   needed (MCP output caps differ and Claude's 25k cap applies to MCP only anyway).
6. **Headless CLIs** for scheduled runs and as the universal fallback for both delegation and cross-family review.
7. **Per-sub-agent model selection** exists on all three — within one vendor on Codex and Claude Code, across vendors
   only on Copilot.

Not common (design must not depend on it): automatic delegation by description (Codex lacks it), AGENTS.md
visibility inside sub-agents (Copilot excludes it by default), host-side truncation of shell output (Copilot
undocumented), live web search by default (Codex defaults to cached), a native Windows sandbox (Claude Code has none),
any particular compaction threshold or context size.

## Recommended fallbacks

| Capability missing / weak | Where | Fallback |
|---|---|---|
| Cross-family challenger sub-agent | Codex, Claude Code (in-process) | 1) If another vendor CLI is installed and signed in, the challenger sub-agent (or skill) shells out: `copilot -p --agent challenger --model <other>` / `claude -p --model …` / `codex exec -m … --sandbox read-only`, reading a proposal ref and returning a verdict digest. 2) Else same-family fresh-context challenger with the adversarial rubric, and the review record says `same-family` so the owner knows it is weaker (today's behaviour). |
| Automatic delegation | Codex | Skill bodies name the sub-agent explicitly (see point 4 above); GM routing table lists which skills delegate. |
| Sub-agents see AGENTS.md | Copilot (excluded by default) | Every sub-agent/role file carries its own short hard-rules block (numbers from tools, untrusted content, no Yahoo writes, ≤ 1.5 KB digest). Don't rely on inheritance. |
| Host truncation of large tool output | Copilot (undocumented); Codex/Claude truncate differently | FBM enforces its own budget: compact/digest output modes and size caps on `fbm tool` so the GM thread never ingests 14–21 KB payloads; the full payload goes to a file a sub-agent can read. |
| Sub-agents at all (disabled, older version, unknown headless behaviour) | Any (`agents.enabled=false`, old CLI) | Nested headless run of the same host (`codex exec` / `claude -p` / `copilot -p`) writing the digest to `reports/…`; last resort: run inline using digest-mode tools only. |
| Live web search | Codex default `cached`; Copilot main-session default unclear | FBM's own news/injury tools (ESPN) are the primary source; web search is supplementary; scout runs with live search where configurable (`codex exec --search`). |
| Compaction reliability | All (thresholds differ, none guaranteed to preserve what matters) | Don't depend on compaction: keep the main GM thread small by design, persist state to `memory/` via `journal_append`, and let each scheduled run start fresh from `context_pack`. Claude's per-sub-agent `autoCompactWindow` / Codex `compact_prompt` are optional tuning, not architecture. |
| Sandbox on Windows | Claude Code (none on native Windows) | Keep relying on allow-lists (`Bash(uv run fbm tool:*)`) and FBM's no-Yahoo-write design, not on OS sandboxing. |

## Implications for FBM's v2 design

- **Static prompt budget.** What every host loads every turn = AGENTS.md (today 8.4 KB) + the session agent's body
  (`agents/roles/gm.md`, today 16 KB, when run as a custom agent) + ~20 skill descriptions. Target a small AGENTS.md
  (constitution + routing, a few KB) and a lean GM body; move procedures into skill bodies and reference material
  into files skills read on demand. Codex's 32 KiB cap and Copilot's 30,000-char agent-body cap are hard ceilings;
  context rot is the real limit well below them (Codex docs make the same point [2]).
- **Skill descriptions are the routing layer** on every host: write them as "use when …" triggers; keep them short.
  Mark scheduled/owner-only skills `disable-model-invocation: true` where supported (Copilot agents, Claude skills) so
  they don't auto-fire.
- **Tool-heavy skills → sub-agents returning ≤ 1.5 KB digests** is supported on all three. Render each worker
  (e.g. data/matchup/waiver analyst, scout, challenger) per host: `.github/agents/*.agent.md`, `.claude/agents/*.md`,
  **`.codex/agents/*.toml`**. FBM's renderer currently says Codex has "no project-level custom-agent format we rely
  on" and ships roles as skills (`src/fbm/agents/render.py`, `_role_file`); that is now outdated — Codex has project
  custom agents (`.codex/agents/*.toml`, stable) [2].
- **Challenger per host.** Copilot: native `challenger.agent.md` with `model:` set to a different family than the GM
  (e.g. GM on Claude → challenger on GPT, or vice versa). Claude Code / Codex: challenger shells out to another
  installed vendor CLI headlessly; detect installed/authenticated CLIs at `fbm setup`/`fbm agent status`; otherwise
  same-family fallback recorded as such. `SINGLE_VENDOR` in `render.py` and the "Reviewer caveat" in `docs/agents.md`
  are correct about in-process sub-agents but miss the shell-out route.
- **Claude Code AGENTS.md.** With the default `claude-md-or-agents-md` mode, FBM's `CLAUDE.md` stub means AGENTS.md is
  read only through the `@AGENTS.md` import — fine. Under the `claude-md-and-agents-md` mode it would load twice;
  consider dropping the stub once Claude ≥ 2.1.277 can be assumed.
- **Skill folders.** Copilot reads `.github/skills`, `.claude/skills` **and** `.agents/skills`. FBM commits
  `setup-fbm` to all three and installs others per host; duplicate-name handling on Copilot is not documented
  (see open questions). `.agents/skills` + `.claude/skills` would cover all three hosts.
- **Size the tool layer, not the host.** Because truncation is host-specific (and undocumented on Copilot), output
  budgets belong in `fbm tool` (digest modes, caps, write-full-to-file), which makes behaviour identical on every
  host.

## Open questions (candidate tickets)

1. **Copilot duplicate skills:** what happens when the same skill name exists in `.github/skills`, `.claude/skills`
   and `.agents/skills` (FBM commits `setup-fbm` to all three)? Dedup, duplicate listing, or conflict? Needs a test.
2. **Sub-agents in headless runs:** do `codex exec`, `copilot -p` and `claude -p` spawn sub-agents the same way as
   interactive sessions (scheduled `daily-gm` depends on it)? Not explicitly documented; needs an empirical check.
3. **Cross-CLI challenger mechanics:** auth, cost, sandbox/network (Codex `workspace-write` blocks network unless
   enabled; Claude sandbox `allowedDomains`), Windows quoting, and timeouts when one host shells out to another.
4. **Does Codex load AGENTS.md into spawned sub-agents?** Not documented. (Copilot: no by default; Claude: yes.)
5. **Copilot model/context visibility:** GitHub doesn't document per-model context windows for Copilot CLI; `/context`
   shows usage at runtime. Is there a stable way for FBM to read the active window size?
6. **Implementation (out of scope here):** render Codex roles as `.codex/agents/*.toml`; add explicit "spawn …"
   phrasing to delegating skills; per-role hard-rules block for sub-agents.

Out of scope: Cursor and Gemini CLI (FBM supports them; not asked), pricing/quotas, Copilot cloud coding agent
(async, issue-triggered — a different mechanism from in-session sub-agents [13][20]).

## Sources (accessed 2026-11-05)

Codex (OpenAI; `developers.openai.com/codex/*` now redirects to `learn.chatgpt.com/docs/*`; repo
`github.com/openai/codex` changelog shows v0.166.0, 2026-10-29)

1. Config reference — https://developers.openai.com/codex/config-reference (`features.multi_agent`, `[agents]`, `project_doc_max_bytes`, `model_auto_compact_token_limit*`, `tool_output_token_limit`, `skills.max_context_tokens`)
2. Subagents — https://developers.openai.com/codex/agent-configuration/subagents (verified directly 2026-11-05)
3. Models ⚡ — https://developers.openai.com/codex/models (GPT-6.1 Sol 500K window; retirement dates)
4. Advanced config — https://developers.openai.com/codex/config-advanced (profiles as files since 0.134.0; `model_providers`)
5. Non-interactive — https://developers.openai.com/codex/noninteractive (`codex exec --json`)
6. CLI commands — https://developers.openai.com/codex/developer-commands
7. AGENTS.md guide — https://developers.openai.com/codex/guides/agents-md
8. Skills — https://developers.openai.com/codex/skills
9. Slash commands — https://developers.openai.com/codex/reference/slash-commands
10. Sandbox / approvals — https://developers.openai.com/codex/sandbox
11. MCP — https://developers.openai.com/codex/extend/mcp
12. Web search — https://developers.openai.com/codex/web-search

GitHub Copilot (docs.github.com; `github.com/github/copilot-cli` changelog v1.0.101, 2026-10-20)

13. Custom agents and subagents — https://docs.github.com/en/copilot/concepts/customizations/custom-agents
14. Comparing customizations — https://docs.github.com/en/copilot/concepts/customizations/comparing-customizations
15. Copilot CLI overview — https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/overview
16. Create custom agents for CLI — https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli
17. Custom agents configuration — https://docs.github.com/en/copilot/reference/custom-agents-configuration (verified directly 2026-11-05)
18. CLI programmatic reference — https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference
19. Delegating and parallelizing work — https://docs.github.com/en/copilot/concepts/agent-capabilities/choosing-and-delegating-agent-work/delegating-and-parallelizing-work-with-agents
20. Cloud agent sessions — https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/start-copilot-sessions
21. Agent skills — https://docs.github.com/en/copilot/concepts/customizations/agent-skills
22. Add skills (CLI) — https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills
23. Custom instructions — https://docs.github.com/en/copilot/concepts/prompting-and-context/custom-instructions
24. Add custom instructions (CLI) — https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions
25. Custom instructions support matrix — https://docs.github.com/en/copilot/reference/custom-instructions-support
26. Comparing customizations (instructions vs skills) — same as [14]
27. Copilot CLI README ⚡ — https://github.com/github/copilot-cli (default model statement likely stale)
28. Copilot CLI concepts — https://docs.github.com/en/copilot/concepts/copilot-surfaces/copilot-cli (95 % auto-compaction, `/compact`, `/context`, `/sandbox`)
29. Copilot CLI changelog — https://github.com/github/copilot-cli/blob/main/changelog.md (v1.0.100 2026-10-19, v1.0.101 2026-10-20)
30. Permissions — same page as [28]
31. MCP for cloud agent — https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/configure-mcp-servers
32. Run CLI programmatically — https://docs.github.com/en/copilot/how-tos/copilot-cli/automate-copilot-cli/run-cli-programmatically
33. Custom agent example with `model:` — linked from [17] (GitHub Blog example)

Claude Code (Anthropic; `code.claude.com/docs/en/*`; CHANGELOG v2.1.318)

34. Subagents — https://code.claude.com/docs/en/sub-agents
35. Tools reference — https://code.claude.com/docs/en/tools-reference
36. Skills — https://code.claude.com/docs/en/skills
37. Context window — https://code.claude.com/docs/en/context-window
38. Memory / CLAUDE.md / AGENTS.md — https://code.claude.com/docs/en/memory (verified directly 2026-11-05)
39. Models ⚡ — https://platform.claude.com/docs/en/models/overview and https://code.claude.com/docs/en/model-config
40. Environment variables — https://code.claude.com/docs/en/env-vars
41. Permission modes — https://code.claude.com/docs/en/permission-modes
42. Sandboxing — https://code.claude.com/docs/en/sandboxing
43. MCP — https://code.claude.com/docs/en/mcp
44. Headless — https://code.claude.com/docs/en/headless

FBM (read-only, this repo at `main`): `src/fbm/agents/render.py` (`SKILL_DIRS`, `SINGLE_VENDOR`, `_role_file`),
`docs/agents.md` (per-agent install table, reviewer caveat, headless commands).
