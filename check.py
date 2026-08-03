#!/usr/bin/env python3
"""Watch Event Cinemas for sessions of a movie at a cinema, and flag newly released dates.

The public page at /Cinema/IMAX-Sydney#date=YYYY-MM-DD selects the date entirely
client-side, so fetching that URL only ever returns the default page. The date
picker is backed by this JSON endpoint instead:

    GET /Cinemas/GetSessions?cinemaIds=<id>&date=YYYY-MM-DD

which returns Data.Dates (every date the cinema currently has anything on sale
for) and Data.Movies[].CinemaModels[].Sessions[] for the requested date.

Writes:
  sessions/odyssey.json  normalised {date: [session, ...]} for the watched movie
  sessions/README.md     human-readable summary
  sessions/alerts.json   which watched dates we have already announced

When a watched date has sessions for the first time, exposes `new_dates` (comma
separated) on $GITHUB_OUTPUT and runs any `--on-new` command.
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

BASE = "https://www.eventcinemas.com.au/Cinemas/GetSessions"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

ROOT = pathlib.Path(__file__).resolve().parent
OUT_DIR = ROOT / "sessions"
CONFIG = json.loads((ROOT / "watch.json").read_text())


def fetch(cinema_id, date):
    url = f"{BASE}?{urllib.parse.urlencode({'cinemaIds': cinema_id, 'date': date})}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.load(resp)
            break
        except Exception as exc:  # noqa: BLE001 - retry anything transient
            if attempt == 3:
                raise
            print(f"  retry {date} after {exc}", file=sys.stderr)
            time.sleep(2 ** attempt)
    if not body.get("Success"):
        raise RuntimeError(f"GetSessions failed for {date}: {body}")
    return body["Data"]


def sessions_for(data, movie_id, cinema_id):
    """Pull the flat session list for one movie at one cinema out of a day's payload."""
    out = []
    for movie in data.get("Movies") or []:
        if movie.get("Id") != movie_id:
            continue
        for cinema in movie.get("CinemaModels") or []:
            if cinema.get("Id") != cinema_id:
                continue
            for s in cinema.get("Sessions") or []:
                out.append(
                    {
                        "sessionId": s["Id"],
                        # StartTime is already local cinema time.
                        "startTime": s["StartTime"],
                        "screen": s.get("ScreenTypeName") or s.get("ScreenType"),
                        "seatsAvailable": s.get("SeatsAvailable"),
                        "bookingUrl": s.get("BookingUrl"),
                    }
                )
    out.sort(key=lambda s: s["startTime"])
    return out


def pretty_time(iso):
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M").strftime("%-I:%M %p")


def pretty_date(date):
    return datetime.strptime(date, "%Y-%m-%d").strftime("%a %-d %b %Y")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--on-new",
        metavar="CMD",
        help="shell command to run when a watched date is released; the alert text is "
        "passed on stdin and as $ALERT_BODY, the dates as $NEW_DATES",
    )
    args = parser.parse_args(argv)

    cinema_id = CONFIG["cinemaId"]
    movie_id = CONFIG["movieId"]
    watch_dates = CONFIG["watchDates"]

    # Any date works as a probe: Data.Dates is the cinema's full on-sale calendar.
    probe = fetch(cinema_id, watch_dates[0] if watch_dates else datetime.now().strftime("%Y-%m-%d"))
    calendar = probe.get("Dates") or []
    print(f"on-sale calendar: {len(calendar)} dates, {calendar[0]} -> {calendar[-1]}")

    # Watched dates may not be on sale yet, so check them even if absent from the calendar.
    targets = sorted(set(calendar) | set(watch_dates))

    by_date = {}
    for date in targets:
        data = probe if date == probe.get("SelectedDate") else fetch(cinema_id, date)
        found = sessions_for(data, movie_id, cinema_id)
        if found:
            by_date[date] = found
        print(f"  {date}: {len(found)} session(s)")
        time.sleep(0.3)

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "odyssey.json").write_text(json.dumps(by_date, indent=2) + "\n")

    alerts_path = OUT_DIR / "alerts.json"
    alerts = json.loads(alerts_path.read_text()) if alerts_path.exists() else {}

    new_dates = []
    for date in watch_dates:
        if by_date.get(date) and date not in alerts:
            alerts[date] = {
                "notifiedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "sessionCount": len(by_date[date]),
            }
            new_dates.append(date)
    alerts_path.write_text(json.dumps(alerts, indent=2) + "\n")

    write_summary(by_date, calendar, watch_dates)

    body = alert_text(new_dates, by_date)
    if new_dates:
        print(f"NEW: sessions released for {', '.join(new_dates)}")
        print(body)
    emit_outputs(new_dates, body)
    if new_dates and args.on_new:
        run_hook(args.on_new, new_dates, body)


def write_summary(by_date, calendar, watch_dates):
    movie = CONFIG["movieName"]
    cinema = CONFIG["cinemaName"]
    lines = [
        f"# {movie} at {cinema}",
        "",
        f"Last checked: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
        f"On sale through: **{calendar[-1] if calendar else 'unknown'}**",
        "",
        "## Watching",
        "",
    ]
    for date in watch_dates:
        found = by_date.get(date)
        state = f"**{len(found)} session(s) released**" if found else "not released yet"
        lines.append(f"- {pretty_date(date)} — {state}")
    lines += ["", "## All sessions", "", "| Date | Sessions |", "| --- | --- |"]
    for date in sorted(by_date):
        times = ", ".join(f"{pretty_time(s['startTime'])} ({s['screen']})" for s in by_date[date])
        lines.append(f"| {pretty_date(date)} | {times} |")
    lines.append("")
    (OUT_DIR / "README.md").write_text("\n".join(lines))


def alert_text(new_dates, by_date):
    lines = []
    for date in new_dates:
        lines.append(f"### {pretty_date(date)}")
        lines.append("")
        for s in by_date[date]:
            seats = s["seatsAvailable"]
            seats = f" — {seats} seats" if seats is not None else ""
            lines.append(f"- [{pretty_time(s['startTime'])} {s['screen']}]({s['bookingUrl']}){seats}")
        lines.append("")
    return "\n".join(lines)


def emit_outputs(new_dates, body):
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with open(out, "a") as fh:
        fh.write(f"new_dates={','.join(new_dates)}\n")
        fh.write("body<<EOF\n" + body + "\nEOF\n")


def run_hook(command, new_dates, body):
    env = dict(os.environ, NEW_DATES=",".join(new_dates), ALERT_BODY=body)
    result = subprocess.run(command, shell=True, input=body, text=True, env=env)
    if result.returncode != 0:
        print(f"--on-new command exited {result.returncode}", file=sys.stderr)


if __name__ == "__main__":
    main()
