---
name: stream-plan
description: Plan this week's streaming adds within the weekly add limit. Use for "Plan my streams for the week", "Who should I stream tomorrow?", "How many adds do I have left?".
---

# Stream plan

Run as the `gm` agent. Streams are add/drops: each one is a proposal.

1. `context_pack`; `schedule` for games left per team (ET days); in the Yahoo league `scoreboard` for what is
   already banked.
2. `weekly_plan` (chase/concede status, official odds) and `simulate_matchup` → swing categories and
   `if_opponent_streams`.
3. `streaming_plan(protect="<players never to drop>")` → the add/drop sequence by ET day, adds used vs the limit,
   expected-category gain, and rest-of-season cost. Hashtag mode: candidates are HT's waiver wire run with FBM's
   category multipliers; FBM chooses the plan by the policy (stream view, `policy.stream_min_gain`,
   `policy.add_reserve`) and logs the decision. `HT_UNHEALTHY` → no plan; report the fix. Honor owner rules from
   `memory/playbook.md`.
4. Sanity-check the first move with `evaluate_add_drop`. A move flagged `via_waivers` cannot happen before its
   `waivers_clear_et`: read its `waiver_verdict` (claim now spends rolling priority; wait = add free once he clears)
   and say how likely another team claims him first; never present it as a same-day add.
   Pending IL activations: model them with `evaluate_il_activation` before streaming around them.
5. Present the plan as a day-by-day table (local time + ET day). Propose only the next move(s) whose window is
   open: `create_proposal` (expiry before that tip-off) → `reviewer` → owner approval → `proposal_steps`
   → owner does it in Yahoo → `confirm_done`.
