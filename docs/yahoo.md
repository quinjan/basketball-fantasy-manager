# Connecting your Yahoo league

FBM reads your league through the Yahoo Fantasy Sports API. Until you have access it runs on a **demo league**: a
simulated 12-team draft over real NBA players with live ESPN data, so every feature works while you wait.

## 1. Apply for API access

Since mid-2026 the Yahoo Fantasy Sports API is gated. Every user must apply at
<https://sports.yahoo.com/developer/access/>. Approval is reported to take weeks and includes signing an agreement
via DocuSign. Wait for Yahoo's "access enabled" email.

## 2. Create a new Yahoo app

After the email, create a **new** app at Yahoo Developer (apps created before approval reportedly can't be fixed):

- API permissions: **Fantasy Sports — Read** (FBM never writes to Yahoo)
- Redirect URI: `https://localhost:8080`

Then submit the new app's Client ID at <https://sports.yahoo.com/developer/application-confirmation/>.

## 3. Authorize and pick your league

```sh
uv run fbm auth            # or `fbm auth --reset` to replace earlier credentials
```

Paste your Client ID and Client Secret into the prompt (they go to your OS credential store, never to a file or to
the AI), approve in the browser, and paste the redirected `https://localhost:8080/...` URL back. The page itself
won't load — that's expected. Then pick your league (`uv run fbm league select` to change it later).

> Never paste secrets into your AI chat. Type them only into the `fbm auth` prompt.

## 4. Switch FBM to your league

```sh
uv run fbm setup --league-source yahoo --yahoo-approved
uv run fbm settings        # pull league settings into memory/league-settings.md
uv run fbm snapshot        # read rosters, scoreboard, standings, waivers, ownership
uv run fbm ids             # players not matched to ESPN ids
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
| HTTP 403 "application not authorized" (or similar) | Your access isn't enabled yet, or you're using an app created before approval, or its Client ID wasn't submitted at the application-confirmation page. Create a new app, submit its Client ID, then `fbm auth --reset`. |
| 401 / token errors | Re-run `uv run fbm auth --reset`. |
| No leagues listed | Make sure the Yahoo account you authorized is in an NBA league for the current season. |
| Players missing values | Run `uv run fbm ids` and add overrides. |
| Stale data warnings | `uv run fbm data sync`, then `uv run fbm snapshot`; check `uv run fbm health`. |
