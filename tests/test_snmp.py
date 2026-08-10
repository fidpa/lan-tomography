"""Tests for the merged SNMP client.

The point of this file is not coverage. It is that `src/lib/snmp.py` was created
by merging two copies of the same code that had drifted apart, and each merge
decision is a claim about behaviour. A claim without a test is an opinion.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

SNMP_PATH = Path(__file__).resolve().parent.parent / "src" / "lib" / "snmp.py"
spec = importlib.util.spec_from_file_location("lt_snmp", SNMP_PATH)
snmp = importlib.util.module_from_spec(spec)
sys.modules["lt_snmp"] = snmp
spec.loader.exec_module(snmp)


# ---------------------------------------------------------------------------
# The merge decisions
# ---------------------------------------------------------------------------


def _int_unsigned_legacy(n: int) -> bytes:
    """The encoder the switch prober used, reproduced verbatim."""
    if n == 0:
        return snmp._tlv(snmp.TAG_INTEGER, b"\x00")
    return snmp._tlv(snmp.TAG_INTEGER, n.to_bytes((n.bit_length() // 8) + 1, "big"))


@pytest.mark.parametrize("n", [
    0, 1, 2, 126, 127, 128, 129, 254, 255, 256, 257,
    32767, 32768, 65535, 65536, 0x7FFFFF, 0x800000, 2**31 - 1,
])
def test_int_signed_matches_legacy_unsigned_for_nonnegative(n):
    """DECISION: adopt signed=True.

    Justified only if it changes nothing for the values this client actually
    encodes - request IDs, version, error-status, max-repetitions, all >= 0.
    """
    assert snmp._int(n) == _int_unsigned_legacy(n), f"diverges at {n}"


def test_int_encodes_negatives_correctly():
    """The legacy unsigned form got these wrong; nothing used them, but the
    merged version should not carry the bug forward."""
    assert snmp._int(-1) == bytes([snmp.TAG_INTEGER, 1, 0xFF])
    assert snmp._int(-128) == bytes([snmp.TAG_INTEGER, 1, 0x80])


def test_opaque_decodes_as_integer():
    """DECISION: tag 0x44 (Opaque) yields a number, not None.

    Only one of the merged copies handled it. Dropping it would silently lose
    readings from switches that wrap counters in Opaque.
    """
    assert snmp._decode(snmp.TAG_OPAQUE, b"\x01\x02") == 0x0102


def test_octet_string_is_stripped():
    """DECISION: strip decoded strings.

    Switches pad port descriptions. The copy that did not strip only ever
    decoded integers, so nothing depended on the padding.
    """
    assert snmp._decode(snmp.TAG_OCTET_STRING, b"  Port 23  \x00".replace(b"\x00", b"")) == "Port 23"


def test_empty_value_decodes_to_zero_not_an_exception():
    """DECISION: a zero-length Counter32 is 0.

    Ports that have never been up return one.
    """
    assert snmp._decode(snmp.TAG_COUNTER32, b"") == 0


# ---------------------------------------------------------------------------
# Encoding correctness
# ---------------------------------------------------------------------------


def test_length_short_and_long_form():
    assert snmp._len(0) == b"\x00"
    assert snmp._len(127) == b"\x7f"
    assert snmp._len(128) == b"\x81\x80"
    assert snmp._len(256) == b"\x82\x01\x00"


def test_oid_roundtrip():
    """Multi-byte arcs are where a hand-rolled encoder usually breaks."""
    for oid in (
        [1, 3, 6, 1, 2, 1, 2, 2, 1, 10],           # ifInOctets
        [1, 3, 6, 1, 2, 1, 17, 4, 3, 1, 2],        # dot1dTpFdbPort
        [1, 3, 6, 1, 4, 1, 9, 9, 999999, 1],       # forces base-128 encoding
    ):
        encoded = snmp._oid(oid)
        tag, val, _ = snmp._tlv_read(encoded, 0)
        assert tag == snmp.TAG_OID
        assert snmp._parse_oid(val) == oid


def test_end_of_mib_markers_stop_a_walk():
    """Treating these as data is how a walk becomes an infinite loop."""
    for tag in (snmp.TAG_NO_SUCH_OBJECT, snmp.TAG_NO_SUCH_INSTANCE,
                snmp.TAG_END_OF_MIB_VIEW):
        assert snmp.is_end_of_mib(tag)
    assert not snmp.is_end_of_mib(snmp.TAG_COUNTER32)


# ---------------------------------------------------------------------------
# Round trip against a synthesised response
# ---------------------------------------------------------------------------


def _build_response(reqid: int, varbinds: list[tuple[list[int], int, bytes]]) -> bytes:
    """Assemble a GetResponse the way an agent would."""
    vbs = b"".join(
        snmp._tlv(snmp.TAG_SEQUENCE, snmp._oid(oid) + snmp._tlv(tag, val))
        for oid, tag, val in varbinds
    )
    pdu = snmp._tlv(
        0xA2,
        snmp._int(reqid) + snmp._int(0) + snmp._int(0)
        + snmp._tlv(snmp.TAG_SEQUENCE, vbs),
    )
    return snmp._tlv(
        snmp.TAG_SEQUENCE,
        snmp._int(1) + snmp._tlv(snmp.TAG_OCTET_STRING, b"public") + pdu,
    )


def test_parse_response_reads_what_build_getbulk_asked_for():
    base = [1, 3, 6, 1, 2, 1, 2, 2, 1, 10]
    raw = _build_response(7, [
        (base + [1], snmp.TAG_COUNTER32, b"\x00\x01\x86\xa0"),
        (base + [2], snmp.TAG_COUNTER32, b""),
        (base + [3], snmp.TAG_OCTET_STRING, b"uplink  "),
    ])
    parsed = snmp.parse_response(raw)
    assert [oid for oid, _, _ in parsed] == [base + [1], base + [2], base + [3]]
    assert parsed[0][2] == 100000
    assert parsed[1][2] == 0
    assert parsed[2][2] == "uplink"


def test_getbulk_request_is_wellformed():
    msg = snmp.build_getbulk("public", [1, 3, 6, 1, 2, 1, 2, 2, 1, 10], reqid=1, reps=30)
    tag, body, _ = snmp._tlv_read(msg, 0)
    assert tag == snmp.TAG_SEQUENCE
    items = snmp._children(body)
    assert items[0][0] == snmp.TAG_INTEGER
    assert int.from_bytes(items[0][1], "big") == 1      # SNMPv2c
    assert items[1][1] == b"public"
    assert items[2][0] == snmp.TAG_GETBULK


# ---------------------------------------------------------------------------
# Walk helpers
# ---------------------------------------------------------------------------


class _FakeSock:
    """Replays canned GetBulk responses, then an out-of-subtree OID to end."""

    def __init__(self, pages):
        self._pages = list(pages)
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def recvfrom(self, _n):
        return self._pages.pop(0), ("192.0.2.2", 161)


def test_walk_column_keys_on_last_arc():
    base = [1, 3, 6, 1, 2, 1, 2, 2, 1, 10]
    page1 = _build_response(1, [
        (base + [1], snmp.TAG_COUNTER32, b"\x01"),
        (base + [2], snmp.TAG_COUNTER32, b"\x02"),
    ])
    # Leaving the subtree is what ends the walk.
    page2 = _build_response(2, [
        ([1, 3, 6, 1, 2, 1, 2, 2, 1, 11, 1], snmp.TAG_COUNTER32, b"\x09"),
    ])
    sock = _FakeSock([page1, page2])
    assert snmp.walk_column(sock, "192.0.2.2", "public", base) == {1: 1, 2: 2}


def test_walk_stops_on_end_of_mib_instead_of_looping():
    base = [1, 3, 6, 1, 2, 1, 2, 2, 1, 10]
    page = _build_response(1, [
        (base + [1], snmp.TAG_COUNTER32, b"\x01"),
        (base + [2], snmp.TAG_END_OF_MIB_VIEW, b""),
    ])
    sock = _FakeSock([page])
    assert snmp.walk(sock, "192.0.2.2", "public", base) == {tuple(base + [1]): 1}


def test_walk_respects_max_requests_backstop():
    """An agent that never advances the OID must not spin forever."""
    base = [1, 3, 6, 1, 2, 1, 2, 2, 1, 10]
    page = _build_response(1, [(base + [1], snmp.TAG_COUNTER32, b"\x01")])
    sock = _FakeSock([page] * 500)
    snmp.walk(sock, "192.0.2.2", "public", base, max_requests=5)
    assert len(sock.sent) == 5
