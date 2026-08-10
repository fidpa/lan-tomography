#!/usr/bin/env python3
"""evtx-peek.py - judge a Windows event log over SMB without transferring it.

WHAT THIS IS FOR
----------------
You want to know whether a Windows event log is worth looking at. Two questions
decide that: HOW FAR BACK DOES IT REACH, and WHAT IS IN IT. Both can be
answered without moving the file.

That matters because the files are large. In the campaign this came from, the
logs on the terminal server ran to 314 MB. Pulling one across a network you are
currently measuring is not a neutral act - it is a multi-hundred-megabyte
transfer over the link whose behaviour is under investigation, and it competes
with the very traffic you are trying to characterise.

`smbclient get` can only fetch whole files. Impacket issues SMB2 READ with an
offset and so reads individual ranges. An EVTX file is built for it: a 4096-byte
file header, then independent 64 KB chunks of ~100 records each. A dozen chunks
- 768 KB - is enough for a judgement.

TWO THINGS THAT DECIDE HOW TO READ THE OUTPUT
---------------------------------------------
**The file is a ring buffer.** Chunk 0 is NOT the oldest. Once the log has
wrapped, the newest record sits somewhere in the middle of the file with the
oldest immediately after it. Reading the first and last chunk and subtracting
therefore gives a coverage span that can be wrong by the entire retention
period. `--coverage` locates the wrap point by bisection over the record
numbers and reports the actual span, at a cost of ~log2(chunks) reads.

**Timestamps in evtx are UTC.** This tool converts to `LT_TZ` (default UTC),
the same setting the probes use, so that an event log line and a pktrate line
can be put side by side. If you set `LT_TZ` on the probes, set it here too -
the failure mode is a correlation that looks convincing and is off by an hour.

WHAT THIS DOES NOT DO
---------------------
It samples. Event IDs and message patterns come from evenly spaced chunks, not
from the whole file, so a rare event can be missed entirely. This answers
"is this log worth fetching", not "did event X occur". For the second question,
fetch the file.

INVOCATION
    evtx-peek.py [--host HOST] 'Security.evtx'
    evtx-peek.py [--host HOST] 'Security.evtx' --coverage
    evtx-peek.py [--host HOST] --list

    The `%4` in channel names is literally part of the filename:
    'Microsoft-Windows-Ntfs%4Operational.evtx'

REQUIREMENTS
    impacket and python-evtx (requirements.txt). The network-side tools in this
    repository need neither - this is the one place with dependencies.

    The account needs read access to the administrative share `C$`. That is a
    privileged credential; keep it in LT_SECRETS, not in the config file.

CONFIGURATION (environment or config/lan-tomography.conf)
    LT_SMB_HOST         Windows host to read from
    LT_SMB_DOMAIN       domain or workgroup (default: empty, i.e. local account)
    LT_TZ               timezone for displayed timestamps (default: UTC)
    LT_SECRETS          file holding LT_SMB_USER and LT_SMB_PASSWORD
"""

from __future__ import annotations

import argparse
import collections
import logging
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from Evtx.Evtx import ChunkHeader
from impacket.smbconnection import SessionError, SMBConnection

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from version import read_version

SCRIPT_NAME = "evtx-peek"

REMOTE_LOG_DIR = "Windows\\System32\\winevt\\Logs"

# EVTX geometry (Windows XML Event Log, libyal specification)
EVTX_HEADER_SIZE = 4096
EVTX_CHUNK_SIZE = 65536

# SMB2 CREATE: read only, but permit every kind of sharing. Without the full
# share mode, Windows answers a request for an actively written event log with
# STATUS_SHARING_VIOLATION - which is to say, the busiest and most interesting
# log is exactly the one that refuses to open.
FILE_READ_DATA = 0x0001
FILE_READ_ATTRIBUTES = 0x0080
SHARE_ALL = 0x0007

DEFAULT_SAMPLES = 6
LOGSTRING_RE = re.compile(r'<Data Name="LogString">(.*?)</Data>', re.DOTALL)
EVENTID_RE = re.compile(r"<EventID[^>]*>(\d+)</EventID>")
PROVIDER_RE = re.compile(r'<Provider Name="([^"]+)"')

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

log = logging.getLogger(SCRIPT_NAME)


class ChunkUnreadable(Exception):
    """A chunk could not be read as an EVTX chunk (empty or overwritten)."""


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


def display_tz() -> ZoneInfo:
    """Timezone for displayed timestamps, from LT_TZ.

    Falls back to UTC on an unknown zone name rather than failing: a typo in
    LT_TZ should not cost a measurement window, and UTC is stated in the output
    header so the reader can see which one applied.
    """
    name = os.environ.get("LT_TZ", "UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown LT_TZ %r, using UTC", name)
        return ZoneInfo("UTC")


def connect(host: str, domain: str) -> SMBConnection:
    """Open an authenticated SMB session.

    Args:
        host: Windows host to connect to.
        domain: Domain or workgroup; empty for a local account.

    Returns:
        A logged-in connection.

    Raises:
        SessionError: On SMB errors, including bad credentials.
        KeyError: If LT_SMB_USER or LT_SMB_PASSWORD is not available.
    """
    user = os.environ.get("LT_SMB_USER") or read_secret("LT_SMB_USER")
    password = os.environ.get("LT_SMB_PASSWORD") or read_secret("LT_SMB_PASSWORD")
    if not user or not password:
        raise KeyError("LT_SMB_USER / LT_SMB_PASSWORD (environment or LT_SECRETS)")
    conn = SMBConnection(host, host)
    conn.login(user, password, domain)
    return conn


class RemoteEvtx:
    """Read access to one remote EVTX file via SMB2 range reads.

    The file is never transferred as a whole. Each access reads exactly one
    64 KB chunk.

    Attributes:
        size: File size in bytes.
        chunk_count: Number of complete chunks.
    """

    def __init__(self, filename: str, host: str, domain: str) -> None:
        """Open the file on `C$` read-only.

        Args:
            filename: Filename in the winevt log directory, e.g.
                `Security.evtx`.
            host: Windows host to connect to.
            domain: Domain or workgroup.

        Raises:
            SessionError: On SMB errors (login, missing file, locked file).
            KeyError: If the credentials are not available.
        """
        self._conn = connect(host, domain)
        self._tid = self._conn.connectTree("C$")
        self._fid = self._conn.openFile(
            self._tid,
            f"{REMOTE_LOG_DIR}\\{filename}",
            desiredAccess=FILE_READ_DATA | FILE_READ_ATTRIBUTES,
            shareMode=SHARE_ALL,
        )
        self.size: int = int(self._conn.queryInfo(self._tid, self._fid)["EndOfFile"])
        usable = self.size - EVTX_HEADER_SIZE
        self.chunk_count: int = max(0, usable // EVTX_CHUNK_SIZE)

    def records(self, chunk_no: int) -> list:
        """Read one chunk and return its records.

        Args:
            chunk_no: Zero-based chunk number.

        Returns:
            The chunk's records in write order.

        Raises:
            ChunkUnreadable: If the chunk is empty or unparsable.
        """
        offset = EVTX_HEADER_SIZE + chunk_no * EVTX_CHUNK_SIZE
        buf = self._conn.readFile(self._tid, self._fid, offset, EVTX_CHUNK_SIZE)
        try:
            found = list(ChunkHeader(buf, 0).records())
        except Exception as exc:  # python-evtx raises bare Exceptions
            raise ChunkUnreadable(str(exc)) from exc
        if not found:
            raise ChunkUnreadable("no records")
        return found

    def close(self) -> None:
        """Close the file and the SMB session."""
        try:
            self._conn.closeFile(self._tid, self._fid)
        finally:
            self._conn.logoff()


def local_time(record, tz: ZoneInfo) -> datetime:
    """Return a record's timestamp in the display timezone.

    Args:
        record: Record from python-evtx; its timestamp is naive UTC.
        tz: Target timezone.

    Returns:
        An aware timestamp.
    """
    return record.timestamp().replace(tzinfo=UTC).astimezone(tz)


def find_ring_head(evtx: RemoteEvtx) -> tuple[int, int]:
    """Locate the wrap point of the ring buffer by bisection.

    Record numbers rise monotonically from chunk to chunk but drop exactly
    once - that is where the newest record ends and the oldest begins. The
    bisection needs ~log2(chunks) range reads instead of reading every chunk.

    Args:
        evtx: An open remote file.

    Returns:
        Chunk number of the newest and of the oldest record. If the file has
        not wrapped yet, that is `(last, 0)`.

    Raises:
        ChunkUnreadable: If even the first chunk is unreadable.
    """
    first_rid = evtx.records(0)[0].record_num()

    def belongs_to_newest_run(chunk_no: int) -> bool:
        """Does this chunk still belong to the run that starts at chunk 0?

        False for the older chunks after the wrap point AND for never-written
        space at the end of the file - both end the run at the same place, so
        one predicate covers both cases.
        """
        try:
            return evtx.records(chunk_no)[0].record_num() >= first_rid
        except ChunkUnreadable:
            return False

    lo, hi = 0, evtx.chunk_count - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if belongs_to_newest_run(mid):
            lo = mid
        else:
            hi = mid - 1

    # Immediately after the newest record sits either the oldest one (the ring
    # has wrapped) or unwritten space (then chunk 0 is the oldest).
    tail = lo + 1
    if tail >= evtx.chunk_count or not _has_older_records(evtx, tail, first_rid):
        tail = 0
    return lo, tail


def _has_older_records(evtx: RemoteEvtx, chunk_no: int, first_rid: int) -> bool:
    """Report whether a chunk holds records older than chunk 0's.

    Args:
        evtx: An open remote file.
        chunk_no: Chunk to test.
        first_rid: First record number from chunk 0.

    Returns:
        True if the chunk is readable and holds older records.
    """
    try:
        return evtx.records(chunk_no)[0].record_num() < first_rid
    except ChunkUnreadable:
        return False


def describe_chunk(
    evtx: RemoteEvtx, chunk_no: int, tz: ZoneInfo,
) -> tuple[str, collections.Counter]:
    """Summarise one chunk as a line of text and count its event IDs.

    Args:
        evtx: An open remote file.
        chunk_no: Zero-based chunk number.
        tz: Display timezone.

    Returns:
        A description line and a counter over `(provider, event id)`.
    """
    try:
        found = evtx.records(chunk_no)
    except ChunkUnreadable as exc:
        return f"chunk {chunk_no:>6}: unreadable ({exc})", collections.Counter()

    ids: collections.Counter = collections.Counter()
    for record in found:
        xml = record.xml()
        eid = EVENTID_RE.search(xml)
        provider = PROVIDER_RE.search(xml)
        ids[(provider.group(1) if provider else "?", eid.group(1) if eid else "?")] += 1

    line = (
        f"chunk {chunk_no:>6}: {len(found):>4} records  "
        f"#{found[0].record_num()}..{found[-1].record_num()}  "
        f"{local_time(found[0], tz):%Y-%m-%d %H:%M} .. "
        f"{local_time(found[-1], tz):%H:%M}"
    )
    return line, ids


def sample_texts(
    evtx: RemoteEvtx, chunks: list[int], limit: int,
) -> list[tuple[int, str]]:
    """Collect frequent message texts across several chunks.

    Args:
        evtx: An open remote file.
        chunks: Chunk numbers to look at.
        limit: How many patterns to return.

    Returns:
        Count and example text, most frequent first. Numbers in the text are
        normalised for grouping; the example printed is the unmodified original.
    """
    counter: collections.Counter = collections.Counter()
    example: dict[str, str] = {}
    for chunk_no in chunks:
        try:
            found = evtx.records(chunk_no)
        except ChunkUnreadable:
            continue
        for record in found:
            xml = record.xml()
            match = LOGSTRING_RE.search(xml)
            text = (match.group(1) if match
                    else "(no LogString - structured EventData)")
            key = re.sub(r"[-0-9.,]+", "#", text)[:120]
            counter[key] += 1
            example.setdefault(key, text[:200])
    return [(count, example[key]) for key, count in counter.most_common(limit)]


def list_logs(host: str, domain: str) -> int:
    """List the host's event logs with their size, largest first.

    Args:
        host: Windows host to connect to.
        domain: Domain or workgroup.

    Returns:
        Exit code.
    """
    try:
        conn = connect(host, domain)
        entries = conn.listPath("C$", f"{REMOTE_LOG_DIR}\\*.evtx")
    except (SessionError, OSError, KeyError) as exc:
        log.error("listing failed: %s", exc)
        return EXIT_ERROR

    rows = sorted(
        ((entry.get_filesize(), entry.get_longname()) for entry in entries),
        reverse=True,
    )
    for size, name in rows:
        print(f"{size / 1_048_576:>8.1f} MB  {name}")
    conn.logoff()
    return EXIT_OK


def peek(args: argparse.Namespace) -> int:
    """Judge one log by sampling.

    Args:
        args: Parsed command line.

    Returns:
        Exit code.
    """
    tz = display_tz()
    try:
        evtx = RemoteEvtx(args.logfile, args.host, args.domain)
    except (SessionError, OSError, KeyError) as exc:
        log.error("file not readable: %s", exc)
        return EXIT_ERROR

    try:
        if evtx.chunk_count == 0:
            log.error("too small, or not an EVTX file: %d bytes", evtx.size)
            return EXIT_ERROR

        transferred = 0
        print(f"# {args.logfile} on {args.host}")
        print(f"# {evtx.size / 1_048_576:.1f} MB, {evtx.chunk_count} chunks, "
              f"times in {tz}")

        if args.coverage:
            head, tail = find_ring_head(evtx)
            newest = local_time(evtx.records(head)[-1], tz)
            oldest = local_time(evtx.records(tail)[0], tz)
            span = newest - oldest
            wrapped = "ring wrapped" if tail != 0 else "not wrapped yet"
            print(f"# coverage: {oldest:%Y-%m-%d %H:%M} .. {newest:%Y-%m-%d %H:%M} "
                  f"({span.days} d {span.seconds // 3600} h, {wrapped})")
            transferred += 2

        step = max(1, evtx.chunk_count // args.samples)
        chunks = list(range(0, evtx.chunk_count, step))[:args.samples]
        all_ids: collections.Counter = collections.Counter()
        for chunk_no in chunks:
            line, ids = describe_chunk(evtx, chunk_no, tz)
            print(line)
            all_ids.update(ids)
        transferred += len(chunks)

        print("\n# event IDs in the sample")
        for (provider, eid), count in all_ids.most_common(12):
            print(f"{count:>6}  {provider} / EventID {eid}")

        if args.patterns:
            print("\n# most frequent messages")
            for count, text in sample_texts(evtx, chunks, args.patterns):
                print(f"{count:>6}  {text}")

        print(f"\n# transferred: {transferred * EVTX_CHUNK_SIZE / 1024:.0f} KB "
              f"of {evtx.size / 1_048_576:.1f} MB")
    except (SessionError, ChunkUnreadable, OSError) as exc:
        log.error("read failed: %s", exc)
        return EXIT_ERROR
    finally:
        evtx.close()

    return EXIT_OK


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line.

    Args:
        argv: Argument list; None uses sys.argv.

    Returns:
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Judge a Windows event log over SMB2 range reads, without "
                    "transferring it.",
    )
    parser.add_argument(
        "logfile",
        nargs="?",
        help="filename in the winevt log directory, e.g. 'Security.evtx'",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("LT_SMB_HOST"),
        help="Windows host to read from (default: LT_SMB_HOST)",
    )
    parser.add_argument(
        "--domain",
        default=os.environ.get("LT_SMB_DOMAIN", ""),
        help="domain or workgroup (default: LT_SMB_DOMAIN, else local account)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="only list the event logs present, with their size",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="determine the exact coverage (bisection over the ring buffer)",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=DEFAULT_SAMPLES,
        help=f"number of evenly spaced sample chunks (default: {DEFAULT_SAMPLES})",
    )
    parser.add_argument(
        "--patterns",
        type=int,
        default=10,
        help="number of message patterns to print (0 = none)",
    )
    parser.add_argument(
        "--version", action="version",
        version=f"{SCRIPT_NAME} {read_version()}",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Argument list; None uses sys.argv.

    Returns:
        Exit code: 0 ok, 1 error, 2 usage error.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stderr)
    args = parse_args(argv)

    if not args.host:
        log.error("no host: pass --host or set LT_SMB_HOST")
        return EXIT_USAGE
    if args.list:
        return list_logs(args.host, args.domain)
    if not args.logfile:
        log.error("no filename (or use --list)")
        return EXIT_USAGE
    if args.samples < 1:
        log.error("--samples must be at least 1")
        return EXIT_USAGE

    return peek(args)


if __name__ == "__main__":
    sys.exit(main())
