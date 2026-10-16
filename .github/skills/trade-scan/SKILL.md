---
name: trade-scan
description: League-wide trade search (twice weekly). Find mutually beneficial trades using every team's category profile, evaluate incoming offers, and draft reviewed trade proposals for owner approval.
---

# Trade scan

Run as the `gm` agent. Trades are never sent without the owner's explicit approval.

1. `context_pack`, `league_profile`. For my team: categories to strengthen (swing/weak but not punted) and surplus
   categories (strong beyond need, or punted).
2. **Partner fit:** teams strong where I'm weak and weak where I'm strong. Read `memory/managers/` for tendencies
   (activity, what they overvalue, past trades).
3. **Candidates:** 1-for-1 and 2-for-1/2-for-2 packages. For each side, compare G-scores by category
   (`player_card`), rest-of-season value, positions/roster fit, injuries, and (late season) playoff-week schedule.
   A good trade improves my expected categories AND is defensible for them (fair by value, fits their build) —
   lopsided offers waste goodwill.
4. Keep at most 2 proposals per scan. `create_proposal` kind `trade` with payload ids + names, the pitch you'd send
   (`note`), and numbers for both sides → invoke the `reviewer` agent.
5. **Incoming offers** (when Yahoo data is live): evaluate the same way; recommend accept/counter/reject via a
   proposal.
6. Report in the next email (kind `approval` if any proposal awaits), journal, `uv run fbm sync-memory`.
