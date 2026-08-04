# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A zero-dependency Python 3 script that polls the Event Cinemas JSON API for *The Odyssey* sessions at IMAX Sydney and fires alerts when watched dates go on sale. Runs hourly via GitHub Actions or locally via cron.

## Running

```bash
python3 check.py
# With a notification hook:
python3 check.py --on-new 'osascript -e "display notification \"$NEW_DATES\" with title \"Odyssey\""'
```

No install step — stdlib only.

## Configuration

`watch.json` — edit to change cinema, movie, or dates to watch:
```json
{
  "cinemaId": 96,
  "cinemaName": "IMAX Sydney",
  "movieId": 19797,
  "movieName": "The Odyssey",
  "watchDates": ["2026-08-29"]
}
```

## Architecture

All logic lives in `check.py` (~200 lines). Key functions:

- `fetch(cinema_id, date)` — GET `https://www.eventcinemas.com.au/Cinemas/GetSessions?cinemaIds=<id>&date=<YYYY-MM-DD>` with 4-attempt retry backoff. The `#date=` URL fragment is client-side only; this JSON endpoint is the real data source.
- `sessions_for(data, movie_id, cinema_id)` — extracts and sorts sessions from the API payload.
- `main(argv)` — reads config, fetches each watched date, writes outputs, checks for new releases, invokes hook.

Output files written to `sessions/` on each run:
- `odyssey.json` — normalised `{date: [session, ...]}` dict
- `README.md` — human-readable markdown table (auto-overwritten)
- `alerts.json` — tracks which dates have already fired an alert (prevents duplicates)

## GitHub Actions (`watch.yml`)

Runs at `17 * * * *` (hourly). On new sessions found, creates a GitHub issue assigned to the repo owner. Commits `sessions/` back to the repo after each run with a rebase-retry loop.
