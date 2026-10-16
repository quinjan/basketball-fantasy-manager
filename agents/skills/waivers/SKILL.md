---
name: waivers
description: Free-agent pickups and rolling-waiver decisions. Use for "Best pickups for my build", "Should I drop X for Y?", "Is X worth burning waiver priority?", "Who's on waivers worth a claim?".
---

# Waivers

Run as the `gm` agent. Add/drops and claims are proposals — FBM never makes them; the owner does, after approving.

1. `context_pack`; `refresh_data` if data is stale; in the Yahoo league `snapshot` for the current waiver list and
   ownership.
2. Candidates: `free_agents(sort="value", punts=<approved punts>)` for rest-of-season (values are
   availability-adjusted for injuries); `free_agents(sort="week")` is this week only (see `stream-plan`).
3. "Drop X for Y?" → `evaluate_add_drop(add=Y, drop=X)`; check `player_card` for both (injury, minutes trend).
4. On waivers → `waiver_decision(player, drop)`: claim / wait / pass under rolling priority. Spend priority only on
   a clear multi-week upgrade, never on a one-day stream.
5. Respect owner rules in `memory/playbook.md` (e.g. "never drop X").
6. If clearly positive: `create_proposal` (payload ids + names, rationale with tool numbers, expiry before the
   waiver run or tip-off in ET) → `reviewer` agent → owner decision → `proposal_steps`. It returns the
   exact steps; the owner makes the claim in Yahoo and confirms with "done" → `confirm_done`.
