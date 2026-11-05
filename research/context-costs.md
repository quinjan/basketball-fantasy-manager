# FBM context costs (ticket #5)

## Method
- Measured 2026-11-05 (owner TZ UTC+8; ET game day 2026-11-05) on the live league: `[league] source = "yahoo"`, Hashtag Basketball enabled and healthy.
- Static prompt: raw file bytes; tokens counted with tiktoken `o200k_base` (an approximation: Claude/Copilot tokenizers differ by roughly +-15%). "Description" = the YAML frontmatter block (name+description), which hosts load every turn; bodies load on demand.
- Tool outputs: each tool run once with default args via `uv run fbm tool <name>` (stdout bytes only; stderr excluded), tokens via the same tokenizer (JSON is token-dense: ~2.7 bytes/token here, so bytes/4 under-counts by ~1.5x). Real values for args: player `Lauri Markkanen`, FA `Cason Wallace`, opponent `Team Shohoku`.
- Run order: `refresh_data` once (first), then `snapshot`, then all others. Runtime is per process incl. uv/Python start-up (~1.7 s floor).
- Not run (write tools): confirm_done, create_proposal, hashtag_import, journal_append, lineup_steps (journals the recommendation even without confirm), proposal_steps, record_owner_decision, record_review, replay_grade, send_email, verify_moves, weekly_plan (logs decisions), plus `get_proposal` (no proposals exist to read). `optimize_lineup` is not flagged [write] and was run.
- Post-cap: measured at current `main` (includes `5e6ff40`).

## Static prompt
| Item | Bytes | Tokens (o200k) | Loads |
|---|---:|---:|---|

| AGENTS.md | 8426 | 2072 | every turn (host instruction file) |
| agents/roles/gm.md (GM manual) | 16194 | 3905 | when GM agent runs |
| agents/roles/reviewer.md | 4203 | 994 | when agent invoked |
| agents/roles/scout.md | 1744 | 401 | when agent invoked |
| 19 skill bodies (agents/skills/*/SKILL.md) | 51101 | 12724 | on demand (1 at a time) |
| 19 skill descriptions (frontmatter only) | 4541 | 1040 | **every turn** |
| 3 agent descriptions (frontmatter only) | 1625 | 383 | **every turn** |
| **Always-on total** (AGENTS.md + 19 skill + 3 agent descriptions) | **14592** | **3495** | |
| Always-on + GM manual (a GM session) | 30786 | 7400 | |
| Always-on + GM manual + `daily-gm` skill body | 35331 | 8554 | |

Largest bodies: `setup-fbm` 8460 B (2132 tok, one-off), `daily-gm` 4545 B, `weekly-retro-preview` 4140 B, `league-intel` 3519 B, `trade-scan` 3494 B.

### Host copies and duplicates
| Location | Contents | Size | Notes |
|---|---|---:|---|
| `agents/` | canonical: 3 roles, 19 skills | see above | source of truth |
| `.github/agents/*.agent.md` | gm/reviewer/scout copies (Copilot) | gm 16124, reviewer 4505, scout 2027 B | near-duplicates of roles (frontmatter + manual; gm differs by ~70 B) |
| `.github/skills/*/SKILL.md` | all 19 skills, byte-identical sizes to `agents/skills` | 51101 B | full duplicate |
| `.claude/skills/`, `.agents/skills/` | only `setup-fbm` (8460 B each) | 8460 B each | installed copies of one skill; Claude/other hosts therefore see only setup-fbm until `fbm setup` installs more |
| `CLAUDE.md` | 0 bytes / absent | - | host reads AGENTS.md |

Per host (best inference, not host-verified): every turn = AGENTS.md + skill/agent descriptions for whichever dir that host scans (Copilot: `.github/skills` + `.github/agents` = all 22 descriptions; Claude: `.claude/skills` = only the setup-fbm description in this clone); on demand = skill bodies when invoked, agent manual when the sub-agent is spawned. Duplication across hosts does not add to one host's context, but means every skill edit must be synced to up to 4 copies.

## Tool outputs (default args, sorted by size)

| Tool | Args used | Exit | Bytes | KB | Tokens | Secs | >3 KB |
|---|---|---:|---:|---:|---:|---:|:--:|
| constants | - | 0 | 44953 | 43.9 | 10930 | 1.9 | **YES** |
| weekly_retro_data | - | 0 | 25011 | 24.4 | 9723 | 3.9 | **YES** |
| context_pack | - | 0 | 24976 | 24.4 | 7998 | 2.6 | **YES** |
| grade_decisions | - | 0 | 17957 | 17.5 | 7170 | 2.3 | **YES** |
| week_box | - | 0 | 17111 | 16.7 | 7137 | 2.0 | **YES** |
| hashtag_status | - | 0 | 16429 | 16.0 | 6476 | 1.8 | **YES** |
| buy_sell | - | 0 | 14359 | 14.0 | 4390 | 2.8 | **YES** |
| signals | - | 0 | 14252 | 13.9 | 4645 | 2.3 | **YES** |
| signals_new | new_only=true | 0 | 14252 | 13.9 | 4645 | 2.3 | **YES** |
| team_roster | team=Team Shohoku | 0 | 13032 | 12.7 | 5278 | 2.9 | **YES** |
| find_trades | partner=Team Shohoku | 0 | 12589 | 12.3 | 4583 | 11.4 | **YES** |
| league_intel | - | 0 | 11836 | 11.6 | 3994 | 2.4 | **YES** |
| my_roster | - | 0 | 11713 | 11.4 | 4984 | 2.8 | **YES** |
| decisions_log | - | 0 | 11266 | 11.0 | 2374 | 1.9 | **YES** |
| punt_scenarios | - | 0 | 10826 | 10.6 | 3460 | 3.2 | **YES** |
| ownership_trends | - | 0 | 10289 | 10.0 | 3619 | 2.1 | **YES** |
| fa_radar | - | 0 | 10017 | 9.8 | 3195 | 2.2 | **YES** |
| free_agents | - | 0 | 9612 | 9.4 | 3154 | 3.6 | **YES** |
| free_agents_sortvalue | sort=value | 0 | 9612 | 9.4 | 3154 | 3.6 | **YES** |
| rankings_avail | available_only=true | 0 | 9247 | 9.0 | 2922 | 2.5 | **YES** |
| refresh_data | - | 0 | 8954 | 8.7 | n/a | 256.7 | **YES** |
| free_agents_sortweek | sort=week | 0 | 8947 | 8.7 | 2905 | 3.6 | **YES** |
| scoreboard | - | 0 | 8157 | 8.0 | 2786 | 1.7 | **YES** |
| rankings | - | 0 | 7042 | 6.9 | 2157 | 2.5 | **YES** |
| next_week | - | 0 | 6959 | 6.8 | 2382 | 5.9 | **YES** |
| policy_settings | - | 0 | 6078 | 5.9 | 1579 | 1.7 | **YES** |
| transactions | - | 0 | 4713 | 4.6 | 1599 | 3.2 | **YES** |
| league_profile | - | 0 | 4500 | 4.4 | 2050 | 2.3 | **YES** |
| player_card | query=Lauri Markkanen | 0 | 4144 | 4.0 | 1457 | 2.1 | **YES** |
| simulate_matchup | - | 0 | 3899 | 3.8 | 1464 | 2.4 | **YES** |
| league_overview | - | 0 | 3441 | 3.4 | 1278 | 1.9 | **YES** |
| news | player=Lauri Markkanen | 0 | 3109 | 3.0 | 1045 | 1.9 | **YES** |
| injuries | - | 0 | 2909 | 2.8 | 810 | 1.8 |  |
| injuries_my_roster | scope=my_roster | 0 | 2909 | 2.8 | 810 | 1.7 |  |
| manager_profile | team=Team Shohoku | 0 | 2761 | 2.7 | 1050 | 2.3 |  |
| playoff_outlook | - | 0 | 2732 | 2.7 | 1072 | 2.6 |  |
| game_log | player=Lauri Markkanen | 0 | 2399 | 2.3 | 1063 | 1.8 |  |
| waiver_decision | player=Cason Wallace drop=Maxime Raynaud | 0 | 2275 | 2.2 | 822 | 2.8 |  |
| standings | - | 0 | 2239 | 2.2 | 843 | 1.7 |  |
| prediction_calibration | - | 0 | 2045 | 2.0 | 836 | 1.8 |  |
| evaluate_trade | partner=Team Shohoku give=Lauri Markkanen get=<Shohoku player #1> | 0 | 1936 | 1.9 | 710 | 2.0 |  |
| health_check | - | 0 | 1708 | 1.7 | 507 | 1.8 |  |
| schedule | - | 0 | 1687 | 1.6 | 582 | 1.8 |  |
| snapshot | - | 0 | 1582 | 1.5 | n/a | 19.3 |  |
| streaming_plan | - | 0 | 1530 | 1.5 | 473 | 2.7 |  |
| signal_grades | - | 0 | 1263 | 1.2 | 367 | 1.8 |  |
| odds_reliability | - | 0 | 1196 | 1.2 | 503 | 1.6 |  |
| optimize_lineup | - | 0 | 1158 | 1.1 | 386 | 2.5 |  |
| evaluate_add_drop | add=Cason Wallace drop=Maxime Raynaud | 0 | 598 | 0.6 | 246 | 2.6 |  |
| policy_suggestions | - | 0 | 254 | 0.2 | 50 | 1.8 |  |
| pending_offers | - | 0 | 162 | 0.2 | 42 | 3.6 |  |
| evaluate_il_activation | activate=Keegan Murray | 1 | 97 | 0.1 | 25 | 1.9 |  |
| list_proposals | - | 0 | 17 | 0.0 | 5 | 1.7 |  |

Exit 1 rows are input errors from my arguments (see below); `refresh_data` 256.7 s is the real ESPN+Hashtag import and was run once. `snapshot` tokens not measured (re-running would refetch Yahoo). `free_agents sort=value` equals the default (identical bytes); `signals new_only=true` returned the same bytes as default (nothing filtered); `injuries scope=my_roster` equals default.
Errors: `evaluate_il_activation activate=Keegan Murray` -> `roster is full (13/13 active): a drop is required` (97 B; valid precondition error, needs `drop=`); first `evaluate_trade` attempt errored because I used my own player as `get` (80 B); the table shows the corrected re-run (1936 B). Not run: see Method.

## Daily-update trace (estimate)

Raw = every output lands in the GM context. `*` = write tools not run (`lineup_steps`, `weekly_plan`); excluded, so totals are slight under-estimates. `snapshot`/`refresh_data` tokens estimated as bytes/2.7 (measured JSON ratio).

| Step | Tools | Raw bytes | Raw tokens |
|---|---|---:|---:|
| context_pack | 1 | 24976 | 7998 |
| 1 refresh_data + snapshot + health_check | 3 | 12244 | 4409 |
| 2 lineup: injuries(my_roster), schedule, optimize_lineup, lineup_steps* | 3 | 5754 | 1778 |
| 3 matchup: scoreboard, week_box, simulate_matchup, streaming_plan, free_agents sort=week (weekly_plan*) | 5 | 39644 | 14765 |
| 4 breakouts: signals new_only, fa_radar, ownership_trends, buy_sell, free_agents value, rankings avail, waiver_decision | 7 | 70051 | 22747 |
| **Tool output total** | | **152669** | **51697** |
| Static always-on (AGENTS.md + descriptions) | | 14592 | 3495 |
| GM manual | | 16194 | 3905 |
| daily-gm skill body | | 4545 | 1154 |
| **Raw total, GM context (excl. host system prompt/tools, PRD: ~75-83k)** | | **188000** | **60251** |
| **Delegated** (steps 1,3,4 each to a sub-agent returning a 1.5 KB digest; context_pack + lineup stay inline) | | **70669** | **20037** |

Delegated tool-output part: 35338 B / ~11483 tok vs raw 152669 B / 51697 tok. Sub-agents still each spend their own context on the raw JSON (total tokens across agents unchanged) and need their own static prompt; delegation protects the main context, not total spend. A v2 target of <=3 KB per default tool would cut the 22-tool chain to <= ~66 KB worst case (<~25k tok) before any digesting.

No real past-session token trace was located; this estimate is from measurements only.

## Key findings

- **32 of 53 measured tool runs exceed 3 KB** (21 within). Top 10: constants 43.9 KB, weekly_retro_data 24.4 KB, context_pack 24.4 KB, grade_decisions 17.5 KB, week_box 16.7 KB, hashtag_status 16.0 KB, buy_sell 14.0 KB, signals 13.9 KB, signals_new 13.9 KB, team_roster 12.7 KB.
- `constants` (45 KB) and `weekly_retro_data` (25 KB), `context_pack` (25 KB), `grade_decisions` (18 KB), `week_box` (17 KB), `hashtag_status` (16 KB) are bigger than any tool the PRD named. `constants`, `hashtag_status` and `grade_decisions` are not in the daily chain but are one call away.
- PRD heavy hitters post-cap (`5e6ff40`): `signals` 14.0 KB (PRD 21.5 KB), `refresh_data` 8.7 KB (PRD 15.7), `optimize_lineup` 1.1 KB (PRD 15.6: capped well), `scoreboard`+`simulate_matchup` 11.8 KB (PRD 14.5). Still over 3 KB: signals, scoreboard, simulate_matchup (3.8 KB, just over).
- Static always-on is 14592 B (~3495 tok o200k) = AGENTS.md 8426 B + skill descriptions 4541 B + agent descriptions 1625 B. With the GM manual it is ~7400 tok. The PRD's +8-10k estimate looks high on this tokenizer (measured ~7400); it would only hold if hosts load full bodies or tokenizer inflation is large.
- Skill count is 19 (not 20) and there are 3 agents; `memory` and `trade-lab` skills exist in `agents/skills` and `.github/skills`.
- Context_pack is itself 25 KB (strategy_md, playbook_md, league_settings_md, journals, manager notes inline); it is the single largest unconditional start-of-session cost.
- Daily-update chain raw tool output is the dominant cost: well above the static prompt, and above the host baseline's likely share; delegation brings it to a fraction.
- Runtime: every tool ~1.7-3 s (process start-up dominates); `find_trades` 11 s, `next_week` 6 s, `refresh_data` 257 s (rate-limited imports), `snapshot` 19 s.
- `.github/skills` duplicates all of `agents/skills`; `.claude/` and `.agents/` carry only `setup-fbm`. Sync drift risk.
