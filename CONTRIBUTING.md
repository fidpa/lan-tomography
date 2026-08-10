# Contributing to lan-tomography

Thanks for considering a contribution. This project has an unusual centre of
gravity, so it is worth saying up front what is most valuable here.

## 📜 Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By
participating you are expected to uphold it.

## 🎯 What is most welcome

**A pitfall you hit.** The core of this repository is
`docs/explanation/pitfalls.md` — measurements that look plausible and are wrong.
If a tool here led you to a confident, incorrect conclusion, that is the single
most valuable thing you can report, more so than a crash. Please include:

- what the data looked like
- what you concluded from it
- what turned out to be true instead
- the cross-check that would have caught it

A pitfall report is a good issue even if you cannot supply a fix.

**A method that did not work.** This project deliberately documents a circuit
test that was retracted as unfit — it returned the same factor in event windows
and in quiet windows, so its negative result meant *blind*, not *no loop*.
Negative results about detection methods are in scope and will be kept.

## 🚀 Getting Started

### Prerequisites

```bash
# Debian / Ubuntu
sudo apt install shellcheck tcpdump zstd python3

# Development extras
pip install -r requirements-dev.txt     # pytest, ruff
pip install -r requirements.txt         # only for src/contrib/
```

The network-side core runs on the Python standard library alone. Only
`src/contrib/` needs third-party packages.

### Project Structure

```
src/lib/        shared shell library and the SNMP client
src/probes/     continuous measurement (ICMP, TCP, packet rates, L2 capture)
src/switch/     SNMP port counters and forwarding-database polling
src/analyze/    correlation and reporting
src/events/     event-driven watchers
src/ops/        scheduling, log rotation, probe-node sync
src/node/       what gets deployed onto a remote probe
src/contrib/    optional tooling with third-party dependencies
docs/           Diátaxis: tutorial / how-to / reference / explanation
examples/       synthetic sample data and its generator
tests/          one test per documented pitfall, wherever it lives in code
```

### Local Development

```bash
shellcheck -x src/**/*.sh
ruff check .
pytest tests/ -v
```

## 🐛 Reporting Bugs

Open an issue with: what you ran, what you expected, what happened, and the
relevant log excerpt. **Redact addresses, hostnames and MAC addresses** before
pasting — see the note on sample data below.

## 🔀 Pull Requests

1. Fork and branch from `main`.
2. Keep the change focused; one concern per PR.
3. Add a test if the change touches parsing or judgement logic.
4. Run shellcheck, ruff and pytest locally.
5. Update `CHANGELOG.md` under `[Unreleased]`.

## 📐 Coding Standards

### Bash

- `set -uo pipefail`. **Not `-e`** — the measurement loops must survive a failing
  probe rather than exit.
- Every executable understands `--help`. The help text *is* the file's header
  comment, emitted by `lt_usage()`. Do not add a second copy: it will drift, and
  it has, visibly, in the code this project came from.
- Source the shared library rather than reimplementing logging.
- Long-running scripts must be replaced atomically (write a sibling file, then
  `mv`). Editing a running script in place makes bash resume at its byte offset
  in the new file.

### Python

- Standard library only, outside `src/contrib/`. This is deliberate: a probe host
  is often a machine you are allowed to touch exactly once.
- `ruff check` clean.
- Parsing is separated from I/O so it can be tested without fixtures or mocks.

### Documentation

- English throughout, including code comments.
- Sample addresses from RFC 5737 (`192.0.2.0/24`, `198.51.100.0/24`,
  `203.0.113.0/24`) and hostnames from RFC 2606 (`example.com`). Never a real
  address, even a private one.
- Distinguish **measured**, **inferred** and **open**. That separation is the
  method, not a stylistic preference.

## 📊 Sample data

Never contribute a capture or log from a real network. A pcap contains
everything that crossed the wire. If you need to demonstrate something, extend
`examples/synthetic/` so the generated data carries the signature you want to
show.

## 🏷️ Release Process

Releases follow Semantic Versioning and are cut by tagging `vX.Y.Z` on `main`.

## 🙏 Recognition

Contributors are credited in release notes.
