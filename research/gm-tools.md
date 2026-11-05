# What tools and data top 9-cat H2H managers use, per decision (vs FBM's tool catalog)

Research for [#3](https://github.com/quinjan/basketball-fantasy-manager/issues/3) on the map
[#1 Wayfinder: spec for FBM v2](https://github.com/quinjan/basketball-fantasy-manager/issues/1).
It builds on the decision catalog from [#2](https://github.com/quinjan/basketball-fantasy-manager/issues/2)
([research/gm-decisions.md](https://github.com/quinjan/basketball-fantasy-manager/blob/research/gm-decisions/research/gm-decisions.md)),
which uses the IDs D0–D14 cited below.

**Scope:** Yahoo H2H 9-cat (FG%, FT%, 3PM, PTS, REB, AST, STL, BLK, TO), redraft, rolling waivers, weekly add limit.
Draft tools (mock drafts, ADP, draft trackers) are out of scope because FBM works in-season.

**Method and caveats:**
- I read products' own public pages and help copy, the Rosenof G-score paper (full text), and Yahoo help.
- Reddit could not be fetched directly (bot check). Reddit claims come from cached search snippets and are marked
  *(community)*.
- Some Yahoo help articles were confirmed only by search snippet. They are marked *(snippet)*.
- Hashtag Basketball (HT) Premium content is personal-use only. This file describes what HT's tools do, citing HT's
  own public pages. It copies no HT data.
- FBM facts come from `docs/tools.md`, `docs/analytics.md` and `uv run fbm tool --list` / `--doc` on `main`
  (2026-11-05). The CLI lists **61 tools**; `docs/tools.md` says 60 (a stale count; every listed tool is documented).

---

## 1. Standard computations

| Computation | What it is | Inputs | Who ships it | Det. / judgment |
|---|---|---|---|---|
| **Z-score** | Per category: (player − pool mean) / pool SD, summed. FG%/FT% use volume-weighted impact, (pct − league pct) × attempts, before standardizing. TO counts negative. | Per-game (or total) projections, a player pool, league category list | BBM "Value" column; HT rankings "TOTAL"; most sites [1][5][9] | Deterministic. **Choosing the pool** (all players vs rotation players, e.g. > 28 MPG) is a modeling choice that moves outliers like elite BLK [10 *(community)*]. Critics also argue naive Z mis-weights STL vs BLK [27 *(community)*]. |
| **G-score** | Z-score with week-to-week variance added to the denominator: (μp − μ) / √(σ² + κτ²), κ = 2N/(2N−1). Z is the τ = 0 case. It down-weights volatile categories (STL, also BLK) relative to stable ones (AST) for H2H. In simulated 12-team H2H seasons a lone G-score drafter beat a Z-score field (21.4% "each category", 32.5% "most categories" vs 8.3% baseline). | Same as Z, plus per-player week-to-week SD per category | Rosenof, arXiv:2307.02188 [1]. No commercial site documents using it. | Deterministic. The paper flags its own limits: no role/injury drift in-season, no position scarcity, and "expected categories won" only approximates "win the week" [1 §4.1]. |
| **Punt-adjusted rankings** | Re-sum Z/G over the non-punted categories. HT's rankings/projections pages let you toggle and weight each category ("Categories & Multipliers") and re-sort by TOTAL. BBM has punt toggles. | Category list and punt set | HT [2][3], BBM [11 *(community)*] | The re-sum is deterministic. **Which** categories to punt, and **when** to commit, is judgment: the paper argues punting is adaptive and a static list can't do it optimally [1 §4.2]. |
| **Custom / alternative value models** | BBM offers "DURANT values" as an alternative to classic z-scores (method not public). HT has ranking "types" (STANDARD / H2H / MINUS 1, not documented publicly). | Projections | BBM [6], HT [2] | Deterministic but proprietary. |
| **Time-window value** | The same value metric over 1 day / 1 week / 2 weeks / season (BBM) or blended trailing windows BLEND7/14/30 plus a games-played penalty (HT projections). | Game logs, projections | BBM home page [5], HT projections [3] | Deterministic. Which window to trust is judgment. |
| **Games-played / schedule grid** | Day-by-day NBA team schedule per fantasy week with game counts, league-wide games per day (light vs heavy nights) and a pace toggle, for current and named future weeks. | NBA schedule, fantasy week boundaries | HT free Advanced Schedule Grid + Premium grid [4][7]; Yahoo matchup page "games played / remaining" [12 *(community, first-party Yahoo post, 2018)*] | Deterministic. |
| **Weekly matchup projection** | Per-game projections × remaining games (started, within slot limits) + banked stats → projected weekly totals per category for both teams; flags close categories. | Both rosters, projections, schedule, live scoreboard, injuries | HT Premium Matchup Planner [7][13 *(community)*]; BBM Matchup Monster / SmartTool [6] | Deterministic once inputs are fixed. Start/sit and injury assumptions are inputs that need judgment. |
| **Category win probability** | P(win) per category from projected totals and spread: a Normal approximation (paper) or Monte Carlo. | Projected totals, per-category variance | Rosenof models weekly totals as Normal [1 §3.3]. **No consumer product documents a true category win probability.** HT shows a share formula, not a probability (FBM `docs/analytics.md`). | Deterministic. Variance calibration is measurable. |
| **Trade category delta** | Per-category value in/out for both sides. BBM also re-projects **league standings and every matchup** after the trade, and suggests "balancing" pieces and fair trades. | Both rosters, projections, league categories | HT Trade Analyzer [8], BBM Trade Analysis / Trade Monster [6][14 *(community)*], FantasyPros My Playbook [15] | The delta is deterministic. **Acceptance likelihood**, fairness framing and timing are judgment. |
| **Auction / replacement value** | Z share of the top-N rostered value × budget; value over replacement (VOR) for tight position leagues. | Z-scores, league size, roster spots | Community method [16 *(community)*]; VOR discussed in [1 §4.1.3] | Deterministic. Mostly draft-time; replacement level matters in-season for drops. |
| **Standings-gain points (SGP)** | A roto-baseball concept. **No 9-cat basketball source found**, so it's not standard here. | — | — | — |

---

## 2. Per decision: what top managers and products use

Legend: **Det.** = pure computation from data. **Judg.** = needs judgment (estimation, opponent psychology, timing).

### Daily lineup and late scratches (D1, D2)
- **Tools:** Yahoo matchup and roster pages (games today/remaining, injury tags) [12][17 *(snippet)*]; FantasyPros
  My Playbook "optimal lineups" after a league sync [15]; BBM SmartTool [6]; recent-form tiers on HT pages [2][3];
  timestamped injury news (Rotoworld/NBC, BBM news ticker) [5][18].
- **Computes:** who plays today, slot legality, max games started; recent-window value to break ties.
- **Inputs:** NBA schedule and tip times, eligibility, roster slots, injury designations, live category margins.
- **Det.:** schedule, legality, max-starts optimization, tip-off lock.
  **Judg.:** sitting a hot/cold player, boom/bust versus a live margin, acting on a scratch not yet in any feed.

### Streaming (D4) and weekly add budget (D5)
- **Tools:** HT free Waiver Wire (players under X% rostered who play today, questionable filter) [9]; HT/Yahoo
  schedule grids [4][12]; BBM 1-day/1-week value [5]; HT Premium Matchup Planner to stream into close categories
  [7][13 *(community)*]; RotoBaller/Rotowire waiver/streamer columns [19][20].
- **Computes:** games left × short-horizon value, roster-% filter, open slot-days, light nights.
- **Inputs:** add limit and adds used, schedule, league-specific availability, live matchup, injuries.
- **Det.:** game counts, short-horizon value, add arithmetic, net slot-days filled.
  **Judg.:** which category to chase, spend an add now or save it, real role change vs a pure games play.

### In-week category triage (D6) and opponent preview (D12)
- **Tools:** Yahoo live scoreboard; HT Matchup Planner; BBM Matchup Monster [6][7]; HT League Scouting Report
  (premium) [7][13 *(community)*].
- **Computes:** projected end-of-week totals, close categories, opponent games remaining.
- **Inputs:** both rosters, makes/attempts (FG%/FT% are volume-weighted), schedule, opponent habits.
- **Det.:** projection and (where offered) odds.
  **Judg.:** chase vs concede thresholds, predicting the opponent's streaming.

### Waiver claims under rolling priority (D7, D8)
- **Tools:** the same discovery tools as streaming over a 1–2 week+ horizon [3][5][9]; FantasyPros/Rotowire waiver
  columns that say who's worth a claim [19][20]; Rotoworld news on role changes [18]; Yahoo Research tab
  (transaction trends, roster rates) [21].
- **Computes:** medium-horizon value vs your worst active player (replacement).
- **Inputs:** priority position, waiver clear times, roster hole, role/news signals.
- **Det.:** priority order, value delta.
  **Judg.:** whether the role is durable, when to burn priority, what rivals will claim. No product found
  quantifies rival claim likelihood.

### Rest-of-season add/drop (D9)
- **Tools:** HT ROS projections with BLEND windows and a games-played penalty [3]; BBM two independent full-season
  projection sets ("second opinion") [6]; Razzball ROS rankings via FantasyPros [22]; SportsEthos Ethos360
  (paywalled) [23].
- **Computes:** ROS Z/G on blended windows; availability-discounted value.
- **Inputs:** role/usage trends, games remaining, team context, the league's category needs.
- **Det.:** the recompute.
  **Judg.:** signal vs noise, injury return and post-return role, hot hand vs established player.

### Trades (D10)
- **Tools:** HT Trade Analyzer (category gains/losses) [8][24 *(community)*] and Premium Trade Machine [7];
  BBM Trade Analysis / Trade Monster (category impact + league-wide standings/matchups, auto-balancing) [6][14];
  FantasyPros My Playbook trade suggestions [15]; buy-low/sell-high columns (Rotowire, FantasyPros) [20][25].
- **Computes:** per-category deltas both sides, league standings impact, fairness balancing.
- **Inputs:** all rosters, categories, standings and category margins, partner build/needs.
- **Det.:** deltas and re-projection.
  **Judg.:** acceptance likelihood, pitch, fair vs need-matched offers, timing vs the deadline and playoff schedule.

### IL / IL+ (D3, D14)
- **Tools:** Yahoo IL/IR/IR+/NA slots and eligibility [17 *(snippet)*]; injury news (Rotoworld, BBM ticker,
  Locked On Fantasy Basketball podcast) [5][18][26].
- **Computes:** eligibility is a **platform rule check**, not a value computation.
- **Inputs:** official designation, open IL/bench slots, add-limit cost of dropping vs stashing.
- **Det.:** eligibility and roster math.
  **Judg.:** the return timeline (data may give a date but not its reliability), stash vs cut, holding an injured
  star.

### Category build / punting (D11)
- **Tools:** HT "Categories & Multipliers" punt toggles [2][3]; BBM punt settings and DURANT values [6][11];
  community pool-filtering refinements [10 *(community)*]; Rosenof on adaptive punting [1 §4.2].
- **Computes:** punt-excluded re-sums, league category ranks.
- **Inputs:** category set, roster, league-wide category profile.
- **Det.:** re-sums, rankings.
  **Judg.:** which categories to punt and when. The paper's central point is that this depends on the path
  (who's actually available).

### Fantasy playoffs (D13)
- **Tools:** HT schedule grids for named future weeks [4]; BBM schedule-aware projections and H2H tools [6]; Yahoo
  games remaining [12]; stretch-run trend columns [18][20].
- **Computes:** games per team/player in playoff weeks; expected-games-maximizing rosters.
- **Inputs:** the league's playoff weeks (a setting), NBA schedule, injury/role outlook, NBA tank/rest risk.
- **Det.:** game counts.
  **Judg.:** tanking/load management, how early to pivot from current needs to the playoff schedule, variance
  appetite. No product found that offers **playoff-qualification / seeding odds** for H2H.

---

## 3. FBM coverage: standard capability → FBM tool(s)

| Standard capability | FBM tool(s) | Status |
|---|---|---|
| Z-score value | `rankings`, `player_card` (Z shown next to G) | Covered |
| G-score value | `rankings`, `my_roster`, `player_card`, `free_agents`, `team_roster` | **Ahead** (no product found ships G) |
| Punt-adjusted ranks | `rankings(punts=)`, `free_agents(punts=)`, `my_roster(punts=)`, `team_roster(punts=)` | Covered |
| Custom category **weights** as a ranking lens (HT multipliers) | Weights exist inside the choosers and are sent to HT (`category_weights`), but `rankings`/`free_agents` take only `punts` | **Partial gap** |
| Time-window value (1d/7d/14d/season; BLEND7/14/30) | HT views imported and chosen by policy; `game_log`, `player_card` last-10; `rankings` has no window or view argument | **Partial gap** |
| Schedule grid, this week | `schedule(days=)`, `my_roster` games left | Covered |
| Schedule grid, next week / playoff weeks | `next_week`, `playoff_outlook(scope=)` | Covered |
| Schedule grid, **any week N ahead** and light-night (league-wide games per day) view | none | **Gap** (minor) |
| Pace / opponent-defense context for streams | none | **Gap** (low value, judgment-level) |
| Weekly matchup projection | `simulate_matchup`, `weekly_plan`, `next_week`, `week_box`, `scoreboard` | Covered |
| Category win probability | `simulate_matchup` (Monte Carlo), official odds in `weekly_plan` | **Ahead**, calibrated by `odds_reliability` |
| Streaming planner within add limit | `streaming_plan`, `free_agents(sort=week)` | **Ahead** |
| Waiver discovery | `free_agents`, `fa_radar`, `ownership_trends`, `signals(kind=opportunity)` | Covered |
| Rolling-priority claim decision + rival claim odds | `waiver_decision` | **Ahead** |
| ROS add/drop delta | `evaluate_add_drop` | Covered |
| Trade analyzer (category delta, both sides, vs every team) | `evaluate_trade` | Covered (matches BBM incl. league-wide) |
| Trade suggestions / balancing | `find_trades` (+ HT Trade Finder ideas) | Covered |
| Buy-low / sell-high | `buy_sell`, `signals(kind=buy_low/sell_high)` | Covered (twice, see §4) |
| Injury feed + return dates | `injuries`, `news`, `player_card` | Covered (ESPN dates; their reliability isn't graded) |
| IL eligibility / activation | `my_roster`, `lineup_steps` (IL moves), `evaluate_il_activation` | Covered |
| League category profile / opponent scouting | `league_profile`, `team_roster`, `manager_profile`, `league_intel` | **Ahead** (transaction-based habits) |
| Standings | `standings` | Covered (raw) |
| **Playoff-qualification / seeding odds** (simulate the rest of the regular season) | none (`playoff_outlook` is schedule only) | **Gap** |
| Market/transaction trends (Yahoo Research tab) | `ownership_trends`, `transactions` | Covered |
| Second-opinion projections (BBM bonus set) | ESPN engine exists but is off in Hashtag mode ("no fallback") | **Gap by design**, see open questions |
| Trade acceptance likelihood | `find_trades` "defensible for them" value filter; `manager_profile` trade history | Proxy only (judgment) |
| Decision calibration (no product offers it) | `replay_grade`, `decisions_log`, `grade_decisions`, `odds_reliability`, `signal_grades`, `policy_suggestions` | **Ahead** |

---

## 4. Redundancies and judgment embedded in tools

### Overlapping tools (candidates to merge in the v2 catalog)

| Overlap | Notes |
|---|---|
| `evaluate_il_activation` ≡ `evaluate_add_drop(activate=)` | Documented as the same computation. Keep one. |
| `buy_sell` ≈ `signals(kind=buy_low/sell_high)` | Same signal computed now vs from the stored feed. |
| `prediction_calibration` (legacy simulator) vs `odds_reliability` | Two Brier graders. The legacy one only covers pre-Hashtag forecasts. |
| `grade_decisions` vs `replay_grade` vs `weekly_retro_data` vs `signal_grades` | Proposal-level vs decision-level grading. The retro bundles the rest. |
| `simulate_matchup` vs `weekly_plan` vs `next_week` (quick sim) | Three matchup-odds entry points with different variance sources (Monte Carlo vs HT means + sim SD). |
| `scoreboard` vs `week_box` | Live totals vs per-player breakdown of the same week. |
| `my_roster` ≈ `team_roster(team=me)` | Same row format. |
| `league_overview` ⊂ `context_pack` | The pack includes the overview. |
| `snapshot` ⊂ `refresh_data`; `hashtag_import` ⊂ `refresh_data` | Fine as manual escape hatches. |
| `hashtag_status` ⊂ `health_check` | Health includes the HT gate. |
| `optimize_lineup` ⊂ `lineup_steps` | Steps wrap the optimizer. |
| `rankings(available_only=true)` ≈ `free_agents` | Same G-score list, slightly different filters. |
| `fa_radar` ⊃ `free_agents` + `ownership_trends` + `signals` | The radar merges them. Useful, but four discovery entry points. |
| `league_intel` / `manager_profile` / `league_profile` | Activity vs one manager vs category profile. Distinct, but naming is close. |
| Two value scales in Hashtag mode | FBM G-scores (`rankings`) next to HT values (`ht_value` in `find_trades`). The GM can quote either, so pick one canonical scale. |

### Tools that return a decision (judgment) rather than only numbers

These run the shared, policy-parameterized decision rule (`fbm.gm.choosers.choose`) or apply a fixed margin and
return a **verdict**. The rules are deterministic and owner-approved via `policy` proposals, so the behavior is
transparent. They still sit on the tool side of the tool/GM line, which ticket #6 should settle.

| Tool | Embedded judgment |
|---|---|
| `weekly_plan` | chase / hold / lock / concede per category (thresholds), category weights |
| `optimize_lineup` / `lineup_steps` | picks one option among HT views and the pct-risk option by policy |
| `waiver_decision` | claim now / wait / add / pass verdict (bar rising with priority) |
| `streaming_plan` | the chosen add sequence (`stream_min_gain`, `add_reserve`, ROS penalty) |
| `punt_scenarios` | "recommend keep or switch" past a margin |
| `fa_radar` | suggested drop + "tool to confirm with" |
| `find_trades` | "defensible for them" acceptance filter (an opponent-psychology proxy) |
| `signals`, `buy_sell` | confidence labels + action hints |
| `evaluate_trade`, `evaluate_add_drop`, `evaluate_il_activation` | log decisions with a recommendation |

Pure computation, by contrast: `rankings`, `free_agents`, `schedule`, `simulate_matchup`, `league_profile`,
`playoff_outlook`, `week_box`, the Yahoo reads, and the graders.

---

## 5. Takeaways for the v2 spec

1. FBM's quantitative layer already **meets or beats** every standard product capability that matters in-season:
   G-scores, Monte Carlo category odds, an add-limit-aware streaming planner, rival-claim modeling on waivers,
   league-wide trade evaluation, and decision calibration. No consumer product documents the last four.
2. The **real gaps** are small:
   - playoff-qualification / seeding odds;
   - a time-window lens and a custom-weight lens on rankings (what HT/BBM users rely on daily);
   - a schedule grid for any future week, including league-wide games per day (light nights).
3. The **bigger issue is catalog shape, not coverage:** about 15 overlapping pairs or clusters, and about 10 tools
   that return verdicts. Both feed the "final tool catalog" and ticket #6 (tool vs GM line).
4. The standard **judgment** parts are the same everywhere: injury timelines, role durability, opponent behavior,
   trade acceptance, punt timing, NBA tank/rest risk, how much variance to take on. No product automates them.
   They belong to the GM, informed by `news` / `injuries` / `signals` / `manager_profile`.
5. HT's value to FBM is mainly the **projections and weekly totals**. HT's analyzers (trade, matchup, punt toggles)
   duplicate what FBM computes itself. That matters for the open HT-dependency question.

---

## Sources

1. Rosenof, *Static quantification of player value for fantasy basketball* (arXiv:2307.02188 v5) — https://arxiv.org/abs/2307.02188 , https://arxiv.org/html/2307.02188v5
2. Hashtag Basketball — Rankings (category toggles/multipliers, ranking types) — https://hashtagbasketball.com/fantasy-basketball-rankings
3. Hashtag Basketball — Projections (BLEND7/14/30, GP penalty) — https://hashtagbasketball.com/fantasy-basketball-projections
4. Hashtag Basketball — Advanced NBA Schedule Grid — https://hashtagbasketball.com/advanced-nba-schedule-grid
5. Basketball Monster — home (Value column, time windows, news ticker) — https://basketballmonster.com/
6. Basketball Monster — Membership (projection sets, DURANT values, SmartTool, Matchup Monster, Analysis/Trade Monster) — https://basketballmonster.com/membership.aspx
7. Hashtag Basketball — Premium tool list (Matchup Planner, Trade Machine, Waiver Machine, League Scouting Report, Premium Schedule Grid) — https://hashtagbasketball.com/premium/
8. Hashtag Basketball — Trade Analyzer — https://hashtagbasketball.com/fantasy-basketball-trade-analyzer
9. Hashtag Basketball — Waiver Wire — https://hashtagbasketball.com/fantasy-basketball-waiver-wire
10. r/fantasybball post on category-ranking formulas / pool filtering *(community; cached snippet via Brave, exact thread unresolved)* — https://search.brave.com/search?q=sportsethos+fantasy+basketball+punt+g-score+z-score
11. r/fantasybball, "Is Basketball Monster player value very accurate?" *(community)* — https://www.reddit.com/r/fantasybball/comments/5ci2db/is_basketball_monster_player_value_very_accurate/
12. r/fantasybball, Yahoo added games played/remaining (Yahoo staff post, 2018) — https://www.reddit.com/r/fantasybball/comments/a10ky0/yahoo_has_added_games_played_and_remaining_to/
13. r/fantasybball, Hashtag Basketball usage post-draft *(community)* — https://www.reddit.com/r/fantasybball/comments/1gep3x3/hashtag_basketball_usage_post_draft/
14. r/fantasybball, Basketball Monster Trade Monster tool *(community)* — https://www.reddit.com/r/fantasybball/comments/9tn4pt/basketball_monster_users_trade_monster_tool/
15. FantasyPros — My Playbook — https://www.fantasypros.com/nba/myplaybook/
16. r/fantasybball, auction value from z-scores *(community)* — https://www.reddit.com/r/fantasybball/comments/58ncua/how_do_youdetermine_auction_value_based_on_a/
17. Yahoo help, "Identify and manage injured and inactive players" *(snippet)* — https://search.yahoo.com/search?p=site%3Ahelp.yahoo.com+fantasy+basketball+injured+list
18. NBC Sports / Rotoworld fantasy basketball news — https://www.nbcsports.com/fantasy/basketball
19. FantasyPros — waiver wire advice feed — https://www.fantasypros.com/nba/advice/waiver-wire.php
20. Rotowire — basketball hub (waiver, start/sit, buy low/sell high, trends) — https://www.rotowire.com/basketball/
21. Yahoo help — Research tab — https://help.yahoo.com/kb/SLN26603.html
22. FantasyPros — injuries advice feed (Razzball ROS rankings) — https://www.fantasypros.com/nba/advice/injuries.php
23. SportsEthos — Ethos360 — https://sportsethos.com/ethos360/
24. r/fantasybball, free trade analyzer sites *(community)* — https://www.reddit.com/r/fantasybball/comments/1px7br9/what_are_some_free_fantasy_trade_analyzer_sites/
25. FantasyPros — NBA articles (trade advice) — https://www.fantasypros.com/nba/articles/
26. Locked On Fantasy Basketball — https://lockedonpodcasts.com/podcasts/locked-on-fantasy-basketball/
27. r/fantasybball, critique of z-score rankings *(community)* — https://www.reddit.com/r/fantasybball/comments/krqvmn/building_an_alternative_valuation_model_why/

FBM sources: `docs/tools.md`, `docs/analytics.md`, `uv run fbm tool --list`, and `--doc` for `playoff_outlook`,
`schedule`, `rankings`, `free_agents`, `standings`, `league_profile`, `next_week`.
