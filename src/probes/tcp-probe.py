#!/usr/bin/env python3
"""tcp-probe.py - measure TCP connection establishment to configured services.

Adds the signal that ICMP cannot give you: the time until a TCP connection is
established (SYN -> SYN/ACK). Network devices treat ICMP differently from
ordinary traffic, and a path can look clean for ping while still losing
connections.

In the campaign this came from, the two genuinely decoupled: one outage took
the gateway away for 298 seconds while every LAN target stayed at zero loss.
Measuring only one of them would have produced a confident wrong answer either
way.

THIS IS NOT A PORT SCAN. One connection, to one known port, per target, at a
fixed slow interval. No data is sent and no application session is established;
the service sees an aborted connection attempt and nothing more. Each target
carries its own interval because some ports must be probed sparingly - see
config/tcp-targets.conf.example for the case that set that rule.

One process, one thread per target.

LOG FORMAT
    Deliberately close to the ping logs, with the Unix epoch as the shared
    reference so both series correlate without timezone arithmetic:

        [1785317745.940000] connect 192.0.2.20:445 ok time=1.23 ms
        [1785317745.940000] connect 192.0.2.20:445 failed err=timeout

    See docs/reference/log-formats.md.

CONFIGURATION (environment or config/lan-tomography.conf)
    LT_BASE_DIR      where measurement data goes
    LT_TCP_TARGETS   path to tcp-targets.conf
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from version import read_version

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_TARGETS = Path(os.environ.get("LT_TCP_TARGETS",
                                      REPO_ROOT / "config" / "tcp-targets.conf"))
DEFAULT_LOG_DIR = Path(os.environ.get("LT_BASE_DIR", "/var/log/lan-tomography")) / "tcp"

# When a connection attempt counts as failed. Deliberately short: a connect
# that takes longer than 1.5 s is useless for an interactive session anyway,
# and a long timeout would distort the series, because the thread is blocked
# for the duration and misses its next slot.
CONNECT_TIMEOUT_S = 1.5

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

log = logging.getLogger("tcp-probe")
_stop = threading.Event()


@dataclass(frozen=True)
class Target:
    """One target of the TCP matrix.

    Attributes:
        ip: target address.
        port: target port - a single, known service, never a range.
        interval: seconds between two measurements of this target.
        label: filename prefix of the log.
        role: role in the hypothesis separation, see targets.conf.
    """

    ip: str
    port: int
    interval: float
    label: str
    role: str


def load_targets(path: Path) -> list[Target]:
    """Read the measurement matrix.

    Line format: ``<IP> <port> <interval-s> <label> <role>``

    Incomplete lines are skipped with a warning rather than aborting: a typo in
    one line must not prevent the whole measurement, because the measurement is
    usually started in a hurry during an incident.
    """
    targets: list[Target] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 5:
            log.warning("%s:%d: incomplete line, skipped", path.name, lineno)
            continue
        try:
            targets.append(
                Target(
                    ip=parts[0],
                    port=int(parts[1]),
                    interval=float(parts[2]),
                    label=parts[3],
                    role=parts[4],
                )
            )
        except ValueError:
            log.warning("%s:%d: port or interval is not numeric, skipped",
                        path.name, lineno)
    return targets


def logfile_for(log_dir: Path, label: str) -> Path:
    """Path of today's file for one target: ``<label>-<YYYY-MM-DD>.log``.

    The date is LOCAL time, deliberately: the shell probes roll their files
    over with `date +%F`, which honours TZ, and a probe whose TCP files roll at
    a different moment than its ICMP files is a nuisance every time somebody
    correlates a night-time event.

    Set LT_TZ (default UTC) to control it, and set it the same on every probe.
    The line timestamps inside the file are epoch and unaffected by this.
    """
    local_now = datetime.now(UTC).astimezone()
    return log_dir / f"{label}-{local_now.strftime('%Y-%m-%d')}.log"


def write_header(path: Path, target: Target) -> None:
    """Write the header of a new daily file, matching ping-target.sh."""
    if path.exists():
        return
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    header = (
        "# lan-tomography TCP probe\n"
        f"# Target: {target.label} ({target.ip}:{target.port}), "
        f"role: {target.role}, interval: {target.interval}s\n"
        f"# Start: {now}, timeout: {CONNECT_TIMEOUT_S}s\n"
        "# Timestamps are Unix epoch (timezone-free)\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(header)


def probe_once(ip: str, port: int) -> tuple[float, str | None]:
    """Measure a single TCP connection establishment.

    Returns (milliseconds, failure reason). The reason is None on success.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT_S)
    start = time.perf_counter()
    try:
        sock.connect((ip, port))
        return (time.perf_counter() - start) * 1000.0, None
    except TimeoutError:
        return (time.perf_counter() - start) * 1000.0, "timeout"
    except ConnectionRefusedError:
        # Refused is a fast, clean answer from a live host. It is NOT a network
        # fault, and lumping it in with timeouts would turn a stopped service
        # into a phantom outage.
        return (time.perf_counter() - start) * 1000.0, "refused"
    except OSError as exc:
        reason = (exc.strerror or str(exc)).replace(" ", "_")
        return (time.perf_counter() - start) * 1000.0, reason
    finally:
        try:
            # SO_LINGER with timeout 0 -> RST instead of FIN. Stops TIME_WAIT
            # entries accumulating over days of probing.
            sock.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_LINGER,
                b"\x01\x00\x00\x00\x00\x00\x00\x00",
            )
        except OSError:
            pass
        sock.close()


def record_measurement(log_dir: Path, target: Target) -> None:
    """Take one measurement and append it to the daily file."""
    stamp = datetime.now(UTC).timestamp()
    elapsed_ms, err = probe_once(target.ip, target.port)

    where = f"{target.ip}:{target.port}"
    if err is None:
        line = f"[{stamp:.6f}] connect {where} ok time={elapsed_ms:.2f} ms\n"
    else:
        line = f"[{stamp:.6f}] connect {where} failed err={err}\n"

    path = logfile_for(log_dir, target.label)
    try:
        write_header(path, target)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError as exc:
        log.error("%s: log not writable: %s", target.label, exc)


def probe_loop(log_dir: Path, target: Target) -> None:
    """Measure one target continuously at its configured rate."""
    while not _stop.is_set():
        cycle_start = time.monotonic()
        record_measurement(log_dir, target)
        # Wait for the next slot rather than sleeping blindly - otherwise the
        # interval drifts by however long each measurement took, and a series
        # meant to align with other probes slowly stops aligning.
        rest = target.interval - (time.monotonic() - cycle_start)
        if rest > 0:
            _stop.wait(rest)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS,
                        help=f"path to the TCP matrix (default: {DEFAULT_TARGETS})")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR,
                        help=f"log directory (default: {DEFAULT_LOG_DIR})")
    parser.add_argument("--once", action="store_true",
                        help="one round per target, then exit (test/verification)")
    parser.add_argument("--version", action="version",
                        version=f"lan-tomography {read_version()}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stdout)
    args = parse_args(argv)

    try:
        targets = load_targets(args.targets)
    except OSError as exc:
        log.error("target matrix not readable: %s", exc)
        return EXIT_USAGE

    if not targets:
        log.error("no valid targets in %s", args.targets)
        return EXIT_USAGE

    try:
        args.log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error("cannot create log directory: %s", exc)
        return EXIT_ERROR

    log.info("TCP probe starting for %d targets:", len(targets))
    for target in targets:
        log.info("  %-14s %s:%-5d every %ss  (%s)",
                 target.label, target.ip, target.port, target.interval, target.role)

    if args.once:
        for target in targets:
            record_measurement(args.log_dir, target)
        log.info("single round complete (--once)")
        return EXIT_OK

    threads = [
        threading.Thread(target=probe_loop, args=(args.log_dir, target),
                         name=target.label, daemon=True)
        for target in targets
    ]
    for thread in threads:
        thread.start()

    try:
        # Watch the threads rather than joining them. A dead measurement thread
        # must take the whole service down so systemd restarts it - otherwise
        # the process stays "active (running)" while one target has silently
        # stopped being measured, which is the failure mode this repository
        # keeps running into.
        while all(thread.is_alive() for thread in threads):
            _stop.wait(5)
        log.error("at least one measurement thread exited unexpectedly")
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_OK
    finally:
        _stop.set()
        for thread in threads:
            thread.join(timeout=CONNECT_TIMEOUT_S + 1)


if __name__ == "__main__":
    sys.exit(main())
