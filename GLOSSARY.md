# FBM

FBM is an AI front office that helps one owner make sound, well-timed roster decisions in one Yahoo 9-cat H2H
fantasy basketball league.

## Layers

**Source data**:
Outside facts fetched and stored as-is (ESPN, Yahoo, Hashtag Basketball). No computation beyond normalizing.
_Avoid_: feeds, raw tools

**Deterministic tool**:
A pure computation over stored source data; the same inputs always give the same output. Never judges.
_Avoid_: engine call, smart tool

**GM**:
The AI host (Codex, Copilot, Claude) acting under FBM's rules: it judges, routes, decides what to recommend, and talks
to the owner. Skills and sub-agents are how the GM works, not separate layers.
_Avoid_: agent (unqualified), bot, assistant

**Freshness gate**:
The deterministic rule that re-syncs only the stale source data a tool depends on before the tool runs, so the GM
never decides when data is stale.
_Avoid_: auto-refresh, GM refresh

## Decisions

**Policy**:
The owner-approved decision thresholds that tools apply; it changes only through an approved policy proposal.
_Avoid_: rules, config, settings (unqualified)

**Policy verdict**:
An action label (claim/wait/pass, chase/hold) that a tool derives from policy alone and returns with its inputs.
It is a tool output, not a GM decision.
_Avoid_: recommendation, suggestion

**Owner**:
The human who owns the fantasy team and is the only one who acts in Yahoo.
_Avoid_: user, manager (reserved for other league teams)

**Proposal**:
A move that spends a scarce or hard-to-undo resource (a valuable drop, rolling waiver priority, a trade, a strategy or
policy change), so it needs independent review and the owner's verbatim decision before the owner acts.
_Avoid_: request, ticket, suggestion

**Recommendation**:
A move that is cheap to undo (lineup, IL slot moves, a stream whose drop is below the policy drop-cost threshold),
given with exact steps for the owner to act on without review or approval.
_Avoid_: suggestion, tip, Policy verdict

**Owner move**:
Any roster change the owner makes in Yahoo, whether it came from a proposal, a recommendation, or neither; every one
is verified against Yahoo and graded.
_Avoid_: transaction (Yahoo's record of it), executed proposal

**Move timing**:
The GM's judgment of when a move should happen (now, a later day, next week, or never), given as part of the
recommendation itself. Not a schedule or automation.
_Avoid_: automation, routine, trigger

**Move window**:
A move that is still possible but stops being possible at a fixed time (a tip-off lock, the transaction deadline, a
waiver clear, a proposal's expiry, the matchup's last day). Windows are facts; what to do about one is Move timing.
_Avoid_: alert, urgent item, deadline (unqualified)

**Next check-in**:
The earliest upcoming time when an open Move window could change, which the GM tells the owner at the end of a chat
so they know when to come back. A statement, not a reminder.
_Avoid_: reminder, schedule, ping
