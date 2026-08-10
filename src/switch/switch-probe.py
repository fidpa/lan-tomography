#!/usr/bin/env python3
"""switch-probe.py - sample a switch's per-port counters over SNMP.

WHAT THIS IS FOR
----------------
Pick the most central managed device you can reach - usually the switch every
other path crosses - and sample its interface counters on a schedule. The point
is not errors. It is DISCARDS.

The distinction decides where you look next. A representative reading from the
campaign this came from, over twenty days of uptime covering the whole
incident:

    ifInErrors, ifOutErrors, ifInDiscards ... 0 on ALL 59 interfaces
    ifOutDiscards .................. up to 4,434,273 on a single port

Zero transmission errors over three weeks says the cabling is fine; stop
looking there. OutDiscards are send-queue overruns - exactly the mechanism that
produces packet loss WITHOUT a link failure, without an event-log entry, and
without a ping matrix necessarily seeing anything. A small-buffer switch with
several 100-Mbit ports and old firmware is a plausible place for it.

The counters are cumulative. Whether the discards line up with your incident or
happened overnight during a backup is answered only by the DIFFERENCE between
two samples, which is why this is a collector and not a one-shot query.

READ THIS BEFORE TRUSTING A QUIET RESULT
----------------------------------------
An empty counter result is NOT an all-clear. During the campaign this came
from, SNMP counters were blind for 12 of 18 flood minutes - the agent simply
did not answer while the device was busy, and the gaps were not random
(p = 2.8e-14 against the flood windows). The device stops reporting precisely
when there is something to report.

So: "the switch shows nothing" means either nothing happened or the switch
could not tell you. Distinguish those before concluding anything. See
docs/explanation/pitfalls.md.

WHY A HAND-WRITTEN SNMP CLIENT
------------------------------
See src/lib/snmp.py. Short version: installing net-snmp on a production box for
a time-boxed investigation is the wrong kind of change, and the slice of
SNMPv2c needed here is small.

GetBulk rather than GetNext is not a detail: walking 59 interfaces costs 60
packets PER COLUMN with GetNext and three with GetBulk. At one run a minute
that is the difference between 7 and 420 packets per pass against somebody
else's production device.

INVOCATION
    switch-probe.py [--switch IP] [--community NAME] [--once]

CONFIGURATION (environment or config/lan-tomography.conf)
    LT_SWITCH_IP        switch to poll
    LT_SNMP_COMMUNITY   read-only community
    LT_BASE_DIR         where measurement data goes
    LT_SECRETS          file to read LT_SNMP_COMMUNITY from, if not in env
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import snmp
from version import read_version

SCRIPT_NAME = "switch-probe"

# Exit 75 (EX_TEMPFAIL) tells systemd this pass failed transiently. Paired with
# SuccessExitStatus=75 in the unit, a single unreachable poll does not leave the
# service stuck in "failed" - which would train the operator to ignore it.
EX_TEMPFAIL = 75

COLUMNS = {
    "descr":        [1, 3, 6, 1, 2, 1, 2, 2, 1, 2],
    "oper_status":  [1, 3, 6, 1, 2, 1, 2, 2, 1, 8],
    "speed":        [1, 3, 6, 1, 2, 1, 2, 2, 1, 5],
    "in_discards":  [1, 3, 6, 1, 2, 1, 2, 2, 1, 13],
    "in_errors":    [1, 3, 6, 1, 2, 1, 2, 2, 1, 14],
    "out_discards": [1, 3, 6, 1, 2, 1, 2, 2, 1, 19],
    "out_errors":   [1, 3, 6, 1, 2, 1, 2, 2, 1, 20],
    "in_octets":    [1, 3, 6, 1, 2, 1, 31, 1, 1, 1, 6],
    "out_octets":   [1, 3, 6, 1, 2, 1, 31, 1, 1, 1, 10],
}
COUNTER_KEYS = ("in_discards", "in_errors", "out_discards", "out_errors",
                "in_octets", "out_octets")

log = logging.getLogger(SCRIPT_NAME)


def read_secret(key: str) -> str | None:
    """Read one KEY=value line from LT_SECRETS without executing the file."""
    path = os.environ.get("LT_SECRETS")
    if not path:
        return None
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        return None
    return None


def snapshot(host: str, community: str) -> dict[int, dict]:
    sock = snmp.open_socket(timeout=4)
    try:
        data: dict[int, dict] = {}
        for name, base in COLUMNS.items():
            for idx, val in snmp.walk_column(sock, host, community, base).items():
                data.setdefault(idx, {})[name] = val
        return data
    finally:
        sock.close()


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(path)
    path.chmod(0o600)


def build_record(now: dict[int, dict], prev: dict, ts: float,
                 host: str) -> tuple[dict, list[str]]:
    """Turn two samples into one timeline record plus a list of notable events.

    Split out from main() so it can be tested without a switch.
    """
    prev_ts = prev.get("ts")
    prev_ports = prev.get("ports", {})

    ports_out: dict[str, dict] = {}
    events: list[str] = []

    for idx, cur in sorted(now.items()):
        key = str(idx)
        old = prev_ports.get(key)

        # Ports with no link and no counter movement are not written: they
        # halve the file without contributing. A port that gains link during
        # the campaign appears automatically, and the transition itself is
        # caught below via oper_status.
        if cur.get("oper_status") != 1:
            moved = old and any(
                (cur.get(k) or 0) != (old.get(k) or 0) for k in COUNTER_KEYS
            )
            if not moved and old and old.get("oper_status") != 1:
                continue

        entry = {k: cur.get(k) for k in ("descr", "oper_status", "speed")}

        if old and prev_ts:
            dt = ts - prev_ts
            deltas = {}
            for k in COUNTER_KEYS:
                a, b = old.get(k), cur.get(k)
                if a is None or b is None:
                    continue
                # Counter wrap or a switch reboot: discard the difference
                # rather than invent a spike. None here means "unknown",
                # which is a different thing from 0 and must stay different -
                # see the pitfall about "no data" not being "no loss".
                deltas[k] = b - a if b >= a else None
            entry["d"] = deltas
            entry["dt"] = round(dt, 1)

            if deltas.get("out_discards"):
                events.append(f"port {idx}: {deltas['out_discards']} out_discards")
            for k in ("in_errors", "out_errors", "in_discards"):
                if deltas.get(k):
                    events.append(f"port {idx}: {deltas[k]} {k}")
            if old.get("oper_status") != cur.get("oper_status"):
                events.append(f"port {idx}: link {old.get('oper_status')} -> "
                              f"{cur.get('oper_status')}")

        ports_out[key] = entry

    record = {
        "ts": datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds"),
        "switch": host,
        "interval_s": round(ts - prev_ts, 1) if prev_ts else None,
        "ports": ports_out,
    }
    return record, events


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--switch", default=os.environ.get("LT_SWITCH_IP"),
                    help="switch address (default: LT_SWITCH_IP)")
    ap.add_argument("--community",
                    default=os.environ.get("LT_SNMP_COMMUNITY") or read_secret("LT_SNMP_COMMUNITY"),
                    help="read-only community (default: LT_SNMP_COMMUNITY or LT_SECRETS)")
    ap.add_argument("--base-dir",
                    default=os.environ.get("LT_BASE_DIR", "/var/log/lan-tomography"))
    ap.add_argument("--version", action="version",
                    version=f"lan-tomography {read_version()}")
    args = ap.parse_args()

    logging.basicConfig(format="[%(asctime)s] [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S", level=logging.INFO)

    if not args.switch:
        log.error("no switch address: set LT_SWITCH_IP or pass --switch")
        return 2
    if not args.community:
        log.error("no SNMP community: set LT_SNMP_COMMUNITY or put it in LT_SECRETS")
        return 2

    base_dir = Path(args.base_dir)
    out_file = base_dir / "switch-timeline.jsonl"
    state_file = base_dir / ".switch-probe-state.json"

    try:
        base_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error("cannot create output directory: %s", exc)
        return 1

    try:
        now = snapshot(args.switch, args.community)
    except TimeoutError:
        # Transient: the switch is momentarily unreachable. At one run a minute
        # a single blip is meaningless and must not wedge the unit in "failed".
        log.warning("%s is not answering SNMP - pass skipped", args.switch)
        return EX_TEMPFAIL
    except OSError as exc:
        log.warning("SNMP error (%s) - pass skipped", exc)
        return EX_TEMPFAIL
    except (IndexError, ValueError) as exc:
        # Undecodable response is a real fault, not a network problem.
        log.error("SNMP response could not be decoded: %s", exc)
        return 1

    if not now:
        log.error("SNMP returned no interfaces - wrong community or OIDs?")
        return 1

    ts = time.time()
    record, events = build_record(now, load_state(state_file), ts, args.switch)

    try:
        with out_file.open("a") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError as exc:
        log.error("writing to %s failed: %s", out_file, exc)
        return 1

    save_state(state_file, {
        "ts": ts,
        "ports": {str(i): {k: v.get(k) for k in COUNTER_KEYS + ("oper_status",)}
                  for i, v in now.items()},
    })

    if events:
        log.warning("%d notable change(s): %s", len(events), "; ".join(events[:6]))
    else:
        log.info("%d interfaces read, no discards in the interval", len(now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
