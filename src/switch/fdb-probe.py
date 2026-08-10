#!/usr/bin/env python3
"""fdb-probe.py - read a switch's forwarding database and log MAC movements.

WHAT THIS IS FOR
----------------
The forwarding database (FDB) says which port the switch last saw each MAC
address on. Sampling it repeatedly turns that into a movement log.

A MAC that keeps changing ports is the single clearest fingerprint of a
forwarding loop: the same frame arrives from two directions, so the switch
relearns the address back and forth. In the campaign this came from, one
address pair moved between two ports 113 times, and that is what turned "there
might be a loop" into "the return path is port 25".

WHAT A MOVEMENT DOES NOT PROVE
------------------------------
It does not name a culprit. While a loop is running, the switch learns EVERY
sender's MAC on the return port - including the measuring machine's own. A MAC
appearing at the return port is a passenger, not a cause.

This bears repeating because it is the most tempting wrong inference this tool
enables. Establish device identity from LLDP, a TLS certificate or an HTTP
banner. Never from the MAC OUI: the vendor is a hint, not an identity.

New and disappearing addresses are deliberately NOT reported. A MAC ages out of
the table constantly in normal operation; reporting that would flood the file
and bury the finding.

READ THIS BEFORE TRUSTING A QUIET RESULT
----------------------------------------
See src/switch/switch-probe.py - the same warning applies. An agent that stops
answering while the device is busy produces silence, not an all-clear.

INVOCATION
    fdb-probe.py                 poll once and log movements
    fdb-probe.py --report        summarise what has been logged so far
    fdb-probe.py --dry-run       poll and print, write nothing

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
import socket
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import snmp
from version import read_version

SCRIPT_NAME = "fdb-probe"

# dot1qTpFdbPort - the 802.1Q bridge table. The last six arcs of each OID are
# the MAC address; the value is the bridge port number.
OID_FDB = [1, 3, 6, 1, 2, 1, 17, 7, 1, 2, 2, 1, 2]

SNMP_TIMEOUT = 4
EX_TEMPFAIL = 75

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


def walk_fdb(sock: socket.socket, host: str, community: str,
             reps: int = 40) -> dict[str, int]:
    """Read the whole MAC table. Returns {"aa:bb:cc:dd:ee:ff": port}.

    GetBulk rather than GetNext: for around a hundred entries that is three
    packets instead of a hundred against a production device.

    The MAC comes out of the OID, not out of the value - the value is the port
    number.
    """
    out: dict[str, int] = {}
    for oid, port in snmp.walk(sock, host, community, OID_FDB, reps).items():
        if not isinstance(port, int):
            continue
        mac = ":".join(f"{b:02x}" for b in oid[-6:])
        out[mac] = port
    return out


def find_flaps(prev: dict[str, int], now: dict[str, int]) -> list[dict[str, Any]]:
    """Compare two table states and report port changes.

    Only addresses present in BOTH samples with a changed port count. New and
    vanished addresses are normal ageing, not flapping.
    """
    flaps = []
    for mac, port in sorted(now.items()):
        old = prev.get(mac)
        if old is not None and old != port:
            flaps.append({"mac": mac, "from": old, "to": port})
    return flaps


def load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(path)
    path.chmod(0o600)


def report(log_file: Path) -> int:
    """Summarise the movement log without polling anything."""
    if not log_file.is_file():
        log.error("no movement log at %s", log_file)
        return 1

    macs: Counter[str] = Counter()
    pairs: Counter[tuple[int, int]] = Counter()
    total = 0
    first = last = None

    for line in log_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        first = first or rec.get("ts")
        last = rec.get("ts")
        for f in rec.get("flaps", []):
            total += 1
            macs[f["mac"]] += 1
            pairs[tuple(sorted((f["from"], f["to"])))] += 1

    print(f"movements logged : {total}")
    print(f"window           : {first} .. {last}")
    print(f"distinct MACs    : {len(macs)}")
    print()
    print("most active addresses (a passenger, not a culprit - see header):")
    for mac, n in macs.most_common(10):
        print(f"  {mac}  {n}")
    print()
    print("most active port pairs (this is where a loop shows itself):")
    for (a, b), n in pairs.most_common(10):
        print(f"  {a} <-> {b}  {n}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", action="store_true",
                    help="summarise logged movements instead of polling")
    ap.add_argument("--dry-run", action="store_true",
                    help="poll and print, write nothing")
    ap.add_argument("--switch", default=os.environ.get("LT_SWITCH_IP"))
    ap.add_argument("--community",
                    default=os.environ.get("LT_SNMP_COMMUNITY") or read_secret("LT_SNMP_COMMUNITY"))
    ap.add_argument("--base-dir",
                    default=os.environ.get("LT_BASE_DIR", "/var/log/lan-tomography"))
    ap.add_argument("--version", action="version",
                    version=f"lan-tomography {read_version()}")
    args = ap.parse_args()

    logging.basicConfig(format="[%(asctime)s] [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S", level=logging.INFO)

    base_dir = Path(args.base_dir)
    log_file = base_dir / "fdb-flapping.jsonl"
    state_file = base_dir / ".fdb-probe-state.json"

    if args.report:
        return report(log_file)

    if not args.switch:
        log.error("no switch address: set LT_SWITCH_IP or pass --switch")
        return 2
    if not args.community:
        log.error("no SNMP community: set LT_SNMP_COMMUNITY or put it in LT_SECRETS")
        return 2

    if not args.dry_run:
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.error("cannot create output directory: %s", exc)
            return 1

    sock = snmp.open_socket(timeout=SNMP_TIMEOUT)
    try:
        now = walk_fdb(sock, args.switch, args.community)
    except TimeoutError:
        log.warning("%s is not answering SNMP - pass skipped", args.switch)
        return EX_TEMPFAIL
    except OSError as exc:
        log.warning("SNMP error (%s) - pass skipped", exc)
        return EX_TEMPFAIL
    except (IndexError, ValueError) as exc:
        log.error("SNMP response could not be decoded: %s", exc)
        return 1
    finally:
        sock.close()

    if not now:
        log.error("forwarding database is empty - wrong community or OIDs?")
        return 1

    prev = load_state(state_file)
    flaps = find_flaps(prev.get("macs", {}), now)

    if args.dry_run:
        print(f"{len(now)} addresses in the table, {len(flaps)} movement(s) "
              f"against the previous state")
        for f in flaps:
            print(f"  {f['mac']}: port {f['from']} -> {f['to']}")
        return 0

    if flaps:
        record = {
            "ts": datetime.fromtimestamp(time.time(), UTC).isoformat(timespec="seconds"),
            "switch": args.switch,
            "macs_total": len(now),
            "flaps": flaps,
        }
        try:
            with log_file.open("a") as fh:
                fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        except OSError as exc:
            log.error("writing to %s failed: %s", log_file, exc)
            return 1
        log.warning("%d address(es) changed port: %s", len(flaps),
                    "; ".join(f"{f['mac']} {f['from']}->{f['to']}" for f in flaps[:6]))
    else:
        log.info("%d addresses in the table, no movement", len(now))

    save_state(state_file, {"ts": time.time(), "macs": now})
    return 0


if __name__ == "__main__":
    sys.exit(main())
