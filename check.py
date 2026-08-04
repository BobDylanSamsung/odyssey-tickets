#!/usr/bin/env python3
"""Watch Event Cinemas for sessions of a movie at a cinema.

Alerts on two events:
  - A session ID appears for the first time (new show on sale).
  - A known session's seatsAvailable increases (returned/refunded tickets).

The public page at /Cinema/IMAX-Sydney#date=YYYY-MM-DD selects the date entirely
client-side, so fetching that URL only ever returns the default page. The date
picker is backed by this JSON endpoint instead:

    GET /Cinemas/GetSessions?cinemaIds=<id>&date=YYYY-MM-DD

which returns Data.Dates (every date the cinema currently has anything on sale
for) and Data.Movies[].CinemaModels[].Sessions[] for the requested date.

Writes:
  sessions/odyssey.json  normalised {date: [session, ...]} for the watched movie
  sessions/README.md     human-readable summary
  sessions/state.json    per-session seat counts and first-seen tracking
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


def load_state(state_path):
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text())


def diff_sessions(by_date, state):
    """Return new sessions and sessions with increased seat counts.

    Also updates state in-place with current seat counts.
    """
    new_sessions = []
    seat_increases = []

    for date, sessions in by_date.items():
        for s in sessions:
            sid = str(s["sessionId"])
            current_seats = s.get("seatsAvailable")

            if sid not in state:
                new_sessions.append({**s, "date": date})
                state[sid] = {
                    "date": date,
                    "startTime": s["startTime"],
                    "screen": s["screen"] or "",
                    "seatsAvailable": current_seats,
                    "firstSeenAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            else:
                prev_seats = state[sid].get("seatsAvailable")
                if current_seats is not None and prev_seats is not None and current_seats > prev_seats:
                    seat_increases.append({
                        **s,
                        "date": date,
                        "oldCount": prev_seats,
                        "newCount": current_seats,
                    })
                state[sid]["seatsAvailable"] = current_seats

    return new_sessions, seat_increases


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--on-new",
        metavar="CMD",
        help="shell command to run when new sessions are found; alert text passed as $ALERT_BODY",
    )
    args = parser.parse_args(argv)

    cinema_id = CONFIG["cinemaId"]
    movie_id = CONFIG["movieId"]

    probe_date = datetime.now().strftime("%Y-%m-%d")
    probe = fetch(cinema_id, probe_date)
    calendar = probe.get("Dates") or []
    print(f"on-sale calendar: {len(calendar)} dates, {calendar[0]} -> {calendar[-1]}")

    by_date = {}
    for date in calendar:
        data = probe if date == probe.get("SelectedDate") else fetch(cinema_id, date)
        found = sessions_for(data, movie_id, cinema_id)
        if found:
            by_date[date] = found
        print(f"  {date}: {len(found)} session(s)")
        time.sleep(0.3)

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "odyssey.json").write_text(json.dumps(by_date, indent=2) + "\n")

    state_path = OUT_DIR / "state.json"
    state = load_state(state_path)

    new_sessions, seat_increases = diff_sessions(by_date, state)

    state_path.write_text(json.dumps(state, indent=2) + "\n")

    write_summary(by_date, calendar)

    new_body = new_sessions_text(new_sessions)
    seat_body = seat_increase_text(seat_increases)

    if new_sessions:
        new_dates = sorted(set(s["date"] for s in new_sessions))
        print(f"NEW SESSIONS: {', '.join(new_dates)}")
        print(new_body)

    if seat_increases:
        inc_dates = sorted(set(s["date"] for s in seat_increases))
        print(f"SEATS FREED: {', '.join(inc_dates)}")
        print(seat_body)

    emit_outputs(new_sessions, new_body, seat_increases, seat_body)

    if new_sessions and args.on_new:
        run_hook(args.on_new, new_body)


def write_summary(by_date, calendar):
    movie = CONFIG["movieName"]
    cinema = CONFIG["cinemaName"]
    lines = [
        f"# {movie} at {cinema}",
        "",
        f"Last checked: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
        f"On sale through: **{calendar[-1] if calendar else 'unknown'}**",
        "",
        "## All sessions",
        "",
        "| Date | Sessions |",
        "| --- | --- |",
    ]
    for date in sorted(by_date):
        times = ", ".join(
            f"{pretty_time(s['startTime'])} ({s['screen']}) — {s['seatsAvailable']} seats"
            for s in by_date[date]
        )
        lines.append(f"| {pretty_date(date)} | {times} |")
    lines.append("")
    (OUT_DIR / "README.md").write_text("\n".join(lines))


def new_sessions_text(new_sessions):
    lines = []
    by_date = {}
    for s in new_sessions:
        by_date.setdefault(s["date"], []).append(s)
    for date in sorted(by_date):
        lines.append(f"### {pretty_date(date)}")
        lines.append("")
        for s in by_date[date]:
            seats = s["seatsAvailable"]
            seats_str = f" — {seats} seats" if seats is not None else ""
            lines.append(f"- [{pretty_time(s['startTime'])} {s['screen']}]({s['bookingUrl']}){seats_str}")
        lines.append("")
    return "\n".join(lines)


def seat_increase_text(increases):
    lines = []
    for inc in increases:
        lines.append(f"### {pretty_date(inc['date'])} — {pretty_time(inc['startTime'])} {inc['screen']}")
        lines.append("")
        lines.append(f"Seats: {inc['oldCount']} → **{inc['newCount']}** available")
        lines.append(f"[Book now]({inc['bookingUrl']})")
        lines.append("")
    return "\n".join(lines)


def emit_outputs(new_sessions, new_body, seat_increases, seat_body):
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    new_dates = sorted(set(s["date"] for s in new_sessions))
    inc_dates = sorted(set(s["date"] for s in seat_increases))
    with open(out, "a") as fh:
        fh.write(f"new_dates={','.join(new_dates)}\n")
        fh.write("new_body<<EOF\n" + new_body + "\nEOF\n")
        fh.write(f"seat_dates={','.join(inc_dates)}\n")
        fh.write("seat_body<<EOF\n" + seat_body + "\nEOF\n")


def run_hook(command, body):
    env = dict(os.environ, ALERT_BODY=body)
    result = subprocess.run(command, shell=True, input=body, text=True, env=env)
    if result.returncode != 0:
        print(f"--on-new command exited {result.returncode}", file=sys.stderr)


if __name__ == "__main__":
    main()
