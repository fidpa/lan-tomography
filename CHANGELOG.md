# Changelog

All notable changes to `lan-tomography` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Later releases group changes as Added / Changed / Fixed; the first one does not,
because in a release where everything is new that heading separates nothing. Its
sections say what the parts *are* instead.

**On versioning here:** this project stays on 0.x while the configuration keys,
log formats and role vocabulary can still change. While it does, content
additions — tools, chapters, pitfalls — are **patch** bumps: on 0.x the minor
number is the only signal available for a change that would break someone's
setup, and spending it on "more of the same, in more detail" leaves nothing to
say it with. A new configuration key that existing installations can ignore is
not that kind of change.

Minor bumps are reserved for changes to the interfaces above, and 1.0.0 for the
point where those are stable enough that changing them would deserve a major
bump. That is deliberately a long way off: this toolkit has been used on one
network, and one network is not enough to call an interface settled.

## [Unreleased]

## [0.1.0] — 2026-08-10

First release: the tools, the method, the pitfalls catalogue and the case study
they all came out of.

The tooling is not the point of this repository. Anyone can write a ping loop or
an SNMP poller. What is hard to come by is the catalogue of analysis pitfalls —
the measurements that look plausible and are wrong — and the tomography idea:
choosing probe locations and a target matrix so the sections you care about
become individually determinable, instead of collecting targets by gut feeling.

### Probes and capture

- **`ping-target.sh`**, **`tcp-probe.py`**, **`pktrate.sh`**, **`l2-sniffer.sh`**
  — ICMP per target, TCP handshake timing, packet rates split by
  unicast/broadcast/multicast, passive STP capture. The network-side core runs
  on the Python standard library alone, deliberately: a diagnostic toolkit that
  needs a package install before it can measure anything is one you cannot
  deploy during an incident.
- **`frame-capture.sh`** — targeted passive capture of one frame class, one
  profile per service instance: `broadcast` (names the source of a flood a
  switch reports only as "behind port N"), `loop-detect` (ethertype 0x8899,
  whose per-frame identifier arriving twice *is* a closed loop), `roaming`
  (ethertype 0x890d, the 802.11r chatter that preceded surges by 0.1 to 4
  seconds), and `custom`.

  Daily profiles get `-U`. Without it tcpdump holds frames until 8 KB has
  accumulated, so a profile producing a kilobyte a day first becomes readable
  after about four days — and until then an empty file is indistinguishable
  from "no frames matched", which is the observation the capture exists to make.
- **`flood-capture.sh`** — copies the broadcast ring out before it is
  overwritten, on either of two triggers: a rate excursion, or a precursor
  frame. The second is what keeps a measurement alive after an intervention —
  once the fault was isolated in the campaign this came from, the floods
  stopped and only the precursors could still answer whether the circuit was
  still forming.

  Always keeps the current ring file **and** the one before it: the rotation
  fell inside an event once, four seconds after it began, and the run-up is
  where the precursor sits.

### Switch interrogation

- **`switch-probe.py`** and **`fdb-probe.py`** — port counters and
  forwarding-database movements over SNMPv2c, from a ~70-line BER client. No
  agent, no package to install on a production box. GetBulk rather than GetNext,
  because politeness towards a production switch is not cosmetic.

### Analysis

- **`correlate.py`** — holds symptom windows against the ping matrix and names
  the network *section* that can account for them.
- **`switch-report.py`** — switch discards against each port's own baseline
  rather than against zero. A switch discards packets in normal operation, so
  "it discarded during the window" is not a finding; the excursion over its own
  median is. A window containing sampling gaps is INCONCLUSIVE, never clean:
  SNMP counters were measured missing for 12 of 18 flood minutes and not
  randomly (p = 2.8·10⁻¹⁴), because the agent stops answering while the device
  is busy. Reading a gap as a zero turns a device's silence into its innocence.
- **`arp-anomalies.py`** — IP conflicts, gratuitous ARP and the loudest senders,
  read out of the existing capture ring. A conflict is the one finding there
  that is a verdict rather than a lead.
- **`pktrate-scan.py`**, **`waves-from-log.py`** — flood and surge detection,
  and the symptom-window input built from any timestamped log.
- **`contrib/evtx-peek.py`** — judges a Windows event log over SMB2 range reads
  instead of transferring it. The logs in the campaign this came from ran to
  314 MB, and pulling one across the link under investigation competes with the
  traffic being measured; a dozen 64 KB chunks answer "is this worth fetching"
  for 768 KB. `--coverage` locates the ring buffer's wrap point by bisection —
  not a refinement, since on a wrapped log first-record-minus-last-record
  reports a span that is wrong by up to the whole retention period, and wrong in
  the reassuring direction. The only tool here with third-party dependencies
  (`impacket`, `python-evtx`), which is why it sits in `contrib/`.

### Operations

- **`probe-node.sh`**, **`sync-node.sh`**, **`compress-logs.sh`**,
  **`event-watch.sh`**, **`liveness-check.sh`**. Liveness watching is not
  optional: a dead probe and a healthy network both produce silence, and
  `systemd` cannot see a single dead ping loop inside a running service.

  `sync-node.sh` pulls a probe's text logs without `--delete`, deliberately: a
  day compressed on the probe would otherwise disappear here when it rotates
  there.

### Documentation

- **[Pitfalls](docs/explanation/pitfalls.md)** — the core chapter. **44**
  documented ways these measurements mislead, each with the symptom, why it
  looks plausible, and the cross-check that settles it. **17** are pinned by a
  named test.
- **[The method](docs/explanation/target-matrix.md)** — the rank calculation
  behind "tomography": how to place probes and assign roles so the sections you
  care about become individually determinable, and how to derive thresholds
  rather than copy them.
- **[Proving a loop](docs/explanation/proving-a-loop.md)** — three detection
  methods with their blind spots, and a fourth that was withdrawn after it
  returned the same factor in event and quiet windows. It had a control window
  and passed it; only a *positive* control, a case known to be true, exposed it.
- **[Case study](docs/explanation/case-study.md)** — nine days of measurement on
  a network the investigators did not administer, condensed from the original
  measurement diary. It carries the four things such a write-up usually leaves
  out: the falsification criteria fixed *before* the measurement windows, a
  correction table of 23 statements treated as established and later fallen, the
  withdrawn detection method, and an ending that is not a resolution. Four
  interventions took effect at once, the symptom stopped, and nothing was
  thereby attributed.
- **[Log formats](docs/reference/log-formats.md)** — every format this toolkit
  writes or reads, defined. Without it you cannot feed in your own data, and you
  cannot check somebody else's analysis.
- **[Configuration](docs/reference/configuration.md)**, plus a tutorial, two
  how-to guides and a documentation index.

### Infrastructure

- **21 systemd unit templates** and an installer that resolves their
  placeholders, verifies none are left, and enables nothing by itself.
- **Synthetic sample data** carrying the same signatures as the real
  measurements, so the analysis is demonstrable end to end without a byte of
  anyone's real traffic.
- **One version source.** `VERSION` at the repository root is the only place a
  version is written; `src/lib/common.sh` exposes it as `LT_VERSION`,
  `src/lib/version.py` does the same for the Python tools, and `pktrate.sh` and
  `install.sh` read the file directly because they are deliberately standalone.
- **79 tests**, and CI across ShellCheck, ruff, pytest, syntax validation and
  systemd unit parsing. The pytest job installs `requirements.txt` as well —
  without it the contrib tests skip themselves and the run reports green for a
  file it never imported.
- **Release notes come from this changelog.** `release.yml` cuts the section for
  the tag being pushed and uses it as the release body, rather than notes
  generated from commit messages. Two gates come with it: the workflow fails if
  the section is missing or empty, and if the tag does not match `VERSION`.

[Unreleased]: https://github.com/fidpa/lan-tomography/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/fidpa/lan-tomography/releases/tag/v0.1.0
