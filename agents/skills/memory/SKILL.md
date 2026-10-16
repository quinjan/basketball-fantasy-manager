---
name: memory
description: Read or update the GM's memory. Use for "What have we learned so far?", "Remember: manager Y overvalues blocks", "New rule: never drop X", "What did we decide last week?".
---

# Memory

Run as the `gm` agent. Memory lives in `memory/` (see AGENTS.md "Memory protocol").

1. Recall: `context_pack` (strategy, playbook, recent journal, manager notes); `grade_decisions` and
   `prediction_calibration` for what results say; older entries in `memory/journal/`.
2. "Remember about manager Y" → append the observation with date and source to `memory/managers/<team>.md`.
3. "New rule" → confirm the exact wording, add it under "Owner rules" in `memory/playbook.md`.
4. Strategy changes are not memory edits: use the `season-strategy` proposal flow (`memory/strategy.json` is
   written only by an approved `strategy` proposal via `proposal_steps`).
5. `journal_append` (kind `owner`) with the change (memory is saved locally).
