#!/usr/bin/env python3
"""switch-report.py - hold switch port discards against the symptom windows.

`switch-probe.py` collects port counters on a schedule. That alone proves
nothing: a counter reading without a comparison is a number, not a statement.
This turns it into a finding.

WHY A BASELINE, AND NOT JUST "DELTA > 0"
    A switch discards packets in normal operation. On a gigabit-to-100-Mbit
    speed mismatch it happens constantly and nobody notices. "Packets were
    discarded during the symptom window" is therefore a worthless sentence.

    What can be used is the comparison against what is normal FOR THAT PORT. So
    every window is set against a baseline drawn from the surrounding period
    (default +/- 60 min, excluding the window itself), reported as a factor.
    Only a clear excursion above a port's own baseline is a finding.

    The baseline is a MEDIAN, not a mean: one outlying minute would otherwise
    raise the bar enough to hide the excursion you are looking for.

    The table carries each port's link speed for that reason - the caveat above
    is unusable without it. Unknown prints as "?", never as 0.

THREE POSSIBLE ANSWERS, ALL OF THEM USEFUL
    excursion         the switch discarded markedly more than usual during the
                      window - it is not exonerated
    within baseline   it discarded, but no more than it always does - the
                      window is not explained by this
    no discards       it carried everything it was given - which is a
                      falsification, and the most valuable of the three

AND A FOURTH ANSWER THAT IS NOT AN ANSWER
    no data. Returned as None, never as "no discards". SNMP counters go blind
    exactly when the device is busy - measured: missing for 12 of 18 flood
    minutes, and not randomly (p = 2.8e-14). Treating a gap as a zero converts
    the device's silence into its innocence.

INVOCATION
    switch-report.py [--timeline FILE] [--waves FILE] [--counter NAME]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from version import read_version

BASE = Path(os.environ.get("LT_BASE_DIR", "/var/log/lan-tomography"))

# How far either side of a symptom window counts as "during" it.
#
# This MUST be at least one sampling interval, or most windows fall between two
# samples and report "no data" - which then reads as a measurement failure when
# it is really a mismatched window. The shipped timer samples every minute;
# 5 minutes gives room for a slower one.
WAVE_WINDOW = timedelta(minutes=5)
# How far either side the baseline is drawn from.
BASELINE_WINDOW = timedelta(minutes=60)

# An excursion must clear both bars: enough packets to matter at all, and
# enough of a multiple to be distinguishable from noise. Either alone produces
# findings nobody can act on.
SPIKE_MIN_PACKETS = 100
SPIKE_FACTOR = 5.0


def load_timeline(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            rec["_dt"] = datetime.fromisoformat(rec["ts"])
        except (ValueError, KeyError):
            continue
        records.append(rec)
    return sorted(records, key=lambda r: r["_dt"])


def load_waves(path: Path) -> list[datetime]:
    """Read waves.csv and return the midpoint of each window."""
    waves = []
    import csv
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                start = float(row["start_epoch"])
                end = float(row["end_epoch"])
            except (KeyError, ValueError):
                continue
            waves.append(datetime.fromtimestamp((start + end) / 2).astimezone())
    return waves


def port_deltas(rec: dict, counter: str) -> dict[str, int]:
    """Per-port delta of one counter, skipping unknowns.

    A delta of None means the counter went backwards - a wrap or a reboot. It
    is skipped rather than read as 0, because "unknown" and "nothing happened"
    are different statements.
    """
    out = {}
    for port, data in rec.get("ports", {}).items():
        val = (data.get("d") or {}).get(counter)
        if isinstance(val, int):
            out[port] = val
    return out


def describe_port(records: list[dict], port: str) -> str:
    """Last known link speed of a port, for the table.

    The header of this file tells the reader that discards are ordinary at a
    gigabit-to-100-Mbit step. That caveat cannot be applied without the number
    it applies to - and `switch-probe.py` has been collecting `ifSpeed` on
    every sample all along, into every line of the timeline.

    Read backwards, because the useful figure is the most recent one the agent
    actually returned: a sample taken while the device was busy can carry the
    port and omit its speed.

    Unknown stays unknown. A speed the agent never gave is printed as "?", not
    as 0 - the same rule the deltas follow one function down, and for the same
    reason: a port of unknown speed and a port at a standstill are different
    statements.
    """
    for rec in reversed(records):
        data = rec.get("ports", {}).get(port)
        if not data:
            continue
        speed = data.get("speed")
        if not isinstance(speed, int) or speed <= 0:
            continue
        # ifSpeed is a 32-bit gauge and saturates: anything at or above
        # 4.295 Gbit/s reports this exact value and the real figure is not in
        # this data. Say so rather than print a wrong number.
        if speed >= 4_294_967_295:
            return ">4 Gbit"
        if speed >= 1_000_000_000:
            return f"{speed / 1_000_000_000:g} Gbit"
        return f"{speed // 1_000_000} Mbit"
    return "?"


def analyse_wave(records: list[dict], wave: datetime, counter: str) -> dict | None:
    """Hold one window against its own surroundings.

    Returns None when there is no data for the window - a different statement
    from "no discards", and it must stay distinguishable.
    """
    in_wave = [r for r in records if abs(r["_dt"] - wave) <= WAVE_WINDOW]
    if not in_wave:
        return None

    base = [r for r in records
            if abs(r["_dt"] - wave) <= BASELINE_WINDOW
            and abs(r["_dt"] - wave) > WAVE_WINDOW]

    wave_sum: dict[str, int] = {}
    for rec in in_wave:
        for port, val in port_deltas(rec, counter).items():
            wave_sum[port] = wave_sum.get(port, 0) + val

    base_per_port: dict[str, list[int]] = {}
    for rec in base:
        deltas = port_deltas(rec, counter)
        for port in set(list(deltas) + list(wave_sum)):
            base_per_port.setdefault(port, []).append(deltas.get(port, 0))

    findings = []
    for port, total in sorted(wave_sum.items(), key=lambda kv: -kv[1]):
        samples = base_per_port.get(port, [])
        med = statistics.median(samples) if samples else 0.0
        # The window spans several samples - normalise to a per-sample rate.
        per_min = total / max(len(in_wave), 1)
        if per_min == 0:
            factor = 0.0          # nothing discarded is not "infinitely more"
        elif med > 0:
            factor = per_min / med
        else:
            factor = float("inf")  # discards against a baseline of zero
        spike = per_min >= SPIKE_MIN_PACKETS and factor >= SPIKE_FACTOR
        findings.append({"port": port, "total": total, "per_min": per_min,
                         "baseline": med, "factor": factor, "spike": spike,
                         "samples": len(samples)})

    # Missing samples inside the window are the whole D2 problem: the agent
    # stops answering while the device is busy. Count them, because "no
    # discards recorded" and "the switch was not answering" must not read the
    # same.
    gaps = 0
    intervals = [r.get("interval_s") for r in in_wave if isinstance(r.get("interval_s"), (int, float))]
    if intervals:
        typical = statistics.median(intervals)
        gaps = sum(1 for iv in intervals if iv > typical * 1.5)

    return {"records": len(in_wave), "baseline_records": len(base),
            "gaps": gaps, "findings": findings}


def verdict(result: dict) -> tuple[str, str]:
    spikes = [f for f in result["findings"] if f["spike"]]
    discarded = [f for f in result["findings"] if f["total"] > 0]

    if result.get("gaps") and not spikes:
        return ("INCONCLUSIVE",
                (f"{result['gaps']} sampling gap(s) inside the window - the "
                 "switch was not answering for part of it. Whatever it did "
                 "then is not in this data, so neither a clean nor a guilty "
                 "reading is available."))
    if spikes:
        ports = ", ".join(f"port {f['port']}" for f in spikes)
        return ("EXCURSION",
                (f"{ports} discarded markedly more during the window than it "
                 "normally does - the switch is not exonerated."))
    if discarded:
        return ("WITHIN BASELINE",
                ("The switch discarded packets, but no more than it does "
                 "anyway - this window is not explained by it."))
    return ("NO DISCARDS",
            ("The switch discarded nothing during the window - it carried "
             "everything it was given."))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--timeline", type=Path, default=BASE / "switch-timeline.jsonl")
    ap.add_argument("--waves", type=Path, default=BASE / "waves.csv")
    ap.add_argument("--counter", default="out_discards",
                    help="which counter to examine (default: out_discards)")
    ap.add_argument("--window", type=float, default=None,
                    help="minutes either side of a window counting as 'during' "
                         "it. Must be >= one sampling interval (default: 5)")
    ap.add_argument("--version", action="version",
                    version=f"lan-tomography {read_version()}")
    args = ap.parse_args()

    global WAVE_WINDOW
    if args.window is not None:
        WAVE_WINDOW = timedelta(minutes=args.window)

    try:
        records = load_timeline(args.timeline)
    except OSError as exc:
        print(f"timeline not readable: {exc}", file=sys.stderr)
        return 2
    try:
        waves = load_waves(args.waves)
    except OSError as exc:
        print(f"symptom windows not readable: {exc}", file=sys.stderr)
        print("See docs/reference/log-formats.md - waves.csv is an INPUT.",
              file=sys.stderr)
        return 2

    if not records:
        print("no usable records in the timeline", file=sys.stderr)
        return 1
    if not waves:
        print("no symptom windows to analyse")
        return 0

    print(f"# Switch report ({args.counter})\n")
    print(f"Timeline: `{args.timeline}`, {len(records)} samples")
    print(f"Windows:  `{args.waves}`, {len(waves)}\n")

    for wave in waves:
        print(f"## {wave.isoformat(timespec='seconds')}\n")
        result = analyse_wave(records, wave, args.counter)
        if result is None:
            # Said explicitly. A silently omitted window reads as a clean one,
            # and the whole point of this tool is the difference.
            print("**NO DATA** - the switch was not sampled during this window.")
            print("\n> That is not an all-clear. The agent stops answering while "
                  "the device is busy, which is exactly when a window matters.\n")
            continue

        v, why = verdict(result)
        print(f"**{v}** - {why}\n")
        if result["findings"]:
            print("| port | speed | total | per sample | baseline (median) | factor |")
            print("|---|---|---|---|---|---|")
            for f in result["findings"][:8]:
                factor = "inf" if f["factor"] == float("inf") else f"{f['factor']:.1f}x"
                mark = "  **<-**" if f["spike"] else ""
                print(f"| {f['port']} | {describe_port(records, f['port'])} | "
                      f"{f['total']} | {f['per_min']:.1f} | "
                      f"{f['baseline']:.1f} | {factor}{mark} |")
        print(f"\n_{result['records']} samples in window, "
              f"{result['baseline_records']} in baseline._\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
