"""Minimal SNMPv2c client: BER/ASN.1 encoding and GetBulk over a raw socket.

Standard library only, on purpose. A probe host is often a machine you are
allowed to touch exactly once, and `pip install pysnmp` is not always a step you
get to take. Roughly seventy lines of encoder buy independence from that.

Scope is deliberately narrow: GetBulk against one agent, community string
authentication, the value types a switch actually returns. This is not a general
SNMP library and should not grow into one.

PROVENANCE
    This module merges two independently written copies of the same client that
    had drifted apart. Where they disagreed, the choice is recorded at the point
    of the decision rather than silently unified - each difference was real
    behaviour that somebody could be relying on.
"""

from __future__ import annotations

import socket
from typing import Any

SNMP_PORT = 161

# ASN.1 / SNMPv2c tags
TAG_INTEGER = 0x02
TAG_OCTET_STRING = 0x04
TAG_NULL = 0x05
TAG_OID = 0x06
TAG_SEQUENCE = 0x30
TAG_IP_ADDRESS = 0x40
TAG_COUNTER32 = 0x41
TAG_GAUGE32 = 0x42
TAG_TIMETICKS = 0x43
TAG_OPAQUE = 0x44
TAG_COUNTER64 = 0x46
TAG_GETBULK = 0xA5

# End-of-MIB and "no such object" markers a GetBulk walk must stop on rather
# than mistake for data.
TAG_NO_SUCH_OBJECT = 0x80
TAG_NO_SUCH_INSTANCE = 0x81
TAG_END_OF_MIB_VIEW = 0x82


# --------------------------------------------------------------------------
# Encoding
# --------------------------------------------------------------------------


def _len(n: int) -> bytes:
    """BER length: short form below 128, else long form."""
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _tlv(tag: int, val: bytes) -> bytes:
    return bytes([tag]) + _len(len(val)) + val


def _int(n: int) -> bytes:
    """BER INTEGER.

    DECISION: signed two's complement, per X.690. One of the merged copies
    encoded unsigned with a special case for zero; the other used signed=True.
    They produce identical bytes for every non-negative input (see
    tests/test_snmp.py, which asserts exactly that over the full range this
    client uses), so adopting the signed form loses nothing and is correct for
    negatives, which the unsigned form silently got wrong.
    """
    raw = n.to_bytes(max(1, (n.bit_length() + 8) // 8), "big", signed=True)
    # X.690 8.3.2 requires the minimal encoding: the first nine bits must not be
    # all ones or all zeros. Neither merged copy did this, because neither ever
    # encoded a negative number - _int(-128) came out as ff 80 instead of 80.
    # Unreachable through this client's call sites, and fixed anyway: a wrong
    # branch left in place is a wrong branch somebody later calls.
    while len(raw) > 1 and (
        (raw[0] == 0x00 and not raw[1] & 0x80)
        or (raw[0] == 0xFF and raw[1] & 0x80)
    ):
        raw = raw[1:]
    return _tlv(TAG_INTEGER, raw)


def _oid(parts: list[int]) -> bytes:
    """BER OBJECT IDENTIFIER: first two arcs packed, rest base-128."""
    out = bytes([parts[0] * 40 + parts[1]])
    for p in parts[2:]:
        if p < 128:
            out += bytes([p])
        else:
            chunks: list[int] = []
            while p:
                chunks.insert(0, p & 0x7F)
                p >>= 7
            out += bytes([c | 0x80 for c in chunks[:-1]] + [chunks[-1]])
    return _tlv(TAG_OID, out)


# --------------------------------------------------------------------------
# Decoding
# --------------------------------------------------------------------------


def _parse_oid(raw: bytes) -> list[int]:
    parts = [raw[0] // 40, raw[0] % 40]
    acc = 0
    for c in raw[1:]:
        acc = (acc << 7) | (c & 0x7F)
        if not c & 0x80:
            parts.append(acc)
            acc = 0
    return parts


def _tlv_read(buf: bytes, i: int) -> tuple[int, bytes, int]:
    tag = buf[i]
    ln = buf[i + 1]
    i += 2
    if ln & 0x80:
        n = ln & 0x7F
        ln = int.from_bytes(buf[i:i + n], "big")
        i += n
    return tag, buf[i:i + ln], i + ln


def _children(buf: bytes) -> list[tuple[int, bytes]]:
    out, i = [], 0
    while i < len(buf):
        tag, val, i = _tlv_read(buf, i)
        out.append((tag, val))
    return out


def _decode(tag: int, val: bytes) -> Any:
    """Decode one varbind value.

    DECISION 1: Opaque (0x44) is decoded as an integer. Strictly it is an
    arbitrary wrapped encoding, but switches in the field return counters in it,
    and one of the merged copies handled it for exactly that reason. Returning
    None instead would drop real readings.

    DECISION 2: strings are stripped. One copy stripped, the other did not. The
    non-stripping copy only ever decoded integers - its MAC addresses come out
    of the OID, not out of a value - so its string branch was dead code, and
    nothing depended on the trailing whitespace that switches pad descriptions
    with.

    DECISION 3: an empty value decodes to 0 rather than raising. A zero-length
    Counter32 shows up on ports that have never been up.
    """
    if tag in (TAG_INTEGER, TAG_COUNTER32, TAG_GAUGE32,
               TAG_TIMETICKS, TAG_OPAQUE, TAG_COUNTER64):
        return int.from_bytes(val, "big") if val else 0
    if tag == TAG_OCTET_STRING:
        return val.decode("utf-8", "replace").strip()
    if tag == TAG_IP_ADDRESS:
        return ".".join(str(b) for b in val)
    return None


def is_end_of_mib(tag: int) -> bool:
    """True for the markers that end a walk.

    Treating these as data is how a walk turns into an infinite loop: the agent
    keeps answering, the OID stops advancing, and the caller keeps asking.
    """
    return tag in (TAG_NO_SUCH_OBJECT, TAG_NO_SUCH_INSTANCE, TAG_END_OF_MIB_VIEW)


# --------------------------------------------------------------------------
# Request / response
# --------------------------------------------------------------------------


def build_getbulk(community: str, oid: list[int], reqid: int,
                  reps: int = 30, non_repeaters: int = 0) -> bytes:
    """Assemble one SNMPv2c GetBulk message."""
    varbind = _tlv(TAG_SEQUENCE, _oid(oid) + _tlv(TAG_NULL, b""))
    pdu = _tlv(TAG_GETBULK,
               _int(reqid) + _int(non_repeaters) + _int(reps)
               + _tlv(TAG_SEQUENCE, varbind))
    # version 1 == SNMPv2c
    return _tlv(TAG_SEQUENCE,
                _int(1) + _tlv(TAG_OCTET_STRING, community.encode()) + pdu)


def parse_response(data: bytes) -> list[tuple[list[int], int, Any]]:
    """Return [(oid, tag, value), ...] from a response message.

    Raises IndexError or ValueError if the response is not decodable; callers
    treat that as "agent answered with something we cannot use", which is a
    different fault from "agent did not answer".
    """
    _, body, _ = _tlv_read(data, 0)
    items = _children(body)
    _, pdu_body = items[2]
    fields = _children(pdu_body)
    _, varbinds = fields[3]

    out: list[tuple[list[int], int, Any]] = []
    for _, vb in _children(varbinds):
        parts = _children(vb)
        oid = _parse_oid(parts[0][1])
        tag, val = parts[1]
        out.append((oid, tag, _decode(tag, val)))
    return out


def walk(sock: socket.socket, host: str, community: str, base_oid: list[int],
         reps: int = 30, port: int = SNMP_PORT,
         max_requests: int = 200) -> dict[tuple[int, ...], Any]:
    """GetBulk-walk one subtree.

    GetBulk rather than GetNext: for a hundred-odd entries that is three packets
    instead of a hundred against a production switch. Politeness
    here is not cosmetic - see docs/explanation/pitfalls.md on what polling does
    to a device whose management CPU is already the bottleneck.

    max_requests is a backstop against an agent that never advances the OID.
    """
    result: dict[tuple[int, ...], Any] = {}
    cur = list(base_oid)
    reqid = 0
    requests = 0

    while requests < max_requests:
        requests += 1
        reqid += 1
        sock.sendto(build_getbulk(community, cur, reqid, reps), (host, port))
        data, _ = sock.recvfrom(65535)

        last = None
        for oid, tag, value in parse_response(data):
            if oid[:len(base_oid)] != base_oid or is_end_of_mib(tag):
                return result
            result[tuple(oid)] = value
            last = oid

        if last is None:
            return result
        cur = last

    return result


def walk_column(sock: socket.socket, host: str, community: str,
                base_oid: list[int], reps: int = 30,
                port: int = SNMP_PORT) -> dict[int, Any]:
    """Walk one table column, keyed by its final OID arc.

    Convenience for the common case of an ifTable column, where the last arc is
    the interface index. Callers needing more of the OID - a forwarding
    database keys on the six MAC bytes, not on one arc - should use walk() and
    slice the key themselves.
    """
    return {
        oid[-1]: value
        for oid, value in walk(sock, host, community, base_oid, reps, port).items()
    }


def open_socket(timeout: float = 3.0) -> socket.socket:
    """UDP socket with a timeout.

    A timeout is not optional here: without one, a switch that stops answering
    mid-walk hangs the poller forever, and the resulting gap in the timeline
    looks exactly like a quiet period rather than a failed measurement.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    return sock
