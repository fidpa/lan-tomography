"""Regression tests for the analysis pitfalls of the ping matrix.

Every test here stands for a pitfall that cost real time or produced a wrong
statement during a live investigation. The pitfalls are written up in prose in
docs/explanation/pitfalls.md; here they are executable.

The benefit is twofold: the analysis stays protected against regressions, and a
reader sees from the test name alone what the trap consists of.

Test data is created exclusively in tmp_path. No production measurement data is
read.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# An arbitrary fixed reference point so the tests do not depend on the time of
# the test run.
T0 = 1785924000.0

INTERVAL = 0.2


def reply(ts: float, seq: int, ip: str = "192.0.2.1") -> str:
    """An answered ping line, as `ping -D` writes it."""
    return f"[{ts:.6f}] 64 bytes from {ip}: icmp_seq={seq} ttl=255 time=0.404 ms"


def no_answer(ts: float, seq: int) -> str:
    """A "no answer yet" line.

    Neither a reply nor a loss: ping writes it as soon as a reply takes longer
    than one send interval.
    """
    return f"[{ts:.6f}] no answer yet for icmp_seq={seq}"


def unreachable(ts: float, seq: int, ip: str = "192.0.2.1") -> str:
    """An unreachable line carrying its own sequence number."""
    return f"[{ts:.6f}] From {ip} icmp_seq={seq} Destination Host Unreachable"


def write_log(directory: Path, label: str, day: str, lines: list[str]) -> Path:
    """Write a daily log file in the probe's format."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{label}-{day}.log"
    path.write_text("\n".join(lines) + "\n")
    return path


def compress(path: Path) -> Path:
    """Compress a log to .log.zst and remove the original."""
    subprocess.run(["zstd", "-q", "--rm", str(path)], check=True)
    return path.with_suffix(path.suffix + ".zst")


# ---------------------------------------------------------------------------
# Pitfall 1: "no answer yet" is neither a reply nor a loss
# ---------------------------------------------------------------------------


def test_no_answer_yet_with_later_reply_is_not_a_loss(correlate, tmp_path):
    """If the reply arrives late, the packet was not lost.

    Counting "no answer yet" as loss overestimates it by orders of magnitude.
    """
    lines = []
    for seq in range(1, 51):
        ts = T0 + seq * INTERVAL
        if seq == 25:
            # First the notice, then a reply to the same sequence number.
            lines.append(no_answer(ts, seq))
            lines.append(reply(ts + 0.3, seq))
        else:
            lines.append(reply(ts, seq))

    write_log(tmp_path, "gw", "2026-08-05", lines)
    res = correlate.analyse_window(tmp_path, "gw", T0, T0 + 100)

    assert res is not None
    assert res["lost"] == 0
    assert res["affected"] is False


def test_never_answered_sequence_is_a_real_loss(correlate, tmp_path):
    """The other direction: with no reply at all, the sequence is lost."""
    lines = []
    for seq in range(1, 51):
        ts = T0 + seq * INTERVAL
        if seq == 25:
            lines.append(no_answer(ts, seq))
        else:
            lines.append(reply(ts, seq))

    write_log(tmp_path, "gw", "2026-08-05", lines)
    res = correlate.analyse_window(tmp_path, "gw", T0, T0 + 100)

    assert res["lost"] == 1


def test_no_answer_yet_does_not_count_as_a_reply(correlate, tmp_path):
    """The second direction of the same trap, and the expensive one.

    The line contains "icmp_seq=", so a parser that treats it as a reply sees a
    smooth answer density and concludes the network is healthy. The actual loss
    in the case this comes from was 9 to 21 percent.
    """
    lines = [reply(T0, 1)]
    # 100 sequence numbers that are NEVER answered.
    lines += [no_answer(T0 + i * INTERVAL, i) for i in range(2, 102)]

    write_log(tmp_path, "gw", "2026-08-05", lines)
    res = correlate.analyse_window(tmp_path, "gw", T0, T0 + 100)

    assert res["lost"] == 100
    assert res["sent"] == 101
    assert res["affected"] is True


# ---------------------------------------------------------------------------
# Pitfall 2: "no data" is not a statement about loss
# ---------------------------------------------------------------------------


def test_missing_log_file_yields_none_not_zero_loss(correlate, tmp_path):
    """A missing file gives None, not "no loss".

    Missing daily files have harmless causes - a target added to the matrix
    later, a file lost during compression. Scored as "0 loss" it would prop up
    a false clean verdict.
    """
    assert correlate.analyse_window(tmp_path, "gw", T0, T0 + 100) is None


def test_window_without_lines_yields_none(correlate, tmp_path):
    """Same if the file exists but nothing falls inside the window."""
    write_log(tmp_path, "gw", "2026-08-05", [reply(T0, 1)])

    res = correlate.analyse_window(tmp_path, "gw", T0 + 10_000, T0 + 20_000)
    assert res is None


# ---------------------------------------------------------------------------
# Pitfall 3: a target that never answers is off, not failed
# ---------------------------------------------------------------------------


def test_permanently_silent_target_counts_as_offline(correlate, tmp_path):
    """Workstations are switched off after hours.

    Without this distinction every overnight analysis reports them as a total
    outage and skews the verdict.
    """
    lines = [no_answer(T0 + i * INTERVAL, i) for i in range(1, 101)]
    write_log(tmp_path, "ws01", "2026-08-05", lines)

    res = correlate.analyse_window(tmp_path, "ws01", T0, T0 + 100)

    assert res["offline"] is True
    assert res["affected"] is False


def test_offline_still_counts_against_a_server_role(correlate, tmp_path):
    """The other half of the same rule, in is_hit().

    Only offline-tolerant roles are excused. A server that is silent for the
    whole window IS a total outage - excusing it would let the worst possible
    failure produce a clean verdict.
    """
    silent = {"affected": False, "offline": True}
    assert correlate.is_hit("client-path", silent) is False
    assert correlate.is_hit("symptom", silent) is True
    assert correlate.is_hit("fabric-ref", silent) is True


# ---------------------------------------------------------------------------
# Pitfall 4: single losses are not an outage, contiguous ones are
# ---------------------------------------------------------------------------


def test_scattered_single_losses_are_not_an_outage(correlate, tmp_path):
    """One to three packets in a thousand go missing on any LAN.

    No TCP or remote-desktop session ever notices.
    """
    lost = {10, 200, 450}
    lines = [
        no_answer(T0 + seq * INTERVAL, seq)
        if seq in lost
        else reply(T0 + seq * INTERVAL, seq)
        for seq in range(1, 501)
    ]
    write_log(tmp_path, "gw", "2026-08-05", lines)

    res = correlate.analyse_window(tmp_path, "gw", T0, T0 + 200)

    assert res["lost"] == 3
    assert res["outage_s"] < correlate.OUTAGE_THRESHOLD_S
    assert res["affected"] is False


def test_contiguous_burst_is_an_outage(correlate, tmp_path):
    """Uninterrupted loss above the threshold tears a session too."""
    lost = set(range(100, 130))  # 30 packets = 6.0 s
    lines = [
        no_answer(T0 + seq * INTERVAL, seq)
        if seq in lost
        else reply(T0 + seq * INTERVAL, seq)
        for seq in range(1, 501)
    ]
    write_log(tmp_path, "gw", "2026-08-05", lines)

    res = correlate.analyse_window(tmp_path, "gw", T0, T0 + 200)

    assert res["lost"] == 30
    assert res["outage_s"] >= correlate.OUTAGE_THRESHOLD_S
    assert res["affected"] is True


def test_gap_with_no_lines_at_all_is_found_on_the_time_axis(correlate, tmp_path):
    """If ping goes silent entirely, there are no sequence numbers to count.

    That is exactly how one real outage presented: last reply at 12:55:57, next
    at 13:18:11, nothing in between. Only the distance between two lines finds
    it - which is why max_gap_s exists alongside the burst measure.
    """
    last_before_gap = T0 + 20 * INTERVAL
    first_after = last_before_gap + 300

    lines = [reply(T0 + i * INTERVAL, i) for i in range(1, 21)]
    # 300 s of silence, then it resumes.
    lines += [reply(first_after + i * INTERVAL, 100 + i) for i in range(20)]
    write_log(tmp_path, "gw", "2026-08-05", lines)

    res = correlate.analyse_window(tmp_path, "gw", T0, T0 + 400)

    assert res["max_gap_s"] == pytest.approx(300, abs=0.01)
    assert res["affected"] is True


# ---------------------------------------------------------------------------
# Pitfall 5: unreachable phases must not collapse into one lost packet
# ---------------------------------------------------------------------------


def test_unreachable_lines_carry_their_sequence_number(correlate, tmp_path):
    """Regression.

    Previously a whole unreachable phase collapsed into a single pseudo-loss
    (seq=-1) and therefore stayed below the outage threshold - a long, obvious
    outage reported as a blip.
    """
    lines = [reply(T0 + i * INTERVAL, i) for i in range(1, 11)]
    lines += [unreachable(T0 + seq * INTERVAL, seq) for seq in range(11, 51)]
    write_log(tmp_path, "gw", "2026-08-05", lines)

    res = correlate.analyse_window(tmp_path, "gw", T0, T0 + 100)

    assert res["lost"] == 40
    assert res["affected"] is True


# ---------------------------------------------------------------------------
# Pitfall 6: compressed archive days must not be skipped silently
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")
def test_log_files_finds_compressed_days(correlate, tmp_path):
    """A plain glob("*.log") skips archive days without a word.

    The analysis would carry on with less data, no error and no exit code.
    """
    write_log(tmp_path, "gw", "2026-08-05", [reply(T0, 1)])
    compress(write_log(tmp_path, "gw", "2026-08-04", [reply(T0 - 86400, 1)]))

    found = correlate.log_files(tmp_path, "gw-*.log")

    assert len(found) == 2
    assert [p.name for p in found] == [
        "gw-2026-08-04.log.zst",
        "gw-2026-08-05.log",
    ]


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")
def test_log_files_does_not_count_the_same_day_twice(correlate, tmp_path):
    """With both forms present, the uncompressed file wins.

    The case is real: probes are mirrored with "rsync -az" without --delete, so
    a day compressed on the probe comes back uncompressed and sits next to its
    own archive. Counted twice it would double the packet counts.
    """
    write_log(tmp_path, "gw", "2026-08-04", [reply(T0, 1)])
    # Put a second copy of the same day next to it as .zst.
    second = write_log(tmp_path, "gwTMP", "2026-08-04", [reply(T0, 1)])
    compressed = compress(second)
    compressed.rename(tmp_path / "gw-2026-08-04.log.zst")

    found = correlate.log_files(tmp_path, "gw-*.log")

    assert len(found) == 1
    assert found[0].name == "gw-2026-08-04.log"


def test_log_files_ignores_partial_archives(correlate, tmp_path):
    """A half-written archive must not be read as a day.

    compress-logs.sh writes to "<name>.zst.partial" and renames only after the
    archive verifies, so that an interrupted run cannot leave a truncated file
    under the name a reader looks for (pitfall B10). That protection depends on
    the suffix falling outside both globs here: the file ends in ".partial",
    not ".zst", so neither pattern matches it. Pick a suffix that keeps the
    .zst ending and the guarantee is gone, silently.
    """
    write_log(tmp_path, "gw", "2026-08-05", [reply(T0, 1)])
    (tmp_path / "gw-2026-08-04.log.zst.partial").write_bytes(b"\x28\xb5\x2f\xfd truncated")

    found = correlate.log_files(tmp_path, "gw-*.log")

    assert [p.name for p in found] == ["gw-2026-08-05.log"]


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")
def test_read_log_returns_the_same_compressed_or_not(correlate, tmp_path):
    """read_log must deliver both forms transparently."""
    content = [reply(T0 + i * INTERVAL, i) for i in range(1, 6)]
    plain = write_log(tmp_path, "gw", "2026-08-05", content)
    expected = correlate.read_log(plain)

    zst = compress(plain)
    assert correlate.read_log(zst) == expected


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")
def test_analysis_over_a_compressed_day_is_identical(correlate, tmp_path):
    """The verdict must not depend on whether a day happens to be compressed."""
    lines = [
        no_answer(T0 + seq * INTERVAL, seq)
        if seq in set(range(50, 80))
        else reply(T0 + seq * INTERVAL, seq)
        for seq in range(1, 201)
    ]

    plain_dir = tmp_path / "plain"
    zst_dir = tmp_path / "zst"
    write_log(plain_dir, "gw", "2026-08-05", lines)
    compress(write_log(zst_dir, "gw", "2026-08-05", lines))

    a = correlate.analyse_window(plain_dir, "gw", T0, T0 + 100)
    b = correlate.analyse_window(zst_dir, "gw", T0, T0 + 100)

    assert a == b
    assert a["affected"] is True


# ---------------------------------------------------------------------------
# Pitfall 7: the known limit of the sequence number
# ---------------------------------------------------------------------------


def test_seq_wrap_masks_loss_in_an_oversized_window(correlate, tmp_path):
    """Documents a known limit, not a defect.

    icmp_seq is 16 bit and wraps after 3 h 38 min at 5 packets/s. Analysing a
    window longer than that makes the same number occur twice, and an answered
    sequence then excuses the later real loss. Hence the rule: analyse along
    the time axis, not along the sequence number.

    If this test ever fails, the limit was fixed. Delete the test and the
    warning in docs/explanation/pitfalls.md together.
    """
    wrap = 65536 * INTERVAL  # 13107.2 s = 3 h 38 min

    lines = [reply(T0, 4242)]
    # One wrap later, the same number, this time unanswered.
    lines.append(no_answer(T0 + wrap, 4242))

    write_log(tmp_path, "gw", "2026-08-05", lines)
    res = correlate.analyse_window(tmp_path, "gw", T0, T0 + wrap + 60)

    # The real loss stays invisible, because the number counts as answered.
    assert res["lost"] == 0
    # On the time axis it is plainly visible.
    assert res["max_gap_s"] == pytest.approx(wrap, abs=1)
    assert res["affected"] is True


# ---------------------------------------------------------------------------
# STP topology changes: three states, and the middle one is the trap
# ---------------------------------------------------------------------------


def stp_line(stamp: str, flags: str = "none") -> str:
    """One `tcpdump -n -l -tttt` STP line, local time and no offset."""
    return (f"{stamp} STP 802.1d, Config, Flags [{flags}], bridge-id "
            f"8000.00:00:5e:00:53:01.8001, length 43")


def write_l2(directory: Path, day: str, lines: list[str]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"l2-events-{day}.log"
    path.write_text("# ---- L2 capture starts | timestamps: UTC (no offset) ----\n"
                    + "\n".join(lines) + "\n")
    return path


def test_topology_change_in_the_window_is_counted(correlate, tmp_path, monkeypatch):
    """The capture exists for this. A change at the tear-down second is
    the switch itself saying the path was rebuilt."""
    monkeypatch.setenv("LT_TZ", "UTC")
    write_l2(tmp_path / "l2", "2026-08-08", [
        stp_line("2026-08-08 12:00:00.100000"),
        stp_line("2026-08-08 12:00:02.100000", flags="Topology change"),
        stp_line("2026-08-08 12:00:04.100000"),
    ])
    start = 1786190400.0                      # 2026-08-08 12:00:00 UTC

    hits = correlate.stp_changes(tmp_path / "l2", start, start + 10)

    assert hits is not None
    assert len(hits) == 1
    assert "Topology change" in hits[0]


def test_window_without_l2_lines_yields_none_not_zero_changes(correlate, tmp_path,
                                                              monkeypatch):
    """A day's file is opened at midnight; a capture that dies at 09:00 leaves
    a file that looks exactly like a quiet day.

    So the answer for an uncovered window is None. Returning 0 would turn a
    dead capture into a falsification - the same mistake as scoring a missing
    ping log as 0 % loss, one layer down.
    """
    monkeypatch.setenv("LT_TZ", "UTC")
    write_l2(tmp_path / "l2", "2026-08-08", [
        stp_line("2026-08-08 09:00:00.100000"),   # capture died after this
    ])
    start = 1786190400.0                      # 12:00 UTC, three hours later

    assert correlate.stp_changes(tmp_path / "l2", start, start + 10) is None
    # And an empty directory is the same statement, not a quieter one.
    assert correlate.stp_changes(tmp_path / "empty", start, start + 10) is None


def test_a_running_capture_without_changes_is_a_finding(correlate, tmp_path,
                                                        monkeypatch):
    """The other side of the same rule: [] and None must not collapse.

    An empty list says "the capture was running and the topology held", which
    is a falsification worth having.
    """
    monkeypatch.setenv("LT_TZ", "UTC")
    write_l2(tmp_path / "l2", "2026-08-08", [
        stp_line("2026-08-08 12:00:00.100000"),
        stp_line("2026-08-08 12:00:02.100000"),
    ])
    start = 1786190400.0

    assert correlate.stp_changes(tmp_path / "l2", start, start + 10) == []


def test_l2_timestamps_follow_lt_tz(correlate, tmp_path, monkeypatch):
    """`tcpdump -tttt` writes local time with no offset.

    Read as UTC on a probe set to Europe/Berlin, every L2 line lands two hours
    away from the ping data it is meant to corroborate - wider than most events
    worth correlating, and the correlation still looks convincing.
    """
    write_l2(tmp_path / "l2", "2026-08-08", [
        stp_line("2026-08-08 14:00:02.100000", flags="Topology change"),
    ])
    start = 1786190400.0                      # 12:00 UTC = 14:00 Europe/Berlin

    monkeypatch.setenv("LT_TZ", "Europe/Berlin")
    assert len(correlate.stp_changes(tmp_path / "l2", start, start + 10)) == 1

    monkeypatch.setenv("LT_TZ", "UTC")
    assert correlate.stp_changes(tmp_path / "l2", start, start + 10) is None


def test_unknown_lt_tz_falls_back_to_utc(correlate, tmp_path, monkeypatch):
    """A typo in LT_TZ must not cost a measurement window."""
    monkeypatch.setenv("LT_TZ", "Europe/Nowhere")
    write_l2(tmp_path / "l2", "2026-08-08", [
        stp_line("2026-08-08 12:00:02.100000", flags="Topology change"),
    ])
    start = 1786190400.0

    assert len(correlate.stp_changes(tmp_path / "l2", start, start + 10)) == 1


# ---------------------------------------------------------------------------
# The matrix has to follow the data directory
# ---------------------------------------------------------------------------

MATRIX = "192.0.2.1 gw fabric-ref\n192.0.2.20 ts01 symptom\n"


def test_targets_beside_the_data_win_over_the_configured_default(correlate, tmp_path,
                                                                 monkeypatch):
    """A probe's own matrix beats the one this installation happens to carry.

    LT_TARGETS arrives from an EnvironmentFile - it describes the local probe,
    not whichever directory is being analysed.
    """
    node = tmp_path / "probe2"
    (node / "ping").mkdir(parents=True)
    (node / "targets.conf").write_text(MATRIX)
    monkeypatch.setenv("LT_TARGETS", str(tmp_path / "local.conf"))

    assert correlate.resolve_targets(node / "ping") == node / "targets.conf"


def test_foreign_data_directory_without_a_matrix_aborts(correlate, tmp_path):
    """Analysing a probe's data against another probe's matrix is silent.

    Labels only the remote matrix knows never reach the table; labels only the
    local one knows appear as measurement gaps. Neither shows up as an error.
    Measured once: seven rows became five, and the two that vanished were the
    two the second measurement point had been built for. A stop is the lesser
    evil, so this stops.
    """
    node = tmp_path / "probe2"
    (node / "ping").mkdir(parents=True)

    with pytest.raises(correlate.TargetsUnresolved) as exc:
        correlate.resolve_targets(node / "ping")

    # The message has to name the remedy, not just the problem.
    assert "targets.conf" in str(exc.value)
    assert "--targets" in str(exc.value)


def test_explicit_targets_always_win(correlate, tmp_path):
    """--targets is the escape hatch and outranks a file beside the data."""
    node = tmp_path / "probe2"
    (node / "ping").mkdir(parents=True)
    (node / "targets.conf").write_text(MATRIX)
    chosen = tmp_path / "chosen.conf"
    chosen.write_text(MATRIX)

    assert correlate.resolve_targets(node / "ping", str(chosen)) == chosen


def test_the_configured_data_directory_still_uses_lt_targets(correlate, tmp_path,
                                                             monkeypatch):
    """Single-probe operation must not be made harder by the rule above.

    For the directory this installation itself describes, LT_TARGETS is the
    right answer - that is what it was set for.
    """
    monkeypatch.setattr(correlate, "DEFAULT_PING_DIR", tmp_path / "ping")
    (tmp_path / "ping").mkdir()
    monkeypatch.setenv("LT_TARGETS", str(tmp_path / "local.conf"))

    assert correlate.resolve_targets(tmp_path / "ping") == tmp_path / "local.conf"


# ---------------------------------------------------------------------------
# Pitfall 8: an outage in an unjudged role must not read as "clean"
# ---------------------------------------------------------------------------


def test_outage_in_an_unjudged_role_is_not_reported_as_clean(correlate):
    """switch-ref carries no verdict - but it must not produce one either.

    Without this branch, an outage in a role that no branch examines falls
    through to "no target had an outage" - a statement the table printed in the
    same output contradicts.
    """
    targets = {"sw-floor": "switch-ref", "ts01": "symptom"}
    results = {
        "sw-floor": {"affected": True, "offline": False},
        "ts01": {"affected": False, "offline": False},
    }
    v, why = correlate.verdict(results, targets)

    assert v == "OUTAGE OUTSIDE THE JUDGEMENT MATRIX"
    assert "sw-floor" in why


def test_no_data_at_all_is_unclear_not_clean(correlate):
    """All targets unmeasured must never come out as a clean network."""
    targets = {"ts01": "symptom", "gw": "fabric-ref"}
    v, _ = correlate.verdict({"ts01": None, "gw": None}, targets)
    assert v == "UNCLEAR"


def test_outage_outside_the_symptom_path_is_not_reported_as_clean(correlate):
    """A clean symptom host does not make the network clean.

    The same rule as the test above, one branch further down and much easier to
    miss: the symptom host answers throughout while a judging role fails hard.
    Until 0.2.0 this printed "NETWORK CLEAN" over a table in which the gateway
    was out for 299 s - reproducible with the shipped synthetic data. The label
    is what travels into a mail subject, and that one said the opposite of its
    own table.
    """
    targets = {"ts01": "symptom", "gw": "fabric-ref"}
    results = {
        "ts01": {"affected": False, "offline": False},
        "gw": {"affected": True, "offline": False},
    }
    v, why = correlate.verdict(results, targets)

    assert v == "OUTAGE OUTSIDE THE SYMPTOM PATH"
    assert "CLEAN" not in v
    assert "gw" in why


def test_nothing_failed_at_all_is_still_network_clean(correlate):
    """The other side of the same boundary: CLEAN must stay reachable.

    A label that never appears is as useless as one that appears wrongly.
    """
    targets = {"ts01": "symptom", "gw": "fabric-ref"}
    results = {
        "ts01": {"affected": False, "offline": False},
        "gw": {"affected": False, "offline": False},
    }
    v, _ = correlate.verdict(results, targets)

    assert v == "NETWORK CLEAN"
