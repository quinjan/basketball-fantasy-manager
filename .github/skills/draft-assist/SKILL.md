---
name: draft-assist
description: Draft help — build a draft board, compare draft slots or punt builds with mock drafts, and run a live draft (record every pick, recommend mine). Use for "draft board", "mock draft", "start the live draft assistant", or when the owner reports picks.
---

# Draft assistant

Run as the `gm` agent. Values are punt-aware G-scores from projections (pre-season = last season's per-game
stats); market expectations are ESPN ADP, or Yahoo ADP after `draft_adp_refresh` (CLI `fbm draft adp --refresh`;
Yahoo league only). In the Yahoo league, live picks sync with `draft_sync_yahoo` (CLI `fbm draft live` polls until
the draft ends); otherwise the owner reports picks.

## Prep
1. `draft_board` (with `punts` if a build is planned). Point out tiers, bargains (value_vs_adp ≫ 0), and reaches.
2. `draft_mock(my_slot, punts=...)` for 2–4 strategies (no punt, single punts, a double punt) to compare expected
   categories. Mock results are optimistic: the other teams draft by ADP and the model grades its own picks —
   use them for relative comparisons only.

## Live draft
3. `draft_start(teams, my_slot, rounds, punts)` — confirm league size, slot, rounds, and punts with the owner.
4. Yahoo league: call `draft_sync_yahoo` to pull new picks (and a recommendation when we're on the clock).
   Otherwise, for every pick in order (the owner says "Team 3 took X" or just "X"): `draft_pick(player)`. If a name is
   ambiguous, ask. If a pick was recorded wrong: `draft_undo`.
5. When it's the owner's pick: show the top 3–5 from `draft_recommend` with value, build score, tier, ADP, and
   P(available at my next pick); recommend one clearly and say why (category build, positions, tier cliff,
   availability). Mention position needs when flagged.
6. After the draft: summarize the roster's category profile (`my_category_g_sum`), suggest the season strategy
   (`season-strategy` skill), and journal the draft.
