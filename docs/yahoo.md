# Connecting your Yahoo league

FBM reads your league from Yahoo, read-only. There are two ways in; both read the same Yahoo Fantasy data (same
resources, same format), so every feature works the same either way:

| | **A. Your Yahoo sign-in (web session)** | **B. Official Yahoo Fantasy API** |
|---|---|---|
| Setup | `fbm yahoo login`: sign in once in a browser window | Apply, wait for approval (weeks), create an app, `fbm auth` |
| Available | Now | After Yahoo approves you |
| Reads from | `pub-api-ro.fantasysports.yahoo.com` (what Yahoo's own website uses) | `fantasysports.yahooapis.com` |
| Supported by Yahoo | No: undocumented, may change or stop without notice | Yes |

Until you connect, FBM runs on a **demo league**: a simulated 12-team draft over real NBA players with live ESPN data,
so every feature works while you wait. When a web session is saved, FBM uses it; `fbm yahoo logout` switches back to
the official API.

## A. Connect now with your Yahoo sign-in

```sh
uv run fbm yahoo login     # opens Edge/Chrome on Yahoo's sign-in page; sign in (tick "Stay signed in")
```

FBM waits until Yahoo accepts the session, then:

1. keeps only the cookies a browser sends to Yahoo's **read-only** Fantasy host, in your OS credential store (never in
   a file, never shown, never in the AI chat). The browser window uses a throwaway profile and closes.
2. lets you pick your league (`fbm league select` later to change it), and
3. offers to switch FBM from the demo to your league (or run `uv run fbm setup --league-source yahoo --yes`), then
   runs `fbm settings`, `fbm snapshot` and `fbm ids`.

Then verify every data feed:

```sh
uv run fbm yahoo check     # settings, teams, rosters, scoreboard, standings, waivers, FA % owned, transactions,
                           # pending trades/claims, draft results, Yahoo ADP (+ a few extra resources)
uv run fbm yahoo status    # which route is active, when the session was saved (no secrets shown)
```

- **No browser window?** `uv run fbm yahoo login --paste`: open the URL it shows in a browser where you're signed in
  to Yahoo, copy the `cookie` request header from DevTools (F12 → Network → the request → Headers), press Enter
  (FBM reads it from your clipboard and clears it). Use `--browser msedge|chrome|chromium` to pick a browser.
- **Session expired / signed out** (`fbm health` shows "Yahoo refresh" failing with "run `fbm yahoo login`"): run
  `fbm yahoo login` again. Signing out of Yahoo everywhere or changing your password ends the session.
- **Pace:** reads are spaced `yahoo.session_min_interval_s` apart (a full snapshot is ~25 reads, ~10 seconds).
- **Read-only by construction:** this route only sends GET requests to the read-only host; FBM never writes to Yahoo.

> **Terms of use.** Yahoo's Terms of Service restrict automated access to its services without permission. This
> route reads only your own league, as you, at a person-like pace, but it isn't an approved API: use it at your own
> discretion, and keep your API application going (route B) as the supported long-term path.

## B. Official Yahoo Fantasy API

### 1. Apply for API access

Since mid-2026 the Yahoo Fantasy Sports API is gated. Every user must apply at
<https://sports.yahoo.com/developer/access/>. Approval is reported to take 3–8 weeks and includes signing an agreement
via DocuSign. Wait for Yahoo's "access enabled" email; the "your access is live" email can arrive a day or two before
access actually works, so re-try `fbm yahoo check` daily.

### 2. Create a new Yahoo app

After the email, create a **new** app at Yahoo Developer (apps created before approval reportedly can't be fixed):

- API permissions: **Fantasy Sports — Read** (FBM never writes to Yahoo)
- Redirect URI: `https://localhost:8080`

Then submit the new app's Client ID at <https://sports.yahoo.com/developer/application-confirmation/>.

### 3. Authorize and pick your league

```sh
uv run fbm auth            # or `fbm auth --reset` to replace earlier credentials
```

Paste your Client ID and Client Secret into the prompt (they go to your OS credential store, never to a file or to
the AI), approve in the browser, and paste the redirected `https://localhost:8080/...` URL back. The page itself
won't load — that's expected. Then pick your league (`uv run fbm league select` to change it later).

> Never paste secrets into your AI chat. Type them only into the `fbm auth` prompt.

### 4. Switch FBM to your league

If a web session is saved, run `uv run fbm yahoo logout` first so FBM uses the API.

```sh
uv run fbm setup --league-source yahoo --yahoo-approved
uv run fbm settings        # pull league settings into memory/league-settings.md
uv run fbm snapshot        # read rosters, scoreboard, standings, waivers, ownership
uv run fbm ids             # players not matched to ESPN ids
uv run fbm yahoo check     # every data feed readable?
uv run fbm health          # everything should be OK
```

(Equivalent to setting `[league] source = "yahoo"` in `config/settings.toml`.)

### Player id overrides

FBM matches Yahoo players to ESPN ids automatically. `fbm ids` lists any it couldn't match and how to fix them: add
the mapping to `config/yahoo_id_overrides.toml` (personal, gitignored), then re-run `fbm snapshot`.

## FBM never changes your league

FBM has no Yahoo write access by design; it only reads. Lineup and IL recommendations and approved proposals
(add/drop, waiver claim, trade) come with **exact steps** to perform in the Yahoo app or website. Tell the GM "done"
(`fbm lineup --done`, or `fbm proposals done REF` / `confirm_done` for proposals), and the next snapshot verifies
the result.
Pending trades and waiver claims are shown by `fbm offers`; accept or reject them in Yahoo yourself.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| HTTP 403 "application not authorized" (or similar) | Official API: your access isn't enabled yet, or you're using an app created before approval, or its Client ID wasn't submitted at the application-confirmation page. Create a new app, submit its Client ID, then `fbm auth --reset`. Meanwhile, `fbm yahoo login` reads your league through your Yahoo sign-in. |
| "Yahoo web session expired or signed out" | Run `uv run fbm yahoo login` again. |
| `fbm yahoo login` can't start a browser | Install Edge or Chrome, or `uv run playwright install chromium`, or use `fbm yahoo login --paste`. |
| Sign-in works but "Yahoo didn't accept the session for reads" | Try `fbm yahoo login --paste`; if that fails too, Yahoo has changed the web route: use the official API. |
| 401 / token errors | Official API: re-run `uv run fbm auth --reset`. |
| No leagues listed | Make sure the Yahoo account you authorized is in an NBA league for the current season. |
| Players missing values | Run `uv run fbm ids` and add overrides. |
| Stale data warnings | `uv run fbm data sync`, then `uv run fbm snapshot`; check `uv run fbm health`. |
