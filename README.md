# Coptic Fasts & Feasts Calendar

Self-hosted, auto-updating iCal feed of Coptic Orthodox fasts and feasts with every event clearly labeled `FAST:` or `FEAST:` so a glance at the title answers "is it a fast right now?" without needing to know every Coptic event name.

**Subscribe:** `https://coptic.petertadrous.com/coptic.ics`

## How it works

1. **Source:** The [Society of St. Stephen Coptic Orthodox Deacons](https://deacons.suscopts.org/calendar/) publishes a Google Calendar `.ics` containing Coptic feasts and fasts pre-computed through year 2100. This handles all the variable-date complexity (Pascha calculation, Coptic month drift against Gregorian, etc.).
2. **Transform:** `transform.py` fetches the source and:
   - Prefixes every event with `🍃 FAST:`, `✨ FEAST:`, or `NOTE:`
   - **Expands single-day `...Begins` markers into multi-day events** spanning the full fast period (e.g. "Holy Great Fast Begins" on day 1 becomes "🍃 FAST: Great Lent" covering all 55 days of Lent + Holy Week).
3. **Deploy:** GitHub Actions runs the script weekly (Sundays 06:00 UTC) and publishes the result to GitHub Pages, served from `coptic.petertadrous.com` via `CNAME`.

## Setup (one-time)

1. Push this repo to GitHub: `petertadrous/coptic-calendar`.
2. Repo **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. Repo **Settings → Pages → Custom domain → `coptic.petertadrous.com` → Save** (this writes/verifies the CNAME file).
4. In Cloudflare (your existing DNS), add a CNAME record:
   - **Name:** `coptic`
   - **Target:** `petertadrous.github.io`
   - **Proxy status:** DNS only (gray cloud) — GitHub Pages handles HTTPS itself; Cloudflare proxy interferes with cert provisioning.
5. Trigger the workflow manually once (Actions tab → Build Coptic Calendar → Run workflow), or wait for Sunday.
6. Subscribe to `https://coptic.petertadrous.com/coptic.ics` in Google Calendar / Apple Calendar.

## Maintaining

Realistically, never. The source is computed through 2100. The mapping in `transform.py` covers all 31 unique event names that currently appear in the source. If the upstream source ever adds new event names, they're auto-passed through as `NOTE: <original name>` and logged as a warning in the Actions run — nothing breaks, you just see a TODO if you ever look at the workflow logs.

## Files

- `transform.py` — fetches source, transforms, writes `coptic.ics`
- `.github/workflows/build.yml` — weekly cron + push trigger, deploys to Pages
- `index.html` — landing page at `petertadrous.com/`
- `CNAME` — custom domain config for GitHub Pages

## Local testing

```bash
# Against a downloaded source file
python3 transform.py --local path/to/basic.ics

# Or live (requires network)
python3 transform.py
```
