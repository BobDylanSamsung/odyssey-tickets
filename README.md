# Odyssey session watcher

Watches https://www.eventcinemas.com.au/Cinema/IMAX-Sydney for sessions of
*The Odyssey* on a date that has not gone on sale yet, and notifies as soon as
it does.

Current state lives in [`sessions/README.md`](sessions/README.md).

## Running on GitHub Actions

`.github/workflows/watch.yml` runs hourly, commits any change, and opens a
GitHub issue assigned to the repo owner the first time a watched date is
released. Assigning notifies you regardless of your repo watch settings.

Nothing to configure — it uses the built-in `GITHUB_TOKEN`.

## Running from cron

`check.py` needs Python 3 and nothing else. It keeps its state on disk next to
the script, so point cron at a checkout and let it write there:

```cron
# Every 15 minutes. Cron has a minimal PATH and no working directory, so use
# absolute paths for everything.
*/15 * * * * cd ~/odyssey && /usr/bin/python3 check.py --on-new 'NOTIFY' >> ~/odyssey/watch.log 2>&1
```

`--on-new` runs once, the first time a watched date is released. The alert text
arrives on stdin and in `$ALERT_BODY`; the dates are in `$NEW_DATES`. Substitute
one of these for `NOTIFY`:

```sh
# macOS notification (add `open -a Safari <url>` if you want it to jump straight in)
osascript -e "display notification \"$NEW_DATES on sale\" with title \"The Odyssey\" sound name \"Glass\""

# Push to your phone via ntfy.sh — subscribe to the same topic in the ntfy app
curl -d @- -H 'Title: Odyssey sessions released' ntfy.sh/your-private-topic-name

# Email, if the machine has a working sendmail
mail -s "Odyssey sessions released for $NEW_DATES" you@example.com
```

Without `--on-new` the script still prints the alert and updates the files, so
`>> watch.log` alone works if you would rather poll the log.

To run it on a schedule *and* keep the committed history, add a `git commit`
after the check:

```cron
*/15 * * * * cd ~/odyssey && /usr/bin/python3 check.py --on-new '...'; git commit -qm sessions sessions
```

Watch out for `%` in a crontab — it is rewritten to a newline unless escaped as
`\%`, which rules out an unescaped `date +%F` in the command.

## Watching another date or movie

Edit `watch.json`:

```json
{
  "cinemaId": 96,
  "cinemaName": "IMAX Sydney",
  "movieId": 19797,
  "movieName": "The Odyssey",
  "watchDates": ["2026-08-29"]
}
```

`cinemaId` and `movieId` are the ids used by the endpoint below — the cinema id
is the `#Cinema_Id` hidden input on a cinema page, and the movie id appears as
`Id` in the response. Dates already announced are recorded in
`sessions/alerts.json`; delete an entry to be notified about it again.

## Why not scrape the page?

`#date=2026-08-29` is a URL fragment, so it is never sent to the server — a
plain `curl` of that URL always retrieves the default page, and that page only
server-renders a rolling three-day block of sessions. The date picker is backed
by a JSON endpoint instead:

```
GET https://www.eventcinemas.com.au/Cinemas/GetSessions?cinemaIds=96&date=YYYY-MM-DD
```

`Data.Dates` is the cinema's full on-sale calendar; showtimes for the requested
date are under `Data.Movies[].CinemaModels[].Sessions[]`.
