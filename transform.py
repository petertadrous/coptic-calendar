#!/usr/bin/env python3
"""
Transform the Society of St. Stephen Coptic Deacons calendar (.ics, valid through
2100) into a "fasting yes/no at a glance" calendar.

Source events come in two shapes:
  1. Single-day feasts: "Glorious Feast of the Resurrection" on one date.
  2. Single-day fast markers: "Holy Great Fast Begins" on day 1 only — useless
     for telling whether you're fasting today on, say, day 30.

This script:
  - Fetches the source .ics
  - Expands each "...Fast Begins" marker into a multi-day all-day event covering
    the full fast, by pairing it with the appropriate end-marker event
  - Prefixes every event title with FAST: / FEAST: / NOTE: so the answer to
    "is it a fast right now?" is visible from the title alone
  - Writes coptic.ics

Unknown event names are kept as NOTE: and printed to stderr so the mapping can
be extended without ever breaking the file.

Source: https://deacons.suscopts.org/calendar/
"""

from __future__ import annotations

import re
import sys
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

SOURCE_URL = (
    "https://calendar.google.com/calendar/ical/"
    "qfdih9v8nohpi1tshnm5gf2jk8@group.calendar.google.com/public/basic.ics"
)
OUTPUT_PATH = Path(__file__).parent / "coptic.ics"

# ---------------------------------------------------------------------------
# Classification of event names that appear in the source calendar.
#
# Each entry is (exact_summary_text, kind, display_name).
#   kind: "FAST_RANGE"   = start of a multi-day fast; needs an end marker
#         "FAST_END"     = single-day event that also marks the END of a fast
#                          (e.g. "Holy Pascha Begins" ends the 55-day Lent fast,
#                          but is itself the start of Holy Week which is also
#                          a fast — these chain together)
#         "FAST_DAY"     = a single-day event that is itself a fast day
#         "FEAST"        = single-day feast (fasting suspended)
#         "NOTE"         = commemoration / informational event, no fasting bearing
#
# Pairing of FAST_RANGE → end is done by the rules below in EXPAND_RULES.
# ---------------------------------------------------------------------------

# Match against the verified 31 unique summaries from the source file:
CLASSIFICATIONS: dict[str, tuple[str, str]] = {
    # Multi-day fasts — start markers
    "Holy Great Fast Begins":         ("FAST_RANGE", "Great Lent"),
    "Holy Pascha Begins":             ("FAST_RANGE", "Holy Week (Pascha)"),
    "Jonah's (Nineveh) Fast Begins":  ("FAST_RANGE", "Jonah's Fast"),
    "St. Mary's Fast Begins":         ("FAST_RANGE", "St. Mary's Fast"),
    "The Apostles' Fast Begins":      ("FAST_RANGE", "Apostles' Fast"),
    "The Holy Nativity Fast Begins":  ("FAST_RANGE", "Nativity Fast"),

    # Feast days (fasting is suspended on these)
    "Glorious Feast of the Resurrection":                    ("FEAST", "Resurrection (Pascha)"),
    "The Holy Nativity Feast":                               ("FEAST", "Nativity"),
    "The Holy Epiphany":                                     ("FEAST", "Epiphany / Theophany"),
    "The Holy Pentecost Feast":                              ("FEAST", "Pentecost"),
    "The Holy Feast of Ascension":                           ("FEAST", "Ascension"),
    "Annunciation Feast":                                    ("FEAST", "Annunciation"),
    "Annunciation Feast (Not celebrated this year)":         ("NOTE",  "Annunciation (not observed)"),
    "The Circumcision Feast":                                ("FEAST", "Circumcision of the Lord"),
    "Transfiguration Feast":                                 ("FEAST", "Transfiguration"),
    "Presentation of the Lord into the Temple":              ("FEAST", "Presentation in the Temple"),
    "Entry of our Lord into Jerusalem (Hosanna Sunday)":     ("FEAST", "Palm Sunday (Hosanna)"),
    "Entry of the Lord into Egypt":                          ("FEAST", "Entry into Egypt"),
    "Feast of the Wedding of Cana of Galilee":               ("FEAST", "Wedding at Cana"),
    "Assumption of St. Mary's Body":                         ("FEAST", "Assumption of St. Mary"),
    "The Apostles' Feast (Martyrdom of St. Peter & St. Paul)": ("FEAST", "Apostles' Feast"),
    "Jonah's (Nineveh) Feast":                               ("FEAST", "Jonah's Feast"),
    "The Nayrouz Feast (Coptic New Year)":                   ("FEAST", "Nayrouz (Coptic New Year)"),
    "The Feast of the Cross":                                ("FEAST", "Feast of the Cross"),
    "The Feast of the Cross (Three days)":                   ("FEAST", "Feast of the Cross"),
    "Thomas’ Sunday":                                        ("FEAST", "Thomas Sunday"),
    "Holy Pascha":                                           ("FEAST", "Pascha"),

    # Days inside Holy Week — already covered by the Holy Week range, but the
    # source file adds named entries for these. Mark them as FAST_DAY so they
    # show as their own labeled event AND the underlying Holy Week range covers
    # the day too (calendars handle overlapping all-day events fine).
    "Lazarus Saturday":   ("FAST_DAY", "Lazarus Saturday"),
    "Covenant Thursday":  ("FAST_DAY", "Covenant Thursday"),
    "Good Friday":        ("FAST_DAY", "Good Friday"),

    # Commemorations
    "Martyrdom of St. Mark the Evangelist": ("NOTE", "Martyrdom of St. Mark"),
}

# Rules for expanding "...Begins" markers into ranges.
# Maps a start-marker summary -> end-marker summary; the multi-day event
# spans [start_date, end_date) where end_date is the day OF the end marker
# (i.e. the fast event ends the night before, since most end markers are
# the feast that follows).
#
# Special cases:
#   - "Holy Pascha Begins" is itself a Holy Week start, ending at Resurrection.
#   - Holy Great Fast (Lent) ends when "Holy Pascha Begins" — they chain, but
#     each is logged as its own range so users can see Lent vs. Holy Week.
EXPAND_RULES: dict[str, str] = {
    "Holy Great Fast Begins":        "Holy Pascha Begins",
    "Holy Pascha Begins":            "Glorious Feast of the Resurrection",
    "St. Mary's Fast Begins":        "Assumption of St. Mary's Body",
    "The Apostles' Fast Begins":     "The Apostles' Feast (Martyrdom of St. Peter & St. Paul)",
    "The Holy Nativity Fast Begins": "The Holy Nativity Feast",
    # Jonah's Fast: fixed 3-day duration; no explicit end event in source
    "Jonah's (Nineveh) Fast Begins": "__FIXED_3_DAYS__",
}


# ---------------------------------------------------------------------------
# .ics parsing
# ---------------------------------------------------------------------------

@dataclass
class Event:
    uid: str
    summary: str
    dtstart: date
    dtend: date              # exclusive (per RFC 5545 for VALUE=DATE)
    raw_block: str           # original VEVENT text, for properties we don't touch

    @property
    def duration_days(self) -> int:
        return (self.dtend - self.dtstart).days


_DATE_RE = re.compile(r"DTSTART;VALUE=DATE:(\d{8})")
_DTEND_RE = re.compile(r"DTEND;VALUE=DATE:(\d{8})")
_SUMMARY_RE = re.compile(r"SUMMARY:(.*?)(?:\r?\n)", re.DOTALL)
_UID_RE = re.compile(r"UID:(.*?)(?:\r?\n)")


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def parse_events(ics_text: str) -> list[Event]:
    blocks = re.findall(r"BEGIN:VEVENT.*?END:VEVENT", ics_text, re.DOTALL)
    events: list[Event] = []
    for block in blocks:
        m_start = _DATE_RE.search(block)
        m_end = _DTEND_RE.search(block)
        m_sum = _SUMMARY_RE.search(block)
        m_uid = _UID_RE.search(block)
        if not (m_start and m_end and m_sum and m_uid):
            continue
        events.append(
            Event(
                uid=m_uid.group(1).strip(),
                summary=m_sum.group(1).strip(),
                dtstart=_parse_date(m_start.group(1)),
                dtend=_parse_date(m_end.group(1)),
                raw_block=block,
            )
        )
    return events


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------

def _ics_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def _build_vevent(uid: str, summary: str, start: date, end: date) -> str:
    """Build a fresh VEVENT block. end is exclusive (per RFC 5545)."""
    return (
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"DTSTAMP:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}\r\n"
        f"DTSTART;VALUE=DATE:{_ics_date(start)}\r\n"
        f"DTEND;VALUE=DATE:{_ics_date(end)}\r\n"
        f"SUMMARY:{summary}\r\n"
        "TRANSP:TRANSPARENT\r\n"
        "END:VEVENT"
    )


def transform(events: list[Event]) -> tuple[list[str], list[str]]:
    """
    Return (vevent_blocks, warnings). Each block is a complete VEVENT string.
    """
    warnings: list[str] = []
    blocks: list[str] = []

    # Index events by summary, keyed for end-marker lookups
    events_by_summary: dict[str, list[Event]] = {}
    for e in events:
        events_by_summary.setdefault(e.summary, []).append(e)

    unknown_summaries: set[str] = set()

    for ev in events:
        cls = CLASSIFICATIONS.get(ev.summary)
        if cls is None:
            unknown_summaries.add(ev.summary)
            # Keep it as NOTE so nothing is silently lost
            blocks.append(
                _build_vevent(
                    uid=ev.uid + "-note",
                    summary=f"NOTE: {ev.summary}",
                    start=ev.dtstart,
                    end=ev.dtend,
                )
            )
            continue

        kind, label = cls

        if kind == "FEAST":
            blocks.append(
                _build_vevent(
                    uid=ev.uid + "-feast",
                    summary=f"✨ FEAST: {label}",
                    start=ev.dtstart,
                    end=ev.dtend,
                )
            )

        elif kind == "NOTE":
            blocks.append(
                _build_vevent(
                    uid=ev.uid + "-note",
                    summary=f"NOTE: {label}",
                    start=ev.dtstart,
                    end=ev.dtend,
                )
            )

        elif kind == "FAST_DAY":
            blocks.append(
                _build_vevent(
                    uid=ev.uid + "-fastday",
                    summary=f"🍃 FAST: {label}",
                    start=ev.dtstart,
                    end=ev.dtend,
                )
            )

        elif kind == "FAST_RANGE":
            rule = EXPAND_RULES.get(ev.summary)
            if rule is None:
                warnings.append(f"No EXPAND_RULES entry for {ev.summary!r}; skipping")
                continue

            if rule == "__FIXED_3_DAYS__":
                start = ev.dtstart
                end = start + timedelta(days=3)  # exclusive
            else:
                # Find the nearest end-marker event AFTER this start
                candidates = events_by_summary.get(rule, [])
                future = [c for c in candidates if c.dtstart > ev.dtstart]
                if not future:
                    warnings.append(
                        f"No end marker {rule!r} found after {ev.summary} on "
                        f"{ev.dtstart}; skipping"
                    )
                    continue
                end_event = min(future, key=lambda c: c.dtstart)
                start = ev.dtstart
                end = end_event.dtstart  # fast ends the day the next event starts

            blocks.append(
                _build_vevent(
                    uid=ev.uid + "-fastrange",
                    summary=f"🍃 FAST: {label}",
                    start=start,
                    end=end,
                )
            )
        else:
            warnings.append(f"Unknown classification kind {kind!r} for {ev.summary!r}")

    if unknown_summaries:
        warnings.append(
            "Unmapped event summaries (kept as NOTE: ...):\n  - "
            + "\n  - ".join(sorted(unknown_summaries))
        )

    return blocks, warnings


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

CALENDAR_HEADER = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//petertadrous.com//Coptic Fasts and Feasts//EN\r\n"
    "CALSCALE:GREGORIAN\r\n"
    "METHOD:PUBLISH\r\n"
    "X-WR-CALNAME:Coptic Fasts & Feasts (labeled)\r\n"
    "X-WR-CALDESC:Coptic Orthodox fasts (multi-day) and feasts, labeled "
    "FAST/FEAST/NOTE for at-a-glance reading. Source: deacons.suscopts.org\r\n"
)
CALENDAR_FOOTER = "END:VCALENDAR\r\n"


def fetch_source(url: str = SOURCE_URL) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "coptic-ics-transformer/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--local":
        # Dev mode: read from stdin or a path arg
        path = sys.argv[2] if len(sys.argv) > 2 else None
        if path:
            ics_text = Path(path).read_text(encoding="utf-8", errors="replace")
        else:
            ics_text = sys.stdin.read()
    else:
        print(f"Fetching {SOURCE_URL}", file=sys.stderr)
        ics_text = fetch_source()

    events = parse_events(ics_text)
    print(f"Parsed {len(events)} source events", file=sys.stderr)

    blocks, warnings = transform(events)
    print(f"Produced {len(blocks)} output events", file=sys.stderr)

    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)

    output = CALENDAR_HEADER + "\r\n".join(blocks) + "\r\n" + CALENDAR_FOOTER
    OUTPUT_PATH.write_text(output, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} ({len(output):,} bytes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
