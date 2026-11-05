# What does a strong 9-cat H2H GM actually decide?

Research for Wayfinder ticket #2 (map #1, "spec for FBM v2").
Scope: one Yahoo Fantasy Basketball league, **H2H Categories, 9-cat** (FG%, FT%, 3PM, PTS, REB, AST, STL, BLK,
TO, lower TO wins), **redraft**, **rolling waiver priority**, **weekly add limit**, weekly matchups Mon–Sun, daily
lineups on US/Eastern game days. The draft is out of scope (FBM v2 starts post-draft) but sets the starting build
and week-1 waiver order.

All web content was read as untrusted reference data. Platform mechanics cite Yahoo Help (primary). Strategy claims
cite expert writing (secondary). Unverified points are flagged in §5.

---

## 1. Yahoo mechanics that shape every decision

| Mechanic | What Yahoo says | Why it matters for decisions | Source |
|---|---|---|---|
| Lineup lock | Default: a player can be moved until **the start of his own real-life game** (per-player lock, not a whole-day lock). Private leagues may choose weekly lineups instead. | "Now" means "before that player's tip-off". Late scratches can be fixed for players whose games haven't started. | [Yahoo SLN6775](https://help.yahoo.com/kb/SLN6775.html) |
| Transaction deadline | **Daily – Today**: adds/drops take effect immediately. **Daily – Tomorrow**: must be done by 11:59 PM PT, effective the next day. **Weekly**: 11:59 PM PT before the weekly roster day. | Decides whether a same-day stream is even possible. The spec must read this league setting, not assume it. | [Yahoo SLN6775](https://help.yahoo.com/kb/SLN6775.html) |
| Waivers after a drop | Standard waivers: a dropped player is frozen for a set period (**default 2 days**, 0–7 configurable). The period **starts the following day** (dropped Friday → on waivers Sat + Sun). If unclaimed, he becomes a Free Agent anyone can add immediately. The dropping manager can't claim him back until his waiver period ends. Add-and-drop on the same day leaves him a Free Agent. | A waiver claim is never a "today" move. With 2-day waivers, a Monday drop is claimable at the earliest ~Thursday. Free Agents are the only same-day option. | [Yahoo SLN6811](https://help.yahoo.com/kb/fantasy-basketball/overview-waivers-free-agents-yahoo-fantasy-sln6811.html) |
| Continuous waivers (alt. mode) | All unclaimed players are always on waivers. A claim starts a clock (default 2 days) for counter-claims. | If the league uses this mode, *every* pickup costs priority and has lag. The spec must read the mode. | [Yahoo SLN6811](https://help.yahoo.com/kb/fantasy-basketball/overview-waivers-free-agents-yahoo-fantasy-sln6811.html) |
| Rolling priority | "Continual Rolling List": initial order from the draft (last draft slot = #1). **A successful claim drops you to the bottom**. The list does not reset weekly (unlike reverse-standings priority). | Priority is a scarce, non-renewing option. Every claim spends it. | [Yahoo SLN6811](https://help.yahoo.com/kb/fantasy-basketball/overview-waivers-free-agents-yahoo-fantasy-sln6811.html), [Yahoo SLN8825](https://help.yahoo.com/kb/fantasy-basketball/weekly-waiver-options-sln8825.html) |
| Conditional claims | Yahoo documents stacking several claims (add C drop A, add D drop A, add D drop B) so one voided claim doesn't wreck the plan. | Good practice is a ranked claim *list*, not one claim. | [Yahoo SLN6811](https://help.yahoo.com/kb/fantasy-basketball/overview-waivers-free-agents-yahoo-fantasy-sln6811.html) |
| Acquisition limits | Three commissioner settings: max acquisitions for the season, max trades for the season, **max acquisitions per week (H2H only)**. Acquisition limits cover **players added through free agency or waivers**, and apply **including the playoffs**. | Waiver claims use weekly adds too. A season trade cap can block late trades. | [Yahoo SLN6981](https://help.yahoo.com/kb/fantasy-basketball/weekly-transaction-limit-sln6981.html) |
| IL eligibility (basketball) | Yahoo's help lists only an **IL** slot for basketball: eligible = **Out (O)** or **Out For Season (OFS)**; ineligible = Probable, Questionable, Doubtful. "Out" stays IL-eligible until the next morning (after nightly processing). "Out 3+ games" stays eligible until he returns. Designations usually appear after Yahoo's overnight update. | Most IL moves become possible the morning after news. | [Yahoo SLN26507](https://help.yahoo.com/kb/fantasy-basketball/injured-list-yahoo-fantasy-basketball-sln26507.html) |
| IL stash / reactivation gate | Players on IL earn no stats. A player can stay on IL all season, but **once he is active in real life you can't complete any add until you activate (or drop) him**. Bench/active swaps and drops are always allowed. | A healthy player sitting in IL blocks all adds. This is a hidden constraint on streaming. | [Yahoo SLN26507](https://help.yahoo.com/kb/fantasy-basketball/injured-list-yahoo-fantasy-basketball-sln26507.html) |
| Roster slots post-draft | Commissioners can add/edit roster positions (incl. IL slots) after the draft, with restrictions. | Slot counts (bench, IL, IL+) must come from league settings. | [Yahoo SLN6941](https://help.yahoo.com/kb/fantasy-basketball/add-edit-remove-roster-positions-post-draft-sln6941.html) |
| Trade review | Public/Private: overturned if **1/3 of managers (rounded up)** vote against, unless the commissioner enabled **Commissioner Trade Review**. Public Prize leagues: 2-day protest window, then a League Office ruling. | Trades have a built-in multi-day lag before players change rosters. | [Yahoo SLN6613](https://help.yahoo.com/kb/fantasy-basketball/protest-veto-trade-yahoo-fantasy-sln6613.html) |
| Playoffs | Bracket sizes 4 / 6 / 8 teams, played in the final weeks before the NBA regular season ends. Per the help article, the 4-team format runs a shorter bracket than 6-/8-team formats. Exact weeks are a league setting. | Playoff weeks and bracket size come from settings and drive late-season planning. | [Yahoo SLN6776](https://help.yahoo.com/kb/SLN6776.html) |
| All-Star week | Yahoo doesn't adjust the weekly acquisition limit for the All-Star break week. | That week has fewer games and the same add budget, so planning changes. | Yahoo SLN14221 (All-Star week scoring) |

**Two consequences that drive the whole catalog:**

1. **Games played is the main weekly lever.** Seven of nine categories are counting stats that grow with games
   played. FG% and FT% are team makes/attempts, so volume moves them too. Hashtag Basketball's streaming definition:
   "The more players you have playing, the more stats you collect, increasing your chances of winning your
   match-up." ([HT punting guide](https://hashtagbasketball.com/introduction-punting-fantasy-basketball))
2. **Every add costs one or two scarce resources.** Each add uses one weekly acquisition. A claim also spends
   rolling priority. Each add also needs a drop, which costs a roster asset. Good GMs budget all three.

---

## 2. The decision catalog

Move timing uses the map's vocabulary: **now** (today, before the relevant lock), **later this week**, **next week**,
**never** (don't act).

### D1. Daily lineup (start/sit, fill every active slot)
- **Frequency:** Every game day (≈160+ times per season). This is the most frequent decision.
- **Signals:** Which rostered players have a game today (schedule grid). Active slots vs players with games. Positional
  eligibility (PG/SG/G/SF/PF/F/C/UTIL). Injury status (O/GTD/Q). In-week category state (D6).
- **Move timing:** **Now.** Each player locks at his own tip-off ([SLN6775](https://help.yahoo.com/kb/SLN6775.html)),
  so the deadline is the earliest tip-off among the players affected.
- **Good practice:** Start every healthy player with a game before anyone without one. Solve position eligibility
  as a matching problem, not by eye, because a flex juggle often frees a slot. Set lineups for several days ahead,
  then re-check close to the first tip-off.
- **Common mistakes:** Leaving a playing player on the bench while an active slot is empty or holds a non-playing
  player. Not re-checking after late injury news. Assuming one lock for the whole day.
- **Info needed:** Roster + eligibility, today's ET schedule and tip times, injury designations, league roster
  positions, and the in-week category state (only to break ties).

### D2. Late-scratch / game-time-decision swap
- **Frequency:** Several times a week during the season. Peaks on busy nights and around back-to-backs.
- **Signals:** Pre-game injury report updates (Q/GTD → Out), rest days announced near tip, starting-lineup news.
- **Move timing:** **Now**, and only until the affected player's tip-off. After the lock, nothing can be done.
- **Good practice:** Before the earliest tip-off, keep a fallback for each GTD starter: a bench player with a later
  or same game who can swap in. Prefer GTDs in later games, since their status resolves before lock.
- **Common mistakes:** Starting an early-game GTD with no fallback. Missing a rest day announced shortly before tip.
- **Info needed:** Injury feed with timestamps, tip times by player, bench players with games that day.

### D3. IL (and IL+ where the league has it) placement / activation
- **Frequency:** Reactive. Weekly or more in injury-heavy stretches.
- **Signals:** O/OFS designation appears (IL-eligible after Yahoo's overnight update). A stashed player is cleared
  or returns in real life, which triggers the **reactivation gate**: no adds until he is activated or dropped
  ([SLN26507](https://help.yahoo.com/kb/fantasy-basketball/injured-list-yahoo-fantasy-basketball-sln26507.html)).
- **Move timing:** **Now** to free the slot as soon as he's eligible, because that slot can be streamed today.
  **Now** to activate when a stashed player returns, because adds are blocked until you do.
- **Good practice:** Use IL slots for long absences, not a one-game Out. Before planning streams, check whether an
  IL player has gone active. Know the roster count after activation: activating needs an open active/bench slot, so
  plan which drop goes with it.
- **Common mistakes:** A waiver claim fails because a recovered IL player was never activated. A long-term injury
  sits on the bench while the IL slot is empty. A player returns and gets activated with no drop planned.
- **Info needed:** Designations (O/OFS/GTD/Q/D), expected return timelines, number of IL/IL+ slots (from league
  settings), current roster count.

### D4. Streaming add/drop (games-played maximization)
- **Frequency:** Several times a week, capped by the weekly acquisition limit
  ([SLN6981](https://help.yahoo.com/kb/fantasy-basketball/weekly-transaction-limit-sln6981.html)).
- **Signals:** Open active-slot-days in the rest of the week. Free agents whose teams play more games, or on
  **light nights** when few teams play. The [HT schedule grid](https://hashtagbasketball.com/advanced-nba-schedule-grid)
  shows games per day, e.g. 15 Monday vs 0 Tuesday vs 2 Thursday in one sample week. Which categories are live (D6).
  How safe the drop is.
- **Move timing:** **Now** under "Daily – Today" transactions (the add counts if his game hasn't started).
  **Later this week** under "Daily – Tomorrow", which must be done by 11:59 PM PT the night before
  ([SLN6775](https://help.yahoo.com/kb/SLN6775.html)). Free agents only. A waiver player can't be streamed
  today.
- **Good practice:** Count *net* games added: a streamer's games on days you'd otherwise have an empty active slot.
  Raw team games don't count. Target 4-game weeks and light nights. Use only designated "streaming slots", never drop
  a keeper ("Just don't do anything silly like drop a player who's worth keeping" —
  [HT](https://hashtagbasketball.com/introduction-punting-fantasy-basketball)). Match the streamer to live
  categories (STL/BLK/3PM specialists). Avoid low-FG% or high-TO volume when FG%/TO are live. Punting FG%/FT%/TO makes
  streaming much easier (same source).
- **Common mistakes:** Streaming for raw games when lineup slots are already full that day (zero net gain).
  Spending the weekly limit by Wednesday with no reserve. Streaming a bad shooter with a narrow FG% lead. Dropping a
  player who returns from injury next week.
- **Info needed:** Per-day schedule for roster and free agents, current daily slot utilization, adds used/remaining
  this week, transaction-deadline setting, per-category matchup state, free-agent per-game projections.

### D5. Weekly add-budget plan
- **Frequency:** Weekly (plan Monday), revised daily.
- **Signals:** Weekly limit and adds used. Days with roster surplus/deficit. Expected injury/claim needs. Matchup
  closeness.
- **Move timing:** Plan **now** (start of week). Execution spreads **later this week**. Unused adds expire with the
  week. Reset day is assumed Monday (see §5).
- **Good practice:** Decide how many adds go to streaming vs a reserve for injuries or claims. Spend early when one
  add covers several remaining games, and keep 1–2 for Thu–Sun injury news. Remember that **successful waiver claims
  count as acquisitions** ([SLN6981](https://help.yahoo.com/kb/fantasy-basketball/weekly-transaction-limit-sln6981.html)),
  so a pending claim needs budget held for it. The limit still applies in the playoffs.
- **Common mistakes:** Hitting the cap early. Not reserving an add for a pending claim. Leaving adds unused in a close
  week.
- **Info needed:** Limit and usage (from Yahoo), remaining schedule, pending claims, matchup state.

### D6. In-week matchup triage (chase vs protect)
- **Frequency:** Weekly plan Monday, re-checked daily. Most decisive Thu–Sun.
- **Signals:** Live category totals vs the opponent, games remaining for each side, and volatility. Ratio categories
  (FG%, FT%) have known attempts. Each category is **won / lost / live**. "You don't get bonus points for winning 8-1
  or 9-0; you just need to win more categories than your opponent" (Hashtag Basketball scouting guide; via search
  snippet, URL not resolved).
- **Move timing:** **Now** (daily lineup/stream choices). Late in the week this can mean benching low-efficiency
  players to protect a FG%/FT%/TO lead, or streaming into a counting category that is still live.
- **Good practice:** Target the 5 categories most likely to swing the week. Stop spending on categories that are
  safely won or lost. When protecting a ratio lead late, weigh lost counting stats against the ratio risk using
  numbers.
- **Common mistakes:** Chasing a category that is already lost. Streaming volume into a narrow FG% lead. Benching
  for TO when counting categories are still live.
- **Info needed:** Live scoreboard (category totals + FGM/FGA/FTM/FTA), both rosters' remaining games, per-game
  projections, a matchup simulation/win probability per category.

### D7. Waiver claim (spend rolling priority?)
- **Frequency:** A few times to a dozen per season of *meaningful* claims. Triggered by drops of rosterable players
  (often after another team's injury purge or release of a real-NBA role winner).
- **Signals:** A player worth more than your worst ROS keeper is on waivers. Your priority rank, and who sits above
  you. The waiver clear date.
- **Move timing:** Submit **now** (before the claim window closes). The player arrives **later this week** (after the
  waiver period, which starts the day after the drop:
  [SLN6811](https://help.yahoo.com/kb/fantasy-basketball/overview-waivers-free-agents-yahoo-fantasy-sln6811.html)).
  Choose **never** if a near-equivalent Free Agent is available now.
- **Good practice:** Treat rolling priority as an option on the *next* big injury. Spend it on ROS-level value, not
  a streamer. File conditional claims (ranked adds, ranked drops). Keep a weekly add free for when the claim clears.
- **Common mistakes:** Burning #1 priority on a short-term need. Claiming when the player is actually a Free Agent
  (no priority needed). Dropping a better player than needed.
- **Info needed:** Waiver list with clear times, own and others' priority, ROS projections for target vs drop
  candidates, weekly adds remaining, league waiver mode and period.

### D8. Injury-news replacement race (handcuff / role-change pickup)
- **Frequency:** Ad hoc, roughly weekly league-wide. Often happens in the evening ET or overnight.
- **Signals:** Starter ruled out for an extended period, or a trade/rotation change. The backup's minutes jump. The
  backup is still a **Free Agent** (not on waivers).
- **Move timing:** **Now.** First to add a Free Agent wins him, and no priority is involved. If he's on waivers,
  this becomes a D7 claim.
- **Good practice:** Act on *confirmed* role change (starting/minutes reporting), not rumor. Size the move: a
  stream for a short absence, a ROS add for a long one.
- **Common mistakes:** Overpaying (dropping a keeper) for a 1–2 game bump. Being late because the news was checked
  once a day.
- **Info needed:** Injury news with timestamps, depth charts / minutes trend, free-agent status, drop candidates.

### D9. ROS add/drop (upgrade the bottom of the roster)
- **Frequency:** Weekly review. Execute a few times a month.
- **Signals:** Multi-game minutes/usage trend (not one box score). Fit with the category build. A sustained
  injury-created role. A rostered player losing his role.
- **Move timing:** Usually **later this week** or **next week**. Act sooner if the target is a Free Agent likely to
  be taken.
- **Good practice:** Rank the bottom 2–3 roster spots against the best available every week using per-game value
  *for your build* (category-weighted). Don't confuse a hot stretch with a role change. Keep the roster split
  between "core", "ROS fringe" and "streaming slots".
- **Common mistakes:** Panic-dropping a proven player after a slump (sell-low). Holding a drafted player out of
  sunk cost. Ignoring build fit.
- **Info needed:** ROS projections, recent minutes/usage trends, build/strategy weights, schedule ahead.

### D10. Trades (propose, evaluate incoming, buy-low/sell-high, category fit)
- **Frequency:** A few per season. Clusters early-to-mid season (small-sample mispricing) and before the trade
  deadline. RotoWire runs a weekly buy-low/sell-high column
  ([RotoWire](https://www.rotowire.com/basketball/article/fantasy-basketball-trade-advice-buy-low-sell-high-hold-145233)).
- **Signals:** Your category surplus vs another team's need (league-wide category profiles). Performance far from
  projection. Real-NBA trades and injuries. Incoming offers.
- **Move timing:** **Later this week / next week.** Negotiation plus the review window (1/3 veto vote or commissioner
  review; [SLN6613](https://help.yahoo.com/kb/fantasy-basketball/protest-veto-trade-yahoo-fantasy-sln6613.html)) means
  a trade can't help *today*. Incoming offers have their own response window. **Never** after the deadline or season
  trade cap ([SLN6981](https://help.yahoo.com/kb/fantasy-basketball/weekly-transaction-limit-sln6981.html)).
- **Good practice:** Evaluate with per-category value deltas *for both teams* (Basketball Monster exposes per-category
  value columns and punt-adjusted value: [BBM rankings](https://basketballmonster.com/playerrankings.aspx)). Propose
  trades that help both sides' builds, since lopsided trades get vetoed or ignored. Account for roster-spot effects
  (2-for-1 frees a slot for streaming). Count games in the next weeks and in playoff weeks.
- **Common mistakes:** Trading on name value or one hot week. Ignoring the veto window and deadline. Ignoring the
  extra drop a 2-for-1 forces on the other side.
- **Info needed:** All teams' rosters and category profiles, ROS projections, playoff-week schedules, league trade
  settings (review mode, deadline, trade cap), manager tendencies.

### D11. Category build / punt strategy
- **Frequency:** Set after the draft, then reviewed every few weeks or after major roster changes. "The idea is to
  punt the most obvious weakness on your roster"
  ([HT](https://hashtagbasketball.com/introduction-punting-fantasy-basketball)).
- **Signals:** Team category ranks vs the league over a meaningful sample. Which categories are cheap to buy on
  waivers. Major injuries/trades that change the roster profile.
- **Move timing:** **Next week** (a strategic shift acts through D4/D7/D9/D10). **Never** react to one week's result.
- **Good practice:** Punt pairings that work: FG%+REB, AST+STL, BLK+FG%, PTS+FT%, or the FG%/FT%/TO triple punt.
  Avoid quadruple punts ("too large of a margin for error").
  ([HT](https://hashtagbasketball.com/introduction-punting-fantasy-basketball)). Punting is an H2H tactic, not a
  roto one (same source). Still roster positive contributors in a punted category when they help elsewhere.
- **Common mistakes:** Switching builds week to week. Punting a category you're already decent in. Punting too many.
- **Info needed:** League-wide category profile (team totals/ranks), per-category player values with punt
  weighting, owner-approved strategy.

### D12. Opponent scouting / weekly preview
- **Frequency:** Weekly (Monday, or Sunday night for next week).
- **Signals:** Next opponent's roster, games this week, injuries, category strengths, and streaming habits.
- **Move timing:** **Now** (feeds D5/D6 planning at week start).
- **Good practice:** Project both sides' weekly totals with games counted. Pick the target categories. Note the
  opponent's add usage and streaming habits.
- **Info needed:** Opponent roster/schedule, projections, transaction history.

### D13. Playoff positioning and playoff-schedule planning
- **Frequency:** Builds over the last ~6–8 regular-season weeks. Includes the final pre-deadline trade window.
- **Signals:** Standings vs the playoff line (bracket size and weeks from settings,
  [SLN6776](https://help.yahoo.com/kb/SLN6776.html)). Each rostered player's **games in the specific playoff
  weeks**. Real-NBA tank/rest risk for players on eliminated teams late in the season. Seeding/tiebreakers.
- **Move timing:** **Next week** and beyond. Trades must clear before the deadline. Swaps toward heavy playoff
  schedules happen in the final regular-season weeks. During the playoffs, the weekly cycle of D1–D6 repeats with
  higher stakes, and acquisition limits still apply
  ([SLN6981](https://help.yahoo.com/kb/fantasy-basketball/weekly-transaction-limit-sln6981.html)).
- **Good practice:** Audit playoff-week game counts weeks early. Trade or swap for players on teams with good
  playoff-week schedules and real-NBA incentive to keep playing. Bank waiver priority for playoff-time injuries.
- **Common mistakes:** A roster built for the regular-season average but with light playoff weeks. Rostering
  veterans on tanking teams. Relaxing once clinched while the seed still matters.
- **Info needed:** League playoff settings, NBA schedule for the playoff weeks, NBA standings (tank/rest risk),
  fantasy standings and tiebreakers.

### D14. Hold vs drop/sell an injured or slumping star
- **Frequency:** A few times per season.
- **Signals:** Injury length (days vs weeks vs season), IL eligibility, roster crunch, standings urgency, playoff
  timing.
- **Move timing:** Usually **never** drop a true star with an IL slot available, because stashing on IL is free.
  A sell-trade is **later this week / next week** (D10). Dropping is **now** only if the player is OFS or there's a
  hard roster crunch.
- **Good practice:** Use IL first. If a sale makes sense, sell while the market still values him. Compare the
  return date with the playoff weeks.
- **Info needed:** Injury timeline, IL capacity, playoff calendar, trade market.

### D0. "Do nothing" (explicit no-move)
A strong GM often decides **never**: no stream that adds zero net games, no priority burn for a marginal gain, no
trade off one hot week. The catalog should treat "no move" as a recommended outcome with reasons, not a gap.

---

## 3. Summary table

| # | Decision | Frequency | Key signals | Move timing | Info needed |
|---|---|---|---|---|---|
| D1 | Daily lineup | Every game day | Games today, slots/eligibility, injuries | Now (per-player tip lock) | Schedule + tip times, roster, injuries |
| D2 | Late-scratch swap | Several/week | Q/GTD→Out, rest news | Now (before that tip) | Timestamped injury feed, bench with games |
| D3 | IL/IL+ moves | Reactive, ~weekly | O/OFS posted; stashed player active | Now | Designations, IL slots, roster count |
| D4 | Streaming add/drop | Several/week (≤ add cap) | Net open slot-days, 4-game weeks, light nights, live cats | Now (Daily-Today) / tonight for tomorrow | Day-by-day schedule, adds left, matchup state, FA projections |
| D5 | Weekly add budget | Weekly + daily revise | Limit/used, schedule gaps, pending claims | Now (plan) → later this week | Add limit/usage, schedule, claims |
| D6 | In-week triage | Daily, key Thu–Sun | Category margins, games left, ratio attempts | Now | Live scoreboard incl. makes/attempts, sim |
| D7 | Waiver claim | Few–dozen/season | Rosterable player dropped; priority rank | Submit now → arrives later this week | Waiver list/clear times, priority, ROS value |
| D8 | Injury-replacement race | Ad hoc | Starter out, backup is FA | Now | News feed, depth/minutes, FA status |
| D9 | ROS add/drop | Weekly review | Sustained minutes/role change, build fit | Later this week / next week | ROS projections, trends, strategy |
| D10 | Trades | Few/season; pre-deadline | Category surplus/need fit, mispricing, offers | Later this week / next week; never after deadline | All teams' profiles, ROS + playoff schedule, trade rules |
| D11 | Build / punt | Post-draft + every few weeks | League category ranks, roster profile | Next week; never on one week | League category profile, punt values |
| D12 | Opponent preview | Weekly | Opponent roster, games, habits | Now (week start) | Opponent roster/schedule, transactions |
| D13 | Playoff planning | Last 6–8 weeks + deadline | Playoff-week games, tank/rest risk, seeding | Next week+ | Playoff settings, NBA schedule/standings |
| D14 | Hold/sell injured star | Few/season | Injury length, IL room, urgency | Never drop (IL) / trade later | Timeline, IL capacity, market |
| D0 | No move | Constant | Net value ≤ cost | Never | Same as the move it rejects |

---

## 4. Cross-cutting principles for the spec

1. **Value every move as net games × category value, minus resource cost.** Resources are the weekly add, rolling
   priority, and the dropped player's ROS value. "Games" means games that actually fill an active slot.
2. **Ratio categories are volume-weighted** (team makes/attempts). Streamers and benching decisions move FG%/FT%
   in proportion to attempts, so in-week decisions need attempts data, not just percentages.
3. **Move timing comes from platform mechanics, not opinion.** Per-player tip-off locks, the transaction-deadline
   setting, waiver periods starting the next day, overnight IL designation updates, and trade review windows decide
   whether a move is "now", "later this week" or "next week". The spec should derive timing from league settings
   plus the schedule.
4. **Two time-scales, one model.** Season build (D11) and in-week triage (D6) are the same category-marginal-value
   math over different horizons.
5. **"Now" decisions dominate by count; ROS/trade decisions dominate by impact.** A chat-only GM with no automation
   must make "now" decisions easy to request at the right moment, e.g. a lineup check before the first tip in the
   owner's time zone. This is a design tension for the spec.

---

## 5. Verification gaps and open points

1. **IL+ for basketball is not in Yahoo's current help.** SLN26507 lists only IL (O/OFS) for basketball and IR+ for
   hockey. Community sources describe a basketball IL+ slot that accepts broader tags (e.g. GTD/DTD). The spec should
   read slot types and eligibility from the league's Yahoo settings/roster data, not hard-code them.
2. **Weekly add counter reset day** isn't stated in Yahoo's help. Monday (the start of the H2H week) is assumed.
   Check it against real league transaction data.
3. **Weekly waiver variants** (e.g. "Game Time – Tuesday" in SLN8825) appear to be football/weekly-lineup oriented.
   Their behavior in a daily-lineup NBA league wasn't confirmed. Read the league's actual waiver mode.
4. **Trade deadline date** is a league setting. No basketball-specific Yahoo article was fetched. Read it from
   settings.
5. Search access was limited during research. FantasyPros/RotoBaller/The Athletic weren't reached. Expert claims
   rely on Hashtag Basketball, Basketball Monster and RotoWire. One HT quote (scouting guide) came from a search
   snippet with no resolved URL.

---

## 6. Sources

**Yahoo Help (primary, mechanics)**
- SLN6775 Transaction and lineup deadlines — https://help.yahoo.com/kb/SLN6775.html
- SLN6811 Waivers and Free Agents — https://help.yahoo.com/kb/fantasy-basketball/overview-waivers-free-agents-yahoo-fantasy-sln6811.html
- SLN8825 Waiver options / priority types — https://help.yahoo.com/kb/fantasy-basketball/weekly-waiver-options-sln8825.html
- SLN6981 Transaction limits — https://help.yahoo.com/kb/fantasy-basketball/weekly-transaction-limit-sln6981.html
- SLN26507 Injured and inactive players — https://help.yahoo.com/kb/fantasy-basketball/injured-list-yahoo-fantasy-basketball-sln26507.html
- SLN6941 Roster positions post-draft — https://help.yahoo.com/kb/fantasy-basketball/add-edit-remove-roster-positions-post-draft-sln6941.html
- SLN6613 Trade protest / veto — https://help.yahoo.com/kb/fantasy-basketball/protest-veto-trade-yahoo-fantasy-sln6613.html
- SLN6776 Playoff settings — https://help.yahoo.com/kb/SLN6776.html
- SLN14221 All-Star week scoring — https://help.yahoo.com/kb/SLN14221.html

**Strategy (secondary)**
- Hashtag Basketball, An Introduction to Punting in Fantasy Basketball — https://hashtagbasketball.com/introduction-punting-fantasy-basketball
- Hashtag Basketball, Advanced NBA schedule grid — https://hashtagbasketball.com/advanced-nba-schedule-grid
- Basketball Monster, Player rankings (per-category and punt values) — https://basketballmonster.com/playerrankings.aspx
- RotoWire, Buy Low / Sell High weekly trade advice — https://www.rotowire.com/basketball/article/fantasy-basketball-trade-advice-buy-low-sell-high-hold-145233
