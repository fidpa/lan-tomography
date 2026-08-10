#!/usr/bin/env python3
"""waves-from-log.py - build waves.csv from any timestamped log.

WHAT THIS IS FOR
    `correlate.py` needs to know WHEN your symptom occurred. That file -
    waves.csv - is the one input this toolkit cannot produce for you, because
    only you know what your symptom is.

    This turns any log with timestamps into it. Give it a pattern that matches
    the lines meaning "the thing happened", and a way to read the timestamp.

    Standard library only. It has no idea what your symptom is and does not
    need to.

EXAMPLES
    # Windows RDP disconnects exported to text
    waves-from-log.py disconnects.txt --match 'Session .* disconnected' \\
        --time-format '%Y-%m-%d %H:%M:%S'

    # A VPN gateway log, syslog timestamps, 3+ per minute counts
    waves-from-log.py /var/log/vpn.log --match 'tunnel down' \\
        --time-format '%b %d %H:%M:%S' --year 2026 --threshold 3

    # Something already emitting epoch seconds
    waves-from-log.py app.log --match 'ERROR conn' --epoch-field 1

    # By hand, from user reports - three careful rows beat three hundred
    # machine-generated ones
    printf 'start_epoch,end_epoch,count,note\\n%s,%s,1,user called\\n' \\
        $(date -d '2026-08-08 14:41' +%s) $(date -d '2026-08-08 14:46' +%s) \\
        > waves.csv

THE THRESHOLD IS A FILTER, AND IT HIDES THINGS
    Emitting a window only when N or more events fall in one minute is how
    these files are usually built, and it is worth knowing what it costs: with
    --threshold 5, a fault affecting one user at a time NEVER APPEARS, while
    being entirely real to that user.

    Default is 1 for that reason. Raise it when the noise is genuinely noise,
    and write down what you raised it to - a quiet waves.csv at threshold 5
    means something different from a quiet one at threshold 1.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from version import read_version


def parse_epoch_field(line: str, index: int) -> float | None:
    """Read a bare epoch from whitespace field `index`, brackets tolerated."""
    parts = line.split()
    if len(parts) <= index:
        return None
    try:
        return float(parts[index].strip("[]"))
    except ValueError:
        return None


def parse_time_format(line: str, fmt: str, year: int | None) -> float | None:
    """Find a timestamp matching `fmt` anywhere in the line.

    strptime needs to know where the timestamp starts, and logs put it in
    different places. Rather than demand an anchor, try successively longer
    prefixes of the line - crude, but it copes with a syslog priority or a
    hostname in front of the timestamp without a second option to get wrong.
    """
    for start in range(min(len(line), 40)):
        for end in range(start + 6, min(len(line), start + 40) + 1):
            try:
                # Naive by design: most logs carry no offset. The timestamp is
                # then read as UTC below, and LT_TZ is what makes that the same
                # decision everywhere. A log that DOES carry an offset should
                # include %z in --time-format.
                dt = datetime.strptime(line[start:end], fmt)  # noqa: DTZ007
            except ValueError:
                continue
            if year is not None and dt.year == 1900:
                dt = dt.replace(year=year)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.timestamp()
    return None


def collect(paths: list[Path], pattern: re.Pattern, *, epoch_field: int | None,
            time_format: str | None, year: int | None) -> list[float]:
    stamps: list[float] = []
    unparsed = 0
    for path in paths:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not pattern.search(line):
                continue
            ts = (parse_epoch_field(line, epoch_field) if epoch_field is not None
                  else parse_time_format(line, time_format, year))
            if ts is None:
                unparsed += 1
                continue
            stamps.append(ts)
    if unparsed:
        # Loud, because silently dropping matched lines would understate the
        # symptom - and an understated symptom reads as a quieter network.
        print(f"warning: {unparsed} matching line(s) had no readable timestamp",
              file=sys.stderr)
    return sorted(stamps)


def group(stamps: list[float], threshold: int, gap_s: float) -> list[dict]:
    """Group timestamps into windows separated by more than `gap_s`."""
    windows: list[dict] = []
    for ts in stamps:
        if windows and ts - windows[-1]["end"] <= gap_s:
            windows[-1]["end"] = ts
            windows[-1]["count"] += 1
        else:
            windows.append({"start": ts, "end": ts, "count": 1})
    return [w for w in windows if w["count"] >= threshold]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--match", required=True,
                    help="regex identifying lines that mean the symptom occurred")
    ap.add_argument("--epoch-field", type=int, default=None,
                    help="whitespace field holding a bare Unix epoch (0-based)")
    ap.add_argument("--time-format", default=None,
                    help="strptime format of the timestamp, e.g. '%%Y-%%m-%%d %%H:%%M:%%S'")
    ap.add_argument("--year", type=int, default=None,
                    help="year to assume when the format carries none (syslog)")
    ap.add_argument("--threshold", type=int, default=1,
                    help="minimum events per window (default 1 - see the header)")
    ap.add_argument("--gap", type=float, default=60.0,
                    help="seconds of quiet that separate two windows (default 60)")
    ap.add_argument("--note", default="",
                    help="text carried into every row")
    ap.add_argument("-o", "--out", type=Path, default=None,
                    help="output file (default: stdout)")
    ap.add_argument("--version", action="version",
                    version=f"lan-tomography {read_version()}")
    args = ap.parse_args()

    if (args.epoch_field is None) == (args.time_format is None):
        print("give exactly one of --epoch-field or --time-format", file=sys.stderr)
        return 2

    try:
        pattern = re.compile(args.match)
    except re.error as exc:
        print(f"bad --match regex: {exc}", file=sys.stderr)
        return 2

    try:
        stamps = collect(args.files, pattern, epoch_field=args.epoch_field,
                         time_format=args.time_format, year=args.year)
    except OSError as exc:
        print(f"cannot read input: {exc}", file=sys.stderr)
        return 1

    if not stamps:
        print("no matching lines with a readable timestamp", file=sys.stderr)
        return 1

    windows = group(stamps, args.threshold, args.gap)

    lines = ["start_epoch,end_epoch,count,note"]
    for w in windows:
        note = args.note.replace(",", ";")
        lines.append(f"{int(w['start'])},{int(w['end'])},{w['count']},{note}")
    text = "\n".join(lines) + "\n"

    if args.out:
        args.out.write_text(text)
        print(f"{len(windows)} window(s) from {len(stamps)} event(s) -> {args.out}",
              file=sys.stderr)
        if args.threshold > 1:
            print(f"note: --threshold {args.threshold} discarded windows with fewer "
                  "events. Record that alongside the result.", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
