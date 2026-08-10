#!/usr/bin/env python3
"""generate.py - synthesise a measurement campaign carrying real signatures.

WHY SYNTHETIC DATA
    Without sample data nobody can follow the analysis. With real captures,
    anonymisation is not practically achievable - a pcap contains everything
    that crossed the wire, payloads included.

    So: generate data that carries the SAME SIGNATURES as the real thing, and
    demonstrate the analysis on that. Every number below is modelled on
    measured values from a real intermittent layer-2 loop; none of it is a
    recording of one.

WHAT IT PRODUCES
    A scripted campaign (default 3 days, --days 9 for the full story)
    containing:

    * a BROADCAST FLOOD, quiet baseline around 100 frames per 5 s, peaking over
      30,000 - the ratio, not the absolute number, is the signature
    * a GATEWAY OUTAGE that occurs WITHOUT a flood, which is what shows the two
      phenomena are decoupled and stops "the flood caused it" being assumed
    * a PRECURSOR 0.1 s before each surge, so the correlation is provable on
      the second
    * workstations that go OFFLINE overnight, so the "silent is not failed"
      distinction has something to bite on
    * one target that is silent throughout, because a target that is simply not
      there is a case every analysis meets
    * a day whose file is COMPRESSED, so the archive-day pitfall is exercised

    Plus the derived inputs: waves.csv, switch-timeline.jsonl.

REPRODUCIBLE
    Fixed seed. The same invocation always produces the same bytes, so a
    documented result stays checkable.

INVOCATION
    generate.py --out ./data [--days 3] [--seed 1]

    Ping logs run at one sample per second per target, so the output is
    roughly 40 MB per day - the same order as a real campaign. It is
    written wherever you point it and is never committed.
    correlate.py --ping-dir ./data/ping --waves ./data/waves.csv \\
                 --targets ../../config/targets.conf.example
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src" / "lib"))
from version import read_version

# Day 1 at 00:00 UTC. An arbitrary fixed point - the campaign is told in
# relative days, so the absolute date carries no meaning.
EPOCH_DAY1 = 1785888000  # 2026-08-04 00:00:00 UTC

# The generator writes ONE sample per second, not the 0.2 s a real probe uses.
# Five times fewer lines, same signatures, a quarter of the disk.
#
# The analysis must be told, or it will multiply lost packets by 0.2 and report
# a 300-second outage as 60 seconds. That is why every hint printed at the end
# sets LT_PING_INTERVAL=1 - a demo that quietly teaches a wrong number is worse
# than no demo.
PING_INTERVAL = 1.0
PKTRATE_INTERVAL = 5

# Roles match config/targets.conf.example.
# Exactly the labels in config/targets.conf.example, so the generated data can
# be analysed with the shipped matrix and nothing reports NO DATA for a reason
# that is an artefact of this generator rather than a property of the data.
TARGETS = [
    ("ts01",       "symptom",     True),
    ("hv01",       "hypervisor",  True),
    ("mail01",     "same-host",   True),
    ("dc01",       "same-host",   True),
    ("vm01",       "other-host",  True),
    ("sql01",      "other-host",  True),
    ("gw",         "fabric-ref",  True),
    ("scan01",     "fabric-ref",  True),
    ("probe3",     "uplink-ref",  True),
    ("ws01",       "client-path", False),   # off overnight
    ("ws02",       "client-path", False),
    ("sw-floor",   "switch-ref",  True),
    ("sw-core-a",  "switch-ref",  True),
    ("sw-core-b",  "switch-ref",  True),
]

# Quiet baseline, measured order of magnitude.
BCAST_QUIET_MEDIAN = 100
BCAST_QUIET_MAX = 243
MCAST_QUIET = 30

# Storm peak. The ratio to the baseline is the signature, not the number.
BCAST_STORM_PEAK = 32000


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds")


def day_str(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")


class Campaign:
    """The scripted events. Written down rather than randomised, because the
    point of this data is that a documented analysis produces a documented
    result."""

    def __init__(self, days: int):
        self.days = days
        # (start_offset_from_day1, duration_s, kind)
        self.events: list[tuple[float, float, str]] = []

        # Day 2, 10:19:53 - a gateway outage with NO flood. This is the one
        # that matters: it proves the two phenomena decouple, and without it a
        # reader will assume the flood explains everything.
        self.events.append((1 * 86400 + 37193, 298.8, "gateway-only"))

        # Day 3, 03:15:39 - a flood at night, no users affected. Shows that
        # "nobody complained" is not "nothing happened".
        self.events.append((2 * 86400 + 11739, 42.0, "flood"))

        # Day 4, 14:41:41 - the strongest storm, during working hours.
        self.events.append((3 * 86400 + 52901, 65.0, "flood+outage"))

        # Day 6, 09:35:58 - flood plus gateway outage, the classic pairing.
        self.events.append((5 * 86400 + 34558, 495.0, "flood+outage"))

    def event_at(self, ts_offset: float) -> str | None:
        for start, dur, kind in self.events:
            if start <= ts_offset <= start + dur:
                return kind
        return None

    def surge_precursor_at(self, ts_offset: float) -> bool:
        """0.1 s before each flood there is a precursor frame burst.

        This is what makes the correlation provable to the second - and why the
        case study insists on keeping clock times while relativising dates.
        """
        for start, _dur, kind in self.events:
            if "flood" in kind and 0 <= start - ts_offset <= 0.1:
                return True
        return False


def is_night(ts: float) -> bool:
    hour = datetime.fromtimestamp(ts, UTC).hour
    return hour < 7 or hour >= 19


def gen_ping(out: Path, camp: Campaign, rng: random.Random) -> None:
    """One daily file per target, in the exact format ping -D -O writes."""
    ping_dir = out / "ping"
    ping_dir.mkdir(parents=True, exist_ok=True)

    for label, _role, always_on in TARGETS:
        for day in range(camp.days):
            day_start = EPOCH_DAY1 + day * 86400
            path = ping_dir / f"{label}-{day_str(day_start)}.log"
            header = (f"# ---- measurement starts {iso(day_start)} | target "
                      f"192.0.2.x ({label}) | interval {PING_INTERVAL}s | "
                      f"probe probe1 ----")
            lines = [header, f"PING 192.0.2.x ({label}) 56(84) bytes of data."]

            seq = 0
            # One sample per second rather than per 0.2 s: the file stays
            # readable and the signatures survive intact.
            for second in range(86400):
                ts = day_start + second
                seq = (seq + 1) % 65536
                offset = ts - EPOCH_DAY1

                if always_on is None:
                    # Never answers. A target that is simply not there.
                    lines.append(f"[{ts}.000000] no answer yet for icmp_seq={seq}")
                    continue

                if not always_on and is_night(ts):
                    # Switched off. ping gives up after a run of unreachables,
                    # so the file simply has fewer lines - which is exactly how
                    # it looks in reality.
                    if second % 60 == 0:
                        lines.append(
                            f"[{ts}.000000] From 192.0.2.x icmp_seq={seq} "
                            "Destination Host Unreachable")
                    continue

                kind = camp.event_at(offset)
                affected = False
                if kind == "gateway-only":
                    affected = label == "gw"
                elif kind == "flood+outage":
                    affected = label in ("gw", "ts01", "mail01", "hv01", "sw-floor")
                elif kind == "flood":
                    # A flood does not always take a target down. This is what
                    # makes the correlation interesting rather than trivial.
                    affected = label == "sw-floor" and rng.random() < 0.6

                if affected:
                    lines.append(f"[{ts}.000000] no answer yet for icmp_seq={seq}")
                    continue

                # Ordinary loss: one to three packets in a thousand.
                if rng.random() < 0.002:
                    lines.append(f"[{ts}.000000] no answer yet for icmp_seq={seq}")
                    continue

                rtt = 0.4 + rng.random() * 0.4
                # switch-ref scatters, because it answers from a management CPU
                # rather than the forwarding path - pitfall D1, visible here.
                if label == "sw-floor":
                    rtt = 1.6 + rng.random() * 3.9
                lines.append(
                    f"[{ts}.000000] 64 bytes from 192.0.2.x: icmp_seq={seq} "
                    f"ttl=64 time={rtt:.3f} ms")

            path.write_text("\n".join(lines) + "\n")


def gen_pktrate(out: Path, camp: Campaign, rng: random.Random) -> None:
    pkt_dir = out / "pktrate"
    pkt_dir.mkdir(parents=True, exist_ok=True)

    for day in range(camp.days):
        day_start = EPOCH_DAY1 + day * 86400
        path = pkt_dir / f"probe1-{day_str(day_start)}.log"
        lines = [
            f"# lan-tomography packet rates (probe1, eth0), interval {PKTRATE_INTERVAL}s",
            "# Timestamp = Unix epoch, in brackets, at the END of the interval.",
            "# Values are DELTAS within the interval, not rates.",
            "# Fields: uni bcast mcast rx tx drop err missed",
            "# Cross-check: uni + bcast + mcast == rx",
        ]

        for step in range(86400 // PKTRATE_INTERVAL):
            ts = day_start + (step + 1) * PKTRATE_INTERVAL   # END of interval
            offset = ts - EPOCH_DAY1
            kind = camp.event_at(offset)

            if kind and "flood" in kind:
                bcast = int(BCAST_STORM_PEAK * (0.5 + rng.random() * 0.5))
                mcast = int(bcast * 0.1)
            elif camp.surge_precursor_at(offset):
                bcast = 1800
                mcast = 40
            else:
                bcast = rng.randint(BCAST_QUIET_MEDIAN // 2, BCAST_QUIET_MAX)
                mcast = rng.randint(10, MCAST_QUIET)

            uni = rng.randint(150, 600)
            rx = uni + bcast + mcast          # the cross-check holds by construction
            tx = rng.randint(100, 500)
            drop = int(bcast * 0.01) if bcast > 10000 else 0
            lines.append(f"[{ts}.000] {uni} {bcast} {mcast} {rx} {tx} {drop} 0 0")

        path.write_text("\n".join(lines) + "\n")


def gen_waves(out: Path, camp: Campaign) -> None:
    """The symptom windows - the INPUT a real user brings themselves."""
    rows = ["start_epoch,end_epoch,count,note"]
    horizon = camp.days * 86400
    for start, dur, kind in camp.events:
        if kind == "flood":
            continue          # nobody was working; no symptom reported
        # Only windows the campaign actually covers. A wave pointing past the
        # generated data would make every tool report NO DATA and look broken,
        # when the real answer is "you did not generate that day".
        if start + dur > horizon:
            continue
        ts = EPOCH_DAY1 + start
        count = 7 if "outage" in kind else 3
        rows.append(f"{int(ts)},{int(ts + dur)},{count},synthetic {kind}")
    (out / "waves.csv").write_text("\n".join(rows) + "\n")


def gen_switch_timeline(out: Path, camp: Campaign, rng: random.Random) -> None:
    path = out / "switch-timeline.jsonl"
    records = []
    prev_ts = None

    for day in range(camp.days):
        day_start = EPOCH_DAY1 + day * 86400
        # Every minute, matching the shipped lt-switch-probe.timer.
        for minute in range(1440):
            ts = day_start + minute * 60
            offset = ts - EPOCH_DAY1
            kind = camp.event_at(offset)

            ports = {}
            for idx, descr in ((5, "uplink"), (23, "floor"), (25, "spare")):
                d = {"in_discards": 0, "in_errors": 0, "out_errors": 0,
                     "in_octets": rng.randint(10**6, 10**7),
                     "out_octets": rng.randint(10**6, 10**7)}
                # Discards during a flood - but see pitfall D2: not every flood
                # minute is represented, because the agent goes blind while the
                # device is busy. That gap is part of the signature.
                d["out_discards"] = rng.randint(5000, 40000) if kind and "flood" in kind else 0
                ports[str(idx)] = {"descr": descr, "oper_status": 1,
                                   "speed": 1000000000, "d": d, "dt": 60.0}

            if kind and "flood" in kind and rng.random() < 0.66:
                continue      # the agent did not answer. Deliberate.

            records.append(json.dumps({
                "ts": iso(ts), "switch": "192.0.2.2",
                "interval_s": round(ts - prev_ts, 1) if prev_ts else None,
                "ports": ports,
            }, separators=(",", ":")))
            prev_ts = ts

    path.write_text("\n".join(records) + "\n")


def compress_one_day(out: Path) -> None:
    """Archive day 1 so the compressed-archive pitfall is exercised."""
    day1 = day_str(EPOCH_DAY1)
    for path in sorted((out / "ping").glob(f"*-{day1}.log")):
        try:
            subprocess.run(["zstd", "-q", "--rm", str(path)], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("  (zstd not available - day 1 left uncompressed)")
            return


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("./data"))
    ap.add_argument("--days", type=int, default=3,
                    help="campaign length. Ping logs run at one sample per\n"
                         "second per target, so this costs roughly 50 MB per\n"
                         "day - the same order as the real thing. Use 9 for\n"
                         "the full scripted campaign.")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--version", action="version",
                    version=f"lan-tomography {read_version()}")
    args = ap.parse_args()

    # Not cryptographic - a fixed seed is the point.
    rng = random.Random(args.seed)
    camp = Campaign(args.days)
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"generating {args.days} days into {args.out} (seed {args.seed})")
    gen_ping(args.out, camp, rng)
    print("  ping logs")
    gen_pktrate(args.out, camp, rng)
    print("  packet-rate logs")
    gen_waves(args.out, camp)
    print("  waves.csv")
    gen_switch_timeline(args.out, camp, rng)
    print("  switch-timeline.jsonl")
    compress_one_day(args.out)
    print("  day 1 archived (.zst)")

    print()
    print("scripted events, in campaign-relative time:")
    for start, dur, kind in camp.events:
        d = int(start // 86400) + 1
        clock = str(timedelta(seconds=int(start % 86400)))
        outside = "   (beyond --days, not generated)" if d > args.days else ""
        print(f"  day {d}, {clock}  {kind:<14} {dur:>6.1f}s{outside}")
    print()
    print("try:")
    print(f"  src/analyze/pktrate-scan.py {args.out}/pktrate/*.log")
    print()
    print("  # LT_PING_INTERVAL=1 because this generator samples once a second,")
    print("  # not five times. Without it the outage durations come out 5x short.")
    print("  LT_PING_INTERVAL=1 src/analyze/correlate.py \\")
    print(f"      --ping-dir {args.out}/ping --waves {args.out}/waves.csv \\")
    print("      --targets config/targets.conf.example")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
