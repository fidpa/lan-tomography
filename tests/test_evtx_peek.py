"""Tests for src/contrib/evtx-peek.py.

The SMB side is not tested - that would need a Windows host, and a mock of
impacket would only assert that the mock behaves like the mock. What is tested
is the part that decides whether a coverage statement is true: the ring-buffer
bisection (pitfall G1), plus credential and timezone resolution.

`FakeEvtx` stands in for `RemoteEvtx`. It is not a mock of SMB; it is a chunk
layout written out by hand, which is the thing the bisection reasons about.
"""

from __future__ import annotations

import pytest

pytest.importorskip("impacket", reason="src/contrib/ needs impacket")
pytest.importorskip("Evtx", reason="src/contrib/ needs python-evtx")


class FakeRecord:
    """One EVTX record, reduced to the only field the bisection reads."""

    def __init__(self, record_num: int) -> None:
        self._num = record_num

    def record_num(self) -> int:
        return self._num


class FakeEvtx:
    """A chunk layout, standing in for an open remote file.

    Args:
        chunks: One entry per chunk. A list of record numbers, or None for a
            chunk that cannot be read (never written, or overwritten mid-write).
    """

    def __init__(self, chunks: list[list[int] | None]) -> None:
        self._chunks = chunks
        self.chunk_count = len(chunks)
        self.reads = 0

    def records(self, chunk_no: int) -> list[FakeRecord]:
        self.reads += 1
        payload = self._chunks[chunk_no]
        if payload is None:
            raise self.unreadable("no records")
        return [FakeRecord(n) for n in payload]

    # Set by the fixture below, so the fake raises the module's own exception.
    unreadable: type[Exception]


@pytest.fixture(scope="session")
def evtx_peek():
    from conftest import load_script
    return load_script("src/contrib/evtx-peek.py")


@pytest.fixture
def make_evtx(evtx_peek):
    def _make(chunks: list[list[int] | None]) -> FakeEvtx:
        fake = FakeEvtx(chunks)
        fake.unreadable = evtx_peek.ChunkUnreadable
        return fake
    return _make


def chunk_run(start: int, count: int, per_chunk: int = 100) -> list[list[int]]:
    """`count` chunks of ascending record numbers, starting at `start`."""
    return [list(range(start + i * per_chunk, start + (i + 1) * per_chunk))
            for i in range(count)]


def test_unwrapped_file_reports_chunk_zero_as_oldest(evtx_peek, make_evtx):
    """A log that has not wrapped yet: newest last, oldest at chunk 0."""
    evtx = make_evtx(chunk_run(1000, 8))
    head, tail = evtx_peek.find_ring_head(evtx)
    assert (head, tail) == (7, 0)


def test_wrapped_file_finds_the_seam_not_chunk_zero(evtx_peek, make_evtx):
    """Pitfall G1: after the wrap, the oldest record is NOT in chunk 0.

    Chunks 0-3 hold the newest run (5000+), chunks 4-9 the older one (1000+).
    Reading first and last would report a span running the wrong way; the seam
    is what makes the coverage statement true.
    """
    evtx = make_evtx(chunk_run(5000, 4) + chunk_run(1000, 6))
    head, tail = evtx_peek.find_ring_head(evtx)
    assert (head, tail) == (3, 4)


def test_seam_at_the_last_chunk(evtx_peek, make_evtx):
    """The wrap point can sit anywhere, including one chunk before the end."""
    evtx = make_evtx(chunk_run(5000, 9) + chunk_run(1000, 1))
    head, tail = evtx_peek.find_ring_head(evtx)
    assert (head, tail) == (8, 9)


def test_unwritten_tail_is_not_mistaken_for_a_wrap(evtx_peek, make_evtx):
    """Never-written chunks end the run exactly like a wrap does.

    They must not be reported as the oldest record: the span would then start
    at an unreadable chunk. Chunk 0 is the oldest here.
    """
    evtx = make_evtx(chunk_run(1000, 5) + [None] * 5)
    head, tail = evtx_peek.find_ring_head(evtx)
    assert (head, tail) == (4, 0)


def test_bisection_does_not_read_every_chunk(evtx_peek, make_evtx):
    """The point of the bisection: ~log2(n) reads, not n.

    A linear scan over a 300 MB log is 4800 range reads, which is the whole
    transfer this tool exists to avoid.
    """
    evtx = make_evtx(chunk_run(5000, 500) + chunk_run(1000, 524))
    evtx_peek.find_ring_head(evtx)
    assert evtx.reads < 30


def test_read_secret_reads_without_sourcing(evtx_peek, tmp_path, monkeypatch):
    """Credentials come from LT_SECRETS as data, never executed."""
    secrets = tmp_path / "secrets"
    secrets.write_text(
        '# comment\n'
        'LT_SMB_USER="svc-readonly"\n'
        "LT_SMB_PASSWORD='p@ss word'\n"
        "$(touch /tmp/should-not-happen)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LT_SECRETS", str(secrets))
    assert evtx_peek.read_secret("LT_SMB_USER") == "svc-readonly"
    assert evtx_peek.read_secret("LT_SMB_PASSWORD") == "p@ss word"
    assert evtx_peek.read_secret("LT_SMB_DOMAIN") is None


def test_read_secret_without_lt_secrets_is_none_not_an_error(evtx_peek, monkeypatch):
    monkeypatch.delenv("LT_SECRETS", raising=False)
    assert evtx_peek.read_secret("LT_SMB_USER") is None


def test_missing_secrets_file_is_none_not_an_error(evtx_peek, tmp_path, monkeypatch):
    monkeypatch.setenv("LT_SECRETS", str(tmp_path / "absent"))
    assert evtx_peek.read_secret("LT_SMB_USER") is None


def test_display_tz_follows_lt_tz(evtx_peek, monkeypatch):
    monkeypatch.setenv("LT_TZ", "Europe/Berlin")
    assert str(evtx_peek.display_tz()) == "Europe/Berlin"


def test_display_tz_defaults_to_utc(evtx_peek, monkeypatch):
    monkeypatch.delenv("LT_TZ", raising=False)
    assert str(evtx_peek.display_tz()) == "UTC"


def test_unknown_timezone_falls_back_to_utc(evtx_peek, monkeypatch):
    """A typo in LT_TZ must not cost a measurement window."""
    monkeypatch.setenv("LT_TZ", "Europe/Berln")
    assert str(evtx_peek.display_tz()) == "UTC"
