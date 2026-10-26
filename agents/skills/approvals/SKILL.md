---
name: approvals
description: Walk the owner through pending GM proposals and capture their decisions. Use when the owner says "what's pending", "approve P-12", "reject P-12", or opens the GM Office after an approval email.
---

# Approvals

1. `list_proposals(status="open")`. Expired proposals are already closed — mention them briefly if relevant.
2. For each `pending_approval` proposal (oldest first): show a compact card — title, the 2–4 key numbers, the
   reviewer's verdict and its first line, risks, expiry in owner-local time.
   - If the reviewer said DISAGREE/SPLIT, show the strongest alternative too.
   - `policy` proposals: show each setting's current → proposed value and the replay evidence (categories gained,
     graded weeks/decisions); say plainly that it changes how FBM decides from now on.
3. Ask the owner with the ask-user tool, choices: `Approve P-<id>`, `Reject P-<id>`, `Discuss P-<id>`.
   If the owner already wrote "approve P-12" in chat, that text is the decision — don't ask again.
4. `record_owner_decision(ref, approved, owner_quote=<the owner's exact words>)`. Never paraphrase or infer.
5. If approved: `proposal_steps(ref)` (re-validates first). Report the result:
   - `awaiting_owner` → show the manual steps exactly; the owner makes the move in Yahoo. When they say done,
     `confirm_done(ref, note)` (status `done`; demo league: this applies the change). The next snapshot verifies.
   - `done` (strategy / policy proposals) → confirm the approved strategy was recorded, or the policy settings were
     applied (new policy version in `policy_settings`).
   - `failed` → explain the re-validation problem and whether a revised proposal makes sense.
6. If rejected: ask (briefly, optional) why, and journal the reason (kind `owner`) — it trains future proposals.
7. Memory is saved locally; nothing else to sync.
