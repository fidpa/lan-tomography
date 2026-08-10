"""Tests for the switch report's verdict logic.

The interesting cases are not the discards. They are the three ways a quiet
result can arise, which must stay distinguishable:

    nothing was discarded          -> a falsification, the strongest answer
    discards within the baseline   -> the window is not explained by this
    the switch was not answering   -> no answer at all is available

Collapsing the third into the first is how a device's silence becomes its
innocence.
"""

from __future__ import annotations

import pytest
from conftest import load_script


@pytest.fixture(scope="session")
def sr():
    return load_script("src/analyze/switch-report.py")


def finding(port="5", total=0, per_min=0.0, baseline=0.0, factor=0.0, spike=False):
    return {"port": port, "total": total, "per_min": per_min,
            "baseline": baseline, "factor": factor, "spike": spike, "samples": 10}


def test_nothing_discarded_is_no_discards_not_within_baseline(sr):
    """All-zero findings must not read as "it discarded, but normally".

    The original version returned WITHIN BASELINE for a window in which
    absolutely nothing was discarded, because the port list was non-empty.
    """
    result = {"findings": [finding(), finding(port="23")], "gaps": 0}
    v, _ = sr.verdict(result)
    assert v == "NO DISCARDS"


def test_discards_at_baseline_level_are_within_baseline(sr):
    result = {"findings": [finding(total=300, per_min=30.0, baseline=28.0, factor=1.07)],
              "gaps": 0}
    v, _ = sr.verdict(result)
    assert v == "WITHIN BASELINE"


def test_a_spike_is_an_excursion(sr):
    result = {"findings": [finding(port="23", total=9000, per_min=900.0,
                                   baseline=12.0, factor=75.0, spike=True)],
              "gaps": 0}
    v, why = sr.verdict(result)
    assert v == "EXCURSION"
    assert "port 23" in why


def test_a_sampling_gap_makes_a_quiet_window_inconclusive(sr):
    """Pitfall D2, enforced.

    SNMP counters were measured missing for 12 of 18 flood minutes, and not
    randomly. A window with gaps and no discards says nothing about the switch,
    and must not be reported as if it did.
    """
    result = {"findings": [finding()], "gaps": 2}
    v, why = sr.verdict(result)
    assert v == "INCONCLUSIVE"
    assert "not answering" in why


def test_a_spike_survives_a_gap(sr):
    """A gap weakens a quiet reading, not a loud one.

    If the switch DID report a large excursion, the fact that it also missed a
    sample does not make that excursion go away.
    """
    result = {"findings": [finding(total=9000, per_min=900.0, baseline=12.0,
                                   factor=75.0, spike=True)],
              "gaps": 3}
    v, _ = sr.verdict(result)
    assert v == "EXCURSION"


def test_zero_discards_against_zero_baseline_is_not_infinite(sr):
    """factor = per_min / baseline must not be inf when nothing was discarded.

    "Zero is infinitely more than zero" printed as `inf` in a report column,
    which reads as an enormous excursion to anyone skimming.
    """
    records = [{
        "ts": "2026-08-08T12:00:00+00:00",
        "_dt": sr.datetime.fromisoformat("2026-08-08T12:00:00+00:00"),
        "interval_s": 60.0,
        "ports": {"5": {"d": {"out_discards": 0}}},
    }]
    wave = sr.datetime.fromisoformat("2026-08-08T12:00:00+00:00")
    result = sr.analyse_wave(records, wave, "out_discards")
    assert result["findings"][0]["factor"] == 0.0


def test_no_records_in_window_returns_none_not_a_verdict(sr):
    """"No data" is not "no discards", and must not become one."""
    assert sr.analyse_wave([], sr.datetime.now(sr.timezone.utc) if hasattr(sr, "timezone")
                           else sr.datetime.fromisoformat("2026-08-08T12:00:00+00:00"),
                           "out_discards") is None


def test_unknown_delta_is_skipped_not_read_as_zero(sr):
    """A null delta means the counter wrapped or the switch rebooted.

    Reading it as 0 invents a quiet minute out of an unknown one.
    """
    rec = {"ports": {"5": {"d": {"out_discards": None}},
                     "23": {"d": {"out_discards": 7}}}}
    assert sr.port_deltas(rec, "out_discards") == {"23": 7}
