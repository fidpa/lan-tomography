# LAN Tomography

![CI](https://github.com/fidpa/lan-tomography/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Bash](https://img.shields.io/badge/Bash-4.0%2B-blue)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Linux-lightgrey)
![Status](https://img.shields.io/badge/Status-pre--release-orange)

**Distributed probes for narrowing down intermittent network faults. Every result
in a documented format you can re-analyse as often as the fault requires, plus a
catalogue of the ways such measurements mislead you.**

> The tooling is not the point. Anyone can write a ping loop or an SNMP poller.
> What is hard to come by is the catalogue of **analysis pitfalls**, the
> measurements that look plausible and are wrong, together with the **tomography
> idea**: choosing probe locations and a target matrix so the sections you care
> about become individually determinable, instead of collecting targets by gut
> feeling.

## Why not Wireshark, LibreNMS or ntopng

An intermittent fault is a bad fit for the tools built for it. Wireshark,
LibreNMS and ntopng are built around a console somebody watches. But the
interesting minute was 03:14 last Tuesday, the evidence is spread over a dozen
machines, and what helps is not a live view but a record you can question again
every time your understanding of the fault changes. Nobody stands up a
monitoring platform for a single fault that may be gone next week, and one that
is already running was configured for a different question.

So every tool here writes plain, line-oriented output in a [documented
format](docs/reference/log-formats.md): nothing locked inside a capture file or
behind a query language. The analysis is scriptable, re-running it against a new
hypothesis costs one command, and each analysis tool is a single file you can
read in one sitting and adapt.

It matters because the early readings are wrong. Nine days of measurement
produced a correction table of 23 statements that were treated as established
and later fell, and one detection method that had to be withdrawn. Evidence you
can interrogate again is what makes that recoverable, and the question of what
a disagreement between probes actually proves is where this repository puts its
weight.

**It does not assume you administer the network, and it does not assume you
don't.** This grew out of a network its owner had administered by an external
provider, so the method starts from what a handful of Linux machines can
establish on their own and treats switch data as something you may only acquire
later: in the [case study](docs/explanation/case-study.md), on Day 5, once the
probe data had made the case for it. Administer the network yourself and you
start further along. The target matrix, the pitfalls and the verdict
logic are the same either way.

## Features

- **Continuous probes.** ICMP per target, TCP handshake timing, packet rates
  split by unicast/broadcast/multicast, passive STP capture. Network-side core
  runs on the Python standard library alone.
- **Switch interrogation over SNMPv2c.** Port counters and forwarding-database
  movements, from a dependency-free SNMP client that fits in one file: `wc -l
  src/lib/snmp.py` says 274 lines, and the BER encoder and decoder are about
  half of them. No agent, no package to install on a production box.
- **Correlation with a verdict.** Holds symptom windows against the ping matrix
  and names the network *section* that can account for them, with the switch's
  own STP topology changes for the same seconds printed underneath. The matrix
  follows the data directory, so one probe's logs are never read against
  another probe's target list.
- **Switch discards against a baseline, not against zero.** A switch discards
  packets in normal operation; the excursion over its own median is the finding,
  and a window with sampling gaps is reported as inconclusive rather than quiet.
- **ARP conflict detection.** One address answered from two MACs produces
  exactly the damage pattern of an intermittent fault, and both devices report
  themselves healthy.
- **A pitfalls catalogue with tests.** 47 entries in
  [pitfalls.md](docs/explanation/pitfalls.md), one per way these measurements
  mislead; 20 of them name the test that pins them.
- **Synthetic sample data.** The analysis is demonstrable end to end without a
  single byte of anyone's real traffic.
- **Liveness watching.** A dead probe and a healthy network both produce
  silence.

## Known limitations

- **Localises sections, not devices.** A `FABRIC` verdict says the fault is in
  the shared path, not which port. Going from a section to a device needs the
  switch's own data, a capture, or somebody walking to the rack.
- **`waves.csv` is an input.** Only you know what your symptom is;
  `src/analyze/waves-from-log.py` builds it from any timestamped log.
- **The thresholds are somebody else's measurements.** Derive your own; the
  method chapter explains how.
- **SNMP counters go blind exactly when it matters**
  ([D2](docs/explanation/pitfalls.md#d2-snmp-counters-go-blind-exactly-when-it-matters)).
  Measured: missing for 12 of 18 flood minutes, and not randomly. A quiet
  timeline is not an all-clear.
- **The case study ends without a resolution.** Four interventions landed at
  once, the symptom stopped, and nothing was thereby attributed. That is the
  honest outcome, not a gap in the write-up.

## Quick start

```bash
git clone https://github.com/fidpa/lan-tomography
cd lan-tomography

# See it work before installing anything
examples/synthetic/generate.py --out /tmp/demo --days 4
LT_PING_INTERVAL=1 src/analyze/correlate.py \
    --ping-dir /tmp/demo/ping --waves /tmp/demo/waves.csv
```

Then [getting started](docs/tutorial/getting-started.md) for a real measurement.

## Key concepts

**Rank, not target count.** Treat each measured path as the sum of the sections
it crosses, a linear system. What you can determine is its *rank*. Adding
targets behind the same bottleneck adds rows without adding rank: ten servers in
one rack, measured from one probe, tell you about one uplink. The full table is
in [the method chapter](docs/explanation/target-matrix.md#a-worked-example).

In the campaign this came from, three probes and eight targets gave rank 9
against 11 sections, which made exactly one section individually determinable.
Adding one target that *terminates on a switch* raised it to 10 and made four.

**Some targets must carry no verdict.** A switch answers ICMP from its
management CPU, not its forwarding path
([D1](docs/explanation/pitfalls.md#d1-a-switchs-management-ip-measures-its-cpu-not-its-forwarding-path)).
Measured: apparent outages up to 24 seconds while every target *behind* that
switch stayed clean. Keep the target,
because it raises the rank, but never let it decide anything.

**"No data" is not "no loss".** An unmeasured window comes back as `None`,
never as `0`. Four tests pin that, three in the ping analysis and one in the
switch report; `missing_log_file_yields_none_not_zero_loss` is the one to read
first. Collapsing the two produces a clean bill of health for a window nobody measured
([B8](docs/explanation/pitfalls.md#b8-a-missing-file-is-not-zero-loss)).

## Requirements

- Linux, Bash 4.0+, Python 3.11+
- `iputils-ping`, `tcpdump`, `ethtool`, `zstd`, `flock` (util-linux)
- `ssh` and `rsync` on the collecting machine, for pulling data off probe nodes
  and for watching their units. A single-probe measurement needs neither.
- `CAP_NET_RAW` (+ `CAP_NET_ADMIN` for captures); the shipped units grant these
  ambiently
- Optional: `pytest`, `ruff` for development
- Optional: `impacket`, `python-evtx` (`requirements.txt`), needed **only** by
  `src/contrib/evtx-peek.py`, the only file under `src/` that imports anything
  outside the standard library. Everything on the network side runs on the
  standard library alone, which is deliberate: a diagnostic toolkit that needs
  a package install before it can measure anything is one you cannot deploy
  during an incident.

## Documentation

Start with **[Pitfalls](docs/explanation/pitfalls.md)**. It is the core of this
repository and the part that saves time.

| | |
|---|---|
| [Pitfalls](docs/explanation/pitfalls.md) | The catalogue: measurements that look plausible and are wrong |
| [The method](docs/explanation/target-matrix.md) | Placing probes, assigning roles, deriving thresholds |
| [Proving a loop](docs/explanation/proving-a-loop.md) | Three detection methods, and one that was withdrawn |
| [Case study](docs/explanation/case-study.md) | The investigation this grew out of, with its corrections and its unresolved ending |
| [Log formats](docs/reference/log-formats.md) | Every format defined, so you can feed in your own data |
| [Configuration](docs/reference/configuration.md) | Every setting |
| [Getting started](docs/tutorial/getting-started.md) | A first measurement |
| [Full index](docs/README.md) | Everything |

## Status

Pre-release, and staying on 0.x for a while. Tooling, method, pitfalls and the
[case study](docs/explanation/case-study.md) are complete.

## Contributing

A pitfall you hit is worth more than a bug report. If a tool here led you to a
confident, incorrect conclusion, that is the single most valuable thing you can
send. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT License - see [LICENSE](LICENSE) for details.

## Author

Marc Allgeier ([@fidpa](https://github.com/fidpa))

## See Also

- [linux-monitoring-templates](https://github.com/fidpa/linux-monitoring-templates): reusable monitoring scaffolding
- [bash-production-toolkit](https://github.com/fidpa/bash-production-toolkit): the Bash library conventions this project follows
- [ubuntu-server-security](https://github.com/fidpa/ubuntu-server-security): hardening for the machines the probes run on
