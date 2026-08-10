#!/usr/bin/env python3
"""arp-anomalies.py - find IP conflicts and ARP oddities in a capture.

WHY
    An IP conflict - two devices answering for the same address - produces
    exactly the damage pattern of an intermittent fault: sporadic, simultaneous
    session drops with no visible infrastructure outage. Both devices answer,
    the session falls apart, and every device involved reports itself healthy.

    The ARP traffic is already in the L2 capture ring. This reads it.

WHAT IT LOOKS FOR
    * addresses answered from MORE THAN ONE MAC - the conflict itself
    * MACs claiming many addresses - normal for routers and proxy ARP,
      notable on a workstation
    * gratuitous ARP in series - an address announcement, often a precursor
    * the loudest ARP senders, because a storm has a source

WHAT A FINDING HERE IS AND IS NOT
    A conflict is a strong, actionable finding: it explains drops directly.

    Everything else on this page is a lead, not a verdict. In particular, do not
    read a device's identity off its MAC prefix - the vendor is a hint. Confirm
    from LLDP, a TLS certificate or an HTTP banner before naming anything.

    And note what this cannot see: it reads captured ARP only. A conflict on a
    segment your probe does not sit on leaves no trace here at all.

INVOCATION
    arp-anomalies.py [--pcap-dir DIR] [--markdown]

CONFIGURATION
    LT_BASE_DIR   default capture location ($LT_BASE_DIR/l2)
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from version import read_version

# A tcpdump ARP reply line:
#   21:11:51.137960 ARP, Reply 192.0.2.5 is-at aa:bb:cc:00:00:01, length 46
RE_REPLY = re.compile(
    r"^(\d\d:\d\d:\d\d\.\d+) ARP, Reply (\d+\.\d+\.\d+\.\d+) is-at "
    r"([0-9a-f:]{17})", re.IGNORECASE)

# A request whose sender and target address are identical is gratuitous ARP.
RE_REQUEST = re.compile(
    r"^(\d\d:\d\d:\d\d\.\d+) ARP, Request who-has (\d+\.\d+\.\d+\.\d+) tell "
    r"(\d+\.\d+\.\d+\.\d+)", re.IGNORECASE)

# Routers and switches legitimately claim many addresses (proxy ARP, VRRP).
# Above this count a MAC is reported for information only, not as a finding.
MULTI_IP_INFO_THRESHOLD = 5


def read_pcaps(pcap_dir: Path):
    """Run tcpdump over every capture file and yield its lines."""
    files = sorted(pcap_dir.glob("*.pcap*"))
    if not files:
        sys.exit(f"no capture files in {pcap_dir}")

    for path in files:
        try:
            # Fixed argument list, no shell; PATH lookup is intended.
            out = subprocess.run(
                ["tcpdump", "-r", str(path), "-n", "arp"],
                capture_output=True, text=True, timeout=300, check=False)
        except (subprocess.SubprocessError, OSError) as exc:
            # Reported, not swallowed: a capture that cannot be read is a gap
            # in the evidence, and a silent gap reads as "nothing found".
            print(f"WARNING: {path.name} not readable ({exc})", file=sys.stderr)
            continue
        for line in out.stdout.splitlines():
            yield path.name, line


def analyse(lines) -> dict:
    """Split out from main() so it can be tested without a capture."""
    ip_to_macs: dict[str, set[str]] = collections.defaultdict(set)
    mac_to_ips: dict[str, set[str]] = collections.defaultdict(set)
    gratuitous: collections.Counter[str] = collections.Counter()
    requests_by_sender: collections.Counter[str] = collections.Counter()
    total = 0

    for line in lines:
        m = RE_REPLY.match(line)
        if m:
            total += 1
            _, ip, mac = m.groups()
            ip_to_macs[ip].add(mac.lower())
            mac_to_ips[mac.lower()].add(ip)
            continue

        m = RE_REQUEST.match(line)
        if m:
            total += 1
            _, target, sender = m.groups()
            requests_by_sender[sender] += 1
            # Asking for your own address announces it.
            if target == sender:
                gratuitous[sender] += 1

    return {
        "total": total,
        "conflicts": {ip: macs for ip, macs in ip_to_macs.items() if len(macs) > 1},
        "gratuitous": gratuitous,
        "requests_by_sender": requests_by_sender,
        "multi_ip": {mac: ips for mac, ips in mac_to_ips.items()
                     if len(ips) >= MULTI_IP_INFO_THRESHOLD},
    }


def report(result: dict, source: Path) -> None:
    print("# ARP analysis\n")
    print(f"Source: `{source}`, {result['total']} ARP packets read\n")

    print("## IP conflicts (one address answered from several MACs)\n")
    if result["conflicts"]:
        print("| address | MACs |")
        print("|---|---|")
        for ip, macs in sorted(result["conflicts"].items()):
            print(f"| **{ip}** | {', '.join(sorted(macs))} |")
        print("\n> A conflict explains sporadic drops directly - both devices "
              "answer, and the session falls apart. This is the one finding on "
              "this page that is a verdict rather than a lead.")
    else:
        print("_None._ Every observed address was answered from exactly one MAC.")
        print("\n> Note what that does and does not mean: no conflict was VISIBLE "
              "in this capture. A conflict on a segment this probe does not sit "
              "on leaves no trace here.")

    print("\n## Gratuitous ARP (address announcements)\n")
    if result["gratuitous"]:
        print("| sender | count |")
        print("|---|---|")
        for ip, n in result["gratuitous"].most_common(10):
            print(f"| {ip} | {n} |")
        print("\n> Occasional is normal (boot, failover). In series it points at "
              "an address change, or at two devices claiming one address.")
    else:
        print("_None._")

    print("\n## Loudest ARP senders\n")
    print("| sender | requests |")
    print("|---|---|")
    for ip, n in result["requests_by_sender"].most_common(8):
        print(f"| {ip} | {n} |")

    if result["multi_ip"]:
        print("\n## MACs claiming many addresses (router or proxy ARP, usually normal)\n")
        for mac, ips in sorted(result["multi_ip"].items(), key=lambda kv: -len(kv[1])):
            print(f"- `{mac}`: {len(ips)} addresses")


def main() -> int:
    default_dir = Path(os.environ.get("LT_BASE_DIR", "/var/log/lan-tomography")) / "l2"
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcap-dir", type=Path, default=default_dir)
    ap.add_argument("--version", action="version",
                    version=f"lan-tomography {read_version()}")
    args = ap.parse_args()

    if not args.pcap_dir.is_dir():
        print(f"not a directory: {args.pcap_dir}", file=sys.stderr)
        return 2
    if not subprocess.run(["which", "tcpdump"], capture_output=True,
                          check=False).returncode == 0:
        print("tcpdump not found - it is needed to read the captures",
              file=sys.stderr)
        return 2

    result = analyse(line for _, line in read_pcaps(args.pcap_dir))
    report(result, args.pcap_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
