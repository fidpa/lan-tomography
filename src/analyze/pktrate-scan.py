#!/usr/bin/env python3
"""pktrate-scan.py - find floods and surges in a packet-rate log.

WHY THIS IS A SEPARATE TOOL
    This logic used to live in awk blocks inside the event watcher, where it
    could not be tested. Every pitfall below had already produced a wrong
    result at least once, and none of them was covered by anything. Pulling it
    out is what makes them testable - see tests/test_pktrate.py, which has one
    test per pitfall.

THE THREE PITFALLS THIS FILE EXISTS TO GET RIGHT

    1. The timestamp is in SQUARE BRACKETS. In awk, "[1786201899.326]" in
       arithmetic context silently evaluates to 0 - no error, no warning. A
       whole measurement day once collapsed into a single hour that way, and
       the resulting table looked entirely plausible: it had one row.
       Cross-check: fewer than 24 hourly buckets on a full day means brackets.

    2. The timestamp is the END of the interval, not its start. Treating it as
       the start shifts every sample by one interval. In the case this comes
       from that was the difference between a duplication factor of 53.5 and
       1.00 - between "there is a loop" and "there is no loop".

    3. Column order is `uni bcast mcast rx tx drop err missed`. Confusing
       bcast with mcast is an off-by-one that produces confident wrong numbers
       with nothing visibly amiss. The one-line cross-check is
       `uni + bcast + mcast == rx`, and check_alignment() below runs it.

INVOCATION
    pktrate-scan.py <file...> [--flood N] [--surge N] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from version import read_version

# Field order of a pktrate line, after the bracketed timestamp.
FIELDS = ("uni", "bcast", "mcast", "rx", "tx", "drop", "err", "missed")


@dataclass(frozen=True)
class Sample:
    """One sampling interval.

    Attributes:
        end: Unix epoch at the END of the interval. Named `end`, not `ts`,
            because the name is the documentation that stops pitfall 2.
    """

    end: float
    uni: int
    bcast: int
    mcast: int
    rx: int
    tx: int
    drop: int = 0
    err: int = 0
    missed: int = 0

    def start(self, interval: float) -> float:
        """Start of the interval. Requires knowing the sampling interval."""
        return self.end - interval


def parse_line(line: str) -> Sample | None:
    """Parse one pktrate line, or None if it is a comment or malformed.

    Strips the brackets around the timestamp. That is pitfall 1, and it is
    handled here exactly once so no caller has to remember it.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    parts = line.split()
    if len(parts) < 5:
        return None

    stamp = parts[0].strip("[]")
    try:
        end = float(stamp)
    except ValueError:
        return None

    values = {}
    for name, raw in zip(FIELDS, parts[1:]):
        try:
            values[name] = int(raw)
        except ValueError:
            return None

    if len(values) < 5:
        return None
    return Sample(end=end, **values)


def parse_file(path: Path) -> list[Sample]:
    samples = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = parse_line(line)
        if s is not None:
            samples.append(s)
    return samples


def check_alignment(samples: list[Sample]) -> list[Sample]:
    """Return samples where uni + bcast + mcast != rx.

    Any hit means the columns are misaligned - a different driver, a changed
    format, or the fields read in the wrong order. Run this before believing
    any number derived from the file.
    """
    return [s for s in samples if s.uni + s.bcast + s.mcast != s.rx]


def find_events(samples: list[Sample], flood_min: int, surge_min: int,
                interval: float) -> list[dict]:
    """Group consecutive samples above a threshold into single events.

    Consecutive samples above the threshold are ONE event, not one per sample.
    Reporting each sample separately is pitfall 4: a watcher emitting the
    sliding maximum of a window turned 6 events into 18, and the count went
    into a report before anyone noticed.
    """
    events: list[dict] = []
    current: dict | None = None

    for s in sorted(samples, key=lambda x: x.end):
        level = None
        if s.bcast >= flood_min or s.mcast >= flood_min:
            level = "flood"
        elif s.bcast >= surge_min or s.mcast >= surge_min:
            level = "surge"

        if level is None:
            current = None
            continue

        # A gap longer than one interval breaks the run: two bursts either side
        # of a quiet minute are two events.
        if current is not None and s.end - current["end"] <= interval * 1.5:
            current["end"] = s.end
            current["samples"] += 1
            current["peak_bcast"] = max(current["peak_bcast"], s.bcast)
            current["peak_mcast"] = max(current["peak_mcast"], s.mcast)
            if level == "flood":
                current["level"] = "flood"
        else:
            current = {
                "level": level,
                "start": s.start(interval),
                "end": s.end,
                "samples": 1,
                "peak_bcast": s.bcast,
                "peak_mcast": s.mcast,
            }
            events.append(current)

    for e in events:
        e["duration_s"] = round(e["end"] - e["start"], 1)
    return events


def hourly_buckets(samples: list[Sample]) -> dict[int, int]:
    """Samples per hour of the day. The cross-check for pitfall 1.

    A full measurement day must produce 24 buckets. One bucket means the
    timestamps were parsed as 0 somewhere upstream.
    """
    buckets: dict[int, int] = {}
    for s in samples:
        hour = int(s.end // 3600)
        buckets[hour] = buckets.get(hour, 0) + 1
    return buckets


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--flood", type=int,
                    default=int(os.environ.get("LT_FLOOD_MIN", "10000")),
                    help="frames per interval counting as a flood")
    ap.add_argument("--surge", type=int,
                    default=int(os.environ.get("LT_SURGE_MIN", "1500")),
                    help="frames per interval counting as a surge")
    ap.add_argument("--interval", type=float,
                    default=float(os.environ.get("LT_PKTRATE_INTERVAL", "5")),
                    help="sampling interval the log was written with")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--version", action="version",
                    version=f"lan-tomography {read_version()}")
    args = ap.parse_args()

    samples: list[Sample] = []
    for path in args.files:
        try:
            samples.extend(parse_file(path))
        except OSError as exc:
            print(f"cannot read {path}: {exc}", file=sys.stderr)
            return 1

    if not samples:
        print("no usable samples", file=sys.stderr)
        return 1

    misaligned = check_alignment(samples)
    events = find_events(samples, args.flood, args.surge, args.interval)

    if args.json:
        print(json.dumps({
            "samples": len(samples),
            "misaligned": len(misaligned),
            "hours_covered": len(hourly_buckets(samples)),
            "events": events,
        }, separators=(",", ":")))
        return 0

    print(f"samples        : {len(samples)}")
    print(f"hours covered  : {len(hourly_buckets(samples))}")
    if misaligned:
        # Loud, because every number below it is then suspect.
        print(f"MISALIGNED     : {len(misaligned)} sample(s) where "
              f"uni+bcast+mcast != rx - do not trust the numbers below")
    print(f"events         : {len(events)}")
    for e in events:
        print(f"  {e['level']:<6} {e['duration_s']:>7.1f}s  "
              f"peak bcast {e['peak_bcast']:>7}  mcast {e['peak_mcast']:>7}  "
              f"({e['samples']} samples)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
