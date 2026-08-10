"""Regression tests for the packet-rate pitfalls.

These were untestable until the logic was pulled out of the awk blocks it used
to live in. Every one of them had already produced a wrong result at least
once, and none was covered by anything.
"""

from __future__ import annotations

import pytest
from conftest import load_script


@pytest.fixture(scope="session")
def scan():
    return load_script("src/analyze/pktrate-scan.py")


T0 = 1786201899.0
INTERVAL = 5.0


def line(end: float, uni: int, bcast: int, mcast: int,
         rx: int | None = None, tx: int = 100) -> str:
    """A pktrate line exactly as the probe writes it - brackets included."""
    if rx is None:
        rx = uni + bcast + mcast
    return f"[{end:.3f}] {uni} {bcast} {mcast} {rx} {tx} 0 0 0"


# ---------------------------------------------------------------------------
# Pitfall 1: the timestamp is in square brackets
# ---------------------------------------------------------------------------


def test_bracketed_timestamp_is_parsed_not_zeroed(scan):
    """In awk, "[1786201899.326]" evaluates to 0 in arithmetic - silently.

    A whole measurement day once collapsed into one hour that way, and the
    table looked plausible because it had exactly one row.
    """
    s = scan.parse_line(line(T0, 100, 10, 5))
    assert s is not None
    assert s.end == pytest.approx(T0)


def test_full_day_covers_24_hourly_buckets(scan):
    """The cross-check for the bracket trap.

    If timestamps were zeroed, every sample lands in the same bucket. Fewer
    than 24 buckets on a full day is the tell.
    """
    samples = []
    for hour in range(24):
        for n in range(3):
            samples.append(scan.parse_line(line(T0 + hour * 3600 + n * INTERVAL, 100, 10, 5)))
    assert len(scan.hourly_buckets(samples)) == 24

    # And the failure mode it is meant to catch:
    zeroed = [scan.parse_line(line(0, 100, 10, 5)) for _ in range(72)]
    assert len(scan.hourly_buckets(zeroed)) == 1


# ---------------------------------------------------------------------------
# Pitfall 2: the timestamp is the END of the interval
# ---------------------------------------------------------------------------


def test_timestamp_is_the_end_of_the_interval(scan):
    """Treating it as the start shifts every sample by one interval.

    That shift was once the whole difference between a duplication factor of
    53.5 and 1.00 - between "there is a loop" and "there is no loop".
    """
    s = scan.parse_line(line(T0, 100, 10, 5))
    assert s.end == pytest.approx(T0)
    assert s.start(INTERVAL) == pytest.approx(T0 - INTERVAL)


# ---------------------------------------------------------------------------
# Pitfall 3: column order, and the one-line cross-check
# ---------------------------------------------------------------------------


def test_alignment_check_passes_on_consistent_data(scan):
    samples = [scan.parse_line(line(T0 + i * INTERVAL, 100, 10, 5)) for i in range(10)]
    assert scan.check_alignment(samples) == []


def test_alignment_check_catches_a_swapped_column(scan):
    """uni + bcast + mcast must equal rx. If it does not, stop trusting the file.

    This is what catches a driver that names its counters differently, or a
    reader that took the fields in the wrong order.
    """
    bad = scan.parse_line(line(T0, 100, 10, 5, rx=999))
    assert scan.check_alignment([bad]) == [bad]


def test_fields_are_read_in_the_documented_order(scan):
    s = scan.parse_line("[1786201899.326] 4211 106 88 4405 3902 1 2 3")
    assert (s.uni, s.bcast, s.mcast, s.rx, s.tx) == (4211, 106, 88, 4405, 3902)
    assert (s.drop, s.err, s.missed) == (1, 2, 3)


# ---------------------------------------------------------------------------
# Pitfall 4: consecutive samples above threshold are ONE event
# ---------------------------------------------------------------------------


def test_consecutive_samples_are_one_event(scan):
    """A watcher emitting a sliding maximum turned 6 events into 18.

    The inflated count reached a report before anyone noticed.
    """
    samples = [scan.parse_line(line(T0 + i * INTERVAL, 100, 20000, 5)) for i in range(6)]
    events = scan.find_events(samples, flood_min=10000, surge_min=1500, interval=INTERVAL)
    assert len(events) == 1
    assert events[0]["level"] == "flood"
    assert events[0]["samples"] == 6


def test_a_quiet_gap_separates_two_events(scan):
    """Two bursts either side of a quiet period are two events, not one."""
    burst_a = [scan.parse_line(line(T0 + i * INTERVAL, 100, 20000, 5)) for i in range(3)]
    burst_b = [scan.parse_line(line(T0 + 600 + i * INTERVAL, 100, 20000, 5)) for i in range(3)]
    events = scan.find_events(burst_a + burst_b, 10000, 1500, INTERVAL)
    assert len(events) == 2


def test_surge_and_flood_are_distinguished(scan):
    """The smaller precursor must not be reported as a flood."""
    surge = [scan.parse_line(line(T0, 100, 2000, 5))]
    flood = [scan.parse_line(line(T0 + 600, 100, 20000, 5))]
    events = scan.find_events(surge + flood, 10000, 1500, INTERVAL)
    assert [e["level"] for e in events] == ["surge", "flood"]


def test_multicast_alone_triggers_an_event(scan):
    """A flood definition counting only broadcast misses multicast storms.

    Measured in the case this comes from: 30,672 multicast against 5,267
    broadcast in the same window. A broadcast-only threshold would have called
    that quiet.
    """
    samples = [scan.parse_line(line(T0, 100, 5, 30000))]
    events = scan.find_events(samples, 10000, 1500, INTERVAL)
    assert len(events) == 1
    assert events[0]["peak_mcast"] == 30000


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_comments_and_blank_lines_are_skipped(scan):
    assert scan.parse_line("# lan-tomography packet rates") is None
    assert scan.parse_line("") is None
    assert scan.parse_line("   ") is None


def test_malformed_lines_are_skipped_not_fatal(scan):
    """A truncated line at the end of a file being written must not abort."""
    assert scan.parse_line("[1786201899.326] 4211 106") is None
    assert scan.parse_line("garbage") is None
    assert scan.parse_line("[not-a-number] 1 2 3 4 5 0 0 0") is None
