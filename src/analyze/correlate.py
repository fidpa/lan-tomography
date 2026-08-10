#!/usr/bin/env python3
"""correlate.py - hold symptom windows against the ping matrix and judge them.

WHAT IT DOES
    For every window in which the symptom occurred (waves.csv), work out which
    targets lost packets, and derive from the PATTERN which section of the
    network can account for it.

    The judgement is the point. Anyone can print a loss percentage; the useful
    step is "these targets failed and those did not, therefore the fault lies
    between X and Y".

THE TWO DISTINCTIONS THIS TOOL EXISTS TO PRESERVE
    1. "No loss" is a statement. "No data" is not. A window with no
       measurement returns None, never zero. Reporting an unmeasured window as
       clean is how a measurement campaign proves the wrong thing.

    2. "Not answering" is not "failed". A workstation is switched off after
       hours. A server is not. Which roles are allowed to be silent is a
       configuration decision, not a default - see OFFLINE_TOLERANT_ROLES.

INPUTS
    ping logs        <base>/ping/<label>-<date>.log[.zst]
    symptom windows  waves.csv - see docs/reference/log-formats.md. Produce it
                     from whatever records your symptom; this repository ships
                     one reference producer in src/contrib/.
    target matrix    config/targets.conf, for the roles

CONFIGURATION (environment or config/lan-tomography.conf)
    LT_BASE_DIR, LT_TARGETS, LT_OUTAGE_THRESHOLD_S, LT_PING_INTERVAL
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from version import read_version

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Send interval of the measurement; must match the probes.
PING_INTERVAL_S = float(os.environ.get("LT_PING_INTERVAL", "0.2"))

# How long an uninterrupted outage has to last before a target counts as
# affected.
#
# This number is empirical, and copying it without deriving your own is a
# mistake. It was raised to 2.5 s after the first hour of measurement, because
# Windows guests deprioritise ICMP: three of them dropped 1.2-2.6 s of echo
# replies individually and at different times while the physical targets
# answered continuously. At 1.0 s every one of those everyday hiccups would
# have produced a false verdict.
OUTAGE_THRESHOLD_S = float(os.environ.get("LT_OUTAGE_THRESHOLD_S", "2.5"))

# Roles for which "no reply in the entire window" is normal rather than an
# outage: workstations are off outside working hours. These do NOT enter the
# verdict - they only appear in the table.
OFFLINE_TOLERANT_ROLES = ("client-path", "vpn-path")

# Roles the verdict is derived from. Everything else appears in the table but
# deliberately carries no verdict:
#
#   uplink-ref  The uplink question is answered by the DIFFERENCE between
#               probes, not by a single-location verdict.
#   switch-ref  A switch answers ICMP from its management CPU, not from the
#               forwarding path. Under load it delays replies while forwarding
#               traffic perfectly - as a judging role that is a false-verdict
#               generator. See config/targets.conf.example.
#   wan-ref     Targets beyond the gateway can fail for reasons that have
#               nothing to do with this network. Use TWO of them at different
#               operators: one failing is that target, both failing together
#               is the way out.
#
# The list is still needed: without it, an outage in an unjudged role falls
# through every branch and lands in "no target had an outage" - a statement the
# table in the same output contradicts.
JUDGED_ROLES = ("symptom", "same-host", "other-host", "fabric-ref",
                "client-path", "hypervisor")

# Roles that also fall out of the "OUTAGE OUTSIDE THE JUDGEMENT MATRIX" branch
# and appear in the table only.
#
# The difference from switch-ref/wan-ref matters: those sit ON the path under
# investigation or at its edge, so their outage is meaningful even though it
# cannot carry a verdict. An external role measures a different network
# entirely. An outage there must not colour the alert subject, or it devalues
# every alert that follows.
EXTERNAL_ROLES = ("external-ref",)

RE_REPLY = re.compile(r"^\[(\d+\.\d+)\].*icmp_seq=(\d+).*time=")
RE_NOANSWER = re.compile(r"^\[(\d+\.\d+)\] no answer yet for icmp_seq=(\d+)")
# Unreachable lines carry their icmp_seq ("From <ip> icmp_seq=7 Destination
# Host Unreachable"). It must be parsed, so an unreachable phase counts as one
# contiguous burst instead of collapsing into a single lost packet. Lines
# without a seq (rare) fall back to -1.
RE_UNREACH = re.compile(
    r"^\[(\d+\.\d+)\](?:.*icmp_seq=(\d+))?.*(?:Unreachable|Time to live exceeded)")


def load_targets(path):
    """targets.conf -> {label: role}"""
    targets = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 3:
            targets[parts[1]] = parts[2]
    return targets


def log_files(directory, pattern):
    """Log files matching a pattern - uncompressed AND zstd-compressed.

    Completed days are archived compressed (about 1/15 the size); the current
    day stays open and uncompressed. Without this, a plain glob("*.log") would
    silently skip the archived days - the analysis would carry on with less
    data, with no message and no exit code. That is the quiet kind of wrong
    this whole repository is about.

    Sorted by the name WITHOUT ".zst", so chronological order holds regardless
    of which days happen to be compressed already.

    If a day exists in BOTH forms the uncompressed file wins and the .zst is
    skipped, otherwise the same day would count twice. That case is real:
    probes are mirrored with "rsync -az" WITHOUT --delete, so a day compressed
    on the probe comes back uncompressed and ends up next to its own archive.
    """
    directory = Path(directory)
    by_day = {}
    for path in [*directory.glob(pattern + ".zst"), *directory.glob(pattern)]:
        # Plain read after zst -> plain overwrites a zst hit.
        by_day[path.name[:-4] if path.suffix == ".zst" else path.name] = path
    return [by_day[name] for name in sorted(by_day)]


def read_log(path):
    """Contents of a log file, transparently including .zst.

    Deliberately via the zstd binary rather than the "zstandard" Python module:
    a probe host is often a machine you may touch once, and adding a package to
    it for a time-boxed investigation is the wrong kind of change. Failures
    become OSError so callers can keep their existing "except OSError:
    continue".
    """
    if path.suffix == ".zst":
        try:
            # Fixed argument list, no shell; PATH lookup is intended.
            done = subprocess.run(
                ["zstd", "-dcq", str(path)],
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            raise OSError(f"{path}: zstd decompression failed: {exc}") from exc
        return done.stdout.decode("utf-8", errors="replace")
    return path.read_text(errors="replace")


def analyse_window(ping_dir, label, start, end):
    """Packet loss of one target in the window [start, end].

    Returns a dict with sent/lost/max_gap_s - or None if there is no
    measurement data at all for the window.

    That distinction is load-bearing: "no loss" is a statement, "no data" is
    not one, and collapsing them produces a clean bill of health for a window
    nobody measured.
    """
    day_files = log_files(ping_dir, f"{label}-*.log")
    if not day_files:
        return None

    answered, missing, stamps = set(), {}, []

    for path in day_files:
        try:
            lines = read_log(path).splitlines()
        except OSError:
            continue
        for line in lines:
            m = RE_REPLY.match(line)
            if m:
                ts, seq = float(m.group(1)), int(m.group(2))
                if start <= ts <= end:
                    answered.add(seq)
                    stamps.append(ts)
                continue
            m = RE_NOANSWER.match(line) or RE_UNREACH.match(line)
            if m:
                ts = float(m.group(1))
                if start <= ts <= end:
                    seq = int(m.group(2)) if m.group(2) else -1
                    missing.setdefault(seq, ts)
                    stamps.append(ts)

    if not stamps:
        return None

    # A target that never answered once in the whole window is not "failed", it
    # is switched off. This mostly affects workstations outside working hours;
    # without the distinction, every overnight analysis would report them as a
    # total outage and skew the verdict.
    if not answered:
        return {"sent": len(missing), "lost": len(missing), "outage_s": 0.0,
                "max_gap_s": 0.0, "affected": False, "offline": True,
                "first": min(stamps), "last": max(stamps)}

    # Only sequences that were NEVER answered count as lost.
    #
    # This is the "no answer yet" pitfall, and it cost hours in both
    # directions. ping prints "no answer yet for icmp_seq=N" and then prints
    # the reply if it arrives late. Counting those lines as loss invents
    # outages; counting them as replies hides real ones.
    lost = {seq: ts for seq, ts in missing.items() if seq not in answered}

    # Longest UNINTERRUPTED run of lost sequence numbers. That is the actual
    # outage measure - scattered single losses come out as 0 here, which is
    # correct: individual dropped echo requests are normal.
    max_burst = 0
    if lost:
        ordered = sorted(lost)
        run = 1
        for prev, cur in zip(ordered, ordered[1:]):
            run = run + 1 if cur == prev + 1 else 1
            max_burst = max(max_burst, run)
        max_burst = max(max_burst, 1)

    # Largest gap between two logged lines - catches outages where ping stopped
    # writing anything at all, which no sequence-number analysis can see.
    reply_times = sorted(stamps)
    max_gap = 0.0
    for a, b in zip(reply_times, reply_times[1:]):
        max_gap = max(max_gap, b - a)

    outage_s = max_burst * PING_INTERVAL_S

    return {
        "sent": len(answered) + len(lost),
        "lost": len(lost),
        "outage_s": round(outage_s, 1),
        "max_gap_s": round(max_gap, 2),
        "affected": outage_s >= OUTAGE_THRESHOLD_S or max_gap >= OUTAGE_THRESHOLD_S,
        "offline": False,
        "first": reply_times[0],
        "last": reply_times[-1],
    }


def is_hit(role, r):
    """Does one result count as an outage?

    "offline" (not a single reply in the whole window) excuses only the
    offline-tolerant roles. Servers run around the clock: if one of those is
    silent for the whole window, that is a total outage and must count -
    otherwise the very worst network failure would produce a clean verdict.
    """
    if r["affected"]:
        return True
    return r.get("offline", False) and role not in OFFLINE_TOLERANT_ROLES


def verdict(results, targets, threshold=None):
    """Derive the causal layer from the loss pattern.

    Returns (verdict, explanation). The wording names the SECTION, not a
    device: this tool sees paths, and a path is not a culprit.
    """
    threshold = OUTAGE_THRESHOLD_S if threshold is None else threshold
    measured = {lab: r for lab, r in results.items() if r is not None}
    if not measured:
        return "UNCLEAR", "No ping data for this window."

    def lost_in(role):
        return [lab for lab, r in measured.items()
                if targets.get(lab) == role and is_hit(role, r)]

    symptom = lost_in("symptom")
    same_host = lost_in("same-host")
    other_host = lost_in("other-host")
    fabric = lost_in("fabric-ref")
    client = lost_in("client-path")
    hypervisor = lost_in("hypervisor")

    # Affected targets whose role enters none of the branches below. They must
    # not produce a verdict, but they must not produce a "clean" either - that
    # would contradict the table printed alongside it.
    unjudged = [lab for lab, r in measured.items()
                if targets.get(lab) not in JUDGED_ROLES
                and targets.get(lab) not in EXTERNAL_ROLES
                and is_hit(targets.get(lab), r)]

    # Client path first: if it fails while the servers stay clean among
    # themselves, the fault is between workstation and server room - a section
    # the other targets never touch.
    if client and not (symptom or same_host or other_host or hypervisor or unjudged):
        return ("CLIENT PATH",
                (f"The workstations ({', '.join(client)}) failed while the servers "
                 "did not - the fault is between workstation and server room "
                 "(floor switch, building cabling), not at the symptom host."))

    if not (symptom or same_host or other_host or fabric or client or hypervisor):
        if unjudged:
            named = ", ".join(f"{lab} ({targets.get(lab)})" for lab in unjudged)
            return ("OUTAGE OUTSIDE THE JUDGEMENT MATRIX",
                    (f"No target with a judging role failed, but {named} lost "
                     f"packets for more than {threshold}s. These roles deliberately "
                     "carry no verdict - the statement only arises from the "
                     "difference to the other probes (see table). Reporting a clean "
                     "network here would be wrong."))
        return ("NETWORK CLEAN",
                (f"No target had a contiguous outage of {threshold}s or more. The "
                 "network carried packets throughout the symptom window - the cause "
                 "lies above layer 3, in the application or the endpoint."))

    # What always decides is whether the path TO THE SYMPTOM HOST failed. If
    # another machine fails while the symptom host answers throughout, that is
    # a side finding and not an explanation.
    if not symptom:
        others = same_host + other_host + fabric + hypervisor + unjudged
        return ("NETWORK CLEAN",
                (f"The symptom host itself stayed reachable (no outage over "
                 f"{threshold}s), so a network problem does not explain this window. "
                 f"Side finding: outage on {', '.join(others)} - not part of the "
                 "symptom picture, but noted."))

    if other_host or fabric:
        return ("FABRIC",
                (f"The symptom host failed together with targets outside its own "
                 f"physical host ({', '.join(other_host + fabric)}) - points at the "
                 "switch, the cabling or the uplink."))

    # Measuring the hypervisor ITSELF separates two levels inside "host" that
    # are otherwise indistinguishable: if the host went down with its guests,
    # the fault is in front of it (its physical NIC or switch port); if it
    # stayed reachable while its guests lost packets, the fault is inside it.
    if same_host or hypervisor:
        if hypervisor:
            return ("HOST UPLINK",
                    (f"The symptom host failed, and the hypervisor itself "
                     f"({', '.join(hypervisor)}) failed with it - while targets on "
                     "other hardware stayed clean. The fault is IN FRONT OF the "
                     "hypervisor: its physical NIC or its switch port."))
        return ("HOST INTERNAL",
                (f"The symptom host failed together with its sibling guests "
                 f"({', '.join(same_host)}), but the hypervisor stayed reachable and "
                 "so did other hardware - the fault is INSIDE the host (virtual "
                 "switch, queue assignment), not on its uplink."))

    return ("GUEST SPECIFIC",
            ("Only the symptom guest failed; its siblings on the same host did not "
             "- points at that guest's virtual NIC or its queue."))


def read_waves(path):
    """Read the symptom windows. Format: docs/reference/log-formats.md."""
    windows = []
    with Path(path).open(newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                windows.append({
                    "start": float(row["start_epoch"]),
                    "end": float(row["end_epoch"]),
                    "count": int(row.get("count", 0) or 0),
                })
            except (KeyError, ValueError):
                continue
    return windows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ping-dir",
                    default=Path(os.environ.get("LT_BASE_DIR",
                                                "/var/log/lan-tomography")) / "ping")
    ap.add_argument("--targets",
                    default=os.environ.get("LT_TARGETS", REPO_ROOT / "config" / "targets.conf"))
    ap.add_argument("--waves",
                    default=Path(os.environ.get("LT_BASE_DIR",
                                                "/var/log/lan-tomography")) / "waves.csv")
    ap.add_argument("--pad", type=float, default=30.0,
                    help="seconds to extend each window on both sides (default: 30)")
    ap.add_argument("--version", action="version",
                    version=f"lan-tomography {read_version()}")
    args = ap.parse_args()

    try:
        targets = load_targets(args.targets)
    except OSError as exc:
        print(f"target matrix not readable: {exc}", file=sys.stderr)
        return 2

    try:
        windows = read_waves(args.waves)
    except OSError as exc:
        print(f"symptom windows not readable: {exc}", file=sys.stderr)
        print("See docs/reference/log-formats.md - waves.csv is an INPUT; "
              "produce it from whatever records your symptom.", file=sys.stderr)
        return 2

    if not windows:
        print("no symptom windows to analyse")
        return 0

    for w in windows:
        start, end = w["start"] - args.pad, w["end"] + args.pad
        results = {lab: analyse_window(args.ping_dir, lab, start, end)
                   for lab in targets}
        v, why = verdict(results, targets)

        when = datetime.fromtimestamp(w["start"], UTC).isoformat(timespec="seconds")
        print(f"\n=== {when}  ({w['count']} events) ===")
        print(f"VERDICT: {v}")
        print(f"  {why}")
        print(f"  {'target':<16}{'role':<14}{'sent':>8}{'lost':>8}{'outage':>9}{'max gap':>10}")
        for lab in sorted(results):
            r = results[lab]
            role = targets.get(lab, "?")
            if r is None:
                # Printed explicitly rather than skipped. An absent row reads
                # as "fine"; "no data" has to be visible to be corrected.
                print(f"  {lab:<16}{role:<14}{'NO DATA':>35}")
                continue
            flag = "  <-- affected" if is_hit(role, r) else ""
            state = "offline" if r.get("offline") else ""
            print(f"  {lab:<16}{role:<14}{r['sent']:>8}{r['lost']:>8}"
                  f"{r['outage_s']:>8.1f}s{r['max_gap_s']:>9.2f}s{flag}{state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
