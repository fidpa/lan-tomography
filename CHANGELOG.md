# Changelog

All notable changes to `lan-tomography` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Later releases group changes as Added / Changed / Fixed; the first one does not,
because in a release where everything is new that heading separates nothing. Its
sections say what the parts *are* instead.

**On versioning here:** this project stays on 0.x while the configuration keys,
log formats and role vocabulary can still change. While it does, content
additions (tools, chapters, pitfalls) are **patch** bumps: on 0.x the minor
number is the only signal available for a change that would break someone's
setup, and spending it on "more of the same, in more detail" leaves nothing to
say it with. A new configuration key that existing installations can ignore is
not that kind of change.

Minor bumps are reserved for changes to the interfaces above, and 1.0.0 for the
point where those are stable enough that changing them would deserve a major
bump. That is deliberately a long way off: this toolkit has been used on one
network, and one network is not enough to call an interface settled.

## [Unreleased]

## [0.2.4] - 2026-08-28: The SNMP blind-spot p-value reads the same in every document

Two follow-ups to the editorial pass in 0.2.3, both of them cases where one
value was written two ways.

### Fixed

- **The p-value behind the SNMP blind-spot finding is written the same way in
  all three places that cite it.** `docs/explanation/case-study.md` set the
  exponent as a superscript while `docs/explanation/pitfalls.md` and
  `docs/reference/log-formats.md` wrote `p = 2.8e-14`. A reader comparing the
  case study against the pitfall it produced saw two notations for one measured
  value, and the superscript form does not survive a copy into a terminal or a
  plain-text note. The value itself is unchanged.
- **A release body starts where its changelog section starts.** The `awk` in
  `release.yml` left the blank line that follows the version heading, so every
  body the CI produced began one line lower than the section it was cut from.
  GitHub renders both the same; a byte comparison of body against section does
  not, and that comparison is how the two are kept in step. `sed -e '/./,$!d'`
  now drops the line in the workflow rather than by hand afterwards.

## [0.2.3] - 2026-08-28: Release titles come from the changelog, and this file is ASCII

An editorial pass over this file and the release pages it feeds. No tool, no
unit and no documented value changed: every measured number, path and function
name in the older sections is the one that was published with its tag.

### Changed

- **A release page carries a headline instead of repeating its own version
  number.** The number already sits beside the title in the release list, so a
  title of `v0.2.2` stated it twice and said nothing else. The headline now
  lives in the version heading in this file and `release.yml` reads it from
  there into the release `name:`, so the title cannot drift from the notes
  below it. The five published releases were retitled from their sections.
- **The version headings read `## [X.Y.Z] - YYYY-MM-DD: headline`**, with a
  plain hyphen. `release.yml` anchors its extraction on `^## \[X.Y.Z\]`, so
  what follows the closing bracket is free and the separator never entered the
  cut.
- **This file is ASCII.** The em dashes that 0.2.1 removed from the
  documentation are gone from here as well, rewritten rather than substituted
  in the same way, and the superscript in the `switch-report.py` p-value now
  reads `p = 2.8e-14`.
- **The [0.1.1] entry about a mislabelled access point no longer names the
  address it was reporting.** Naming the value put it on a release page and in
  this file, which is the opposite of what that fix was for. The entry says
  what was wrong and what now holds; the value is not part of it.
- **Two entries lead with what changed for the operator** rather than with the
  code that changed: the `zstd --rm` data-loss window in [0.1.1] and the
  `log_files_ignores_partial_archives` test in [0.2.2].
- **The v0.1.0 release page matches its section again.** Its body still
  described the `loop-detect` profile as a copy-counting circuit test, the
  reading this project withdrew and 0.1.1 corrected everywhere else. All five
  bodies were set from their sections in this file.

## [0.2.2] - 2026-08-16: Sync and compression stop failing on their own housekeeping

Two failure modes that only appear once probes compress their own days, which
is what `install.sh --role node` has been setting up all along. Both were found
while running this toolkit's ancestor on the network the case study describes.

### Fixed

- **`sync-node.sh` reported a failure on a schedule.** rsync exit code 24 means
  "a source file vanished while transferring", and this project produces it in
  normal operation: the probe compresses yesterday's log on its own timer, and
  a pull that overlaps watches a `.log` become a `.log.zst` mid-flight.
  Everything else transferred and the archive arrives on the next pass, but the
  run was logged as `rsync of ping/ from <node> failed` with no exit code shown.
  A unit that reports an error on a schedule teaches people to stop reading its
  output, and the overlap cannot be scheduled away: both timers fire relative
  to boot, on two different machines, so their relative position drifts and
  eventually coincides. Exit 24 is now logged as what it is; every other
  non-zero code still fails, and now says which one.
- **An interrupted compression left an archive nothing would ever replace.**
  `compress-logs.sh` wrote the archive under its final name, so a run killed
  mid-compression left a truncated `.log.zst` that the next run skipped with
  "archive already exists". No data was lost at that point, since the source is
  removed only after `zstd -t` passes, but the day was never archived again and
  the pair sat there looking like leftovers, waiting for the next person to
  delete the `.log` beside its archive. Archives are now written to
  `<name>.zst.partial`, renamed only after they verify, and leftovers from an
  interrupted run are cleared at the start of the next one.

### Added

- **Pitfall B10, "An archive that exists is not an archive that reads back"**,
  the archive-level counterpart to E7, covering why compression writes under a
  name nothing looks for. Counted again afterwards:
  `docs/explanation/pitfalls.md` has **47** entries, **20** of which name a
  test, and every test name in it resolves.
- **The partial-archive protection is pinned by a test.**
  `log_files_ignores_partial_archives` covers the half of B10 that lives in
  code. The protection depends on `.zst.partial` falling outside both globs in
  `log_files()`; a suffix that kept the `.zst` ending would remove it without
  any other symptom.

## [0.2.1] - 2026-08-10: The documented numbers match what the code and CI do

Documentation only. Three numbers that were wrong, two dependencies that were
never listed, and a badge that asserted a test result nothing verified. Also a
typographic pass over the documentation: the em dashes went, this file
excepted.

### Fixed

- **The pitfall count in the documentation table still said 44.** It was raised
  to 46 in the feature list for 0.2.0 and missed one line further down, so the
  same page carried both numbers. Measured again: `docs/explanation/pitfalls.md`
  has **46** entries, **19** of which name a test, with 34 references to 33
  distinct test names, all of them resolving.
- **"a ~70-line BER client" was a number with no origin.** Measured:
  `src/lib/snmp.py` is 274 lines, **135 of them code**, of which **62** are the
  BER encoder and decoder. The claim attributed the whole SNMP client to the
  size of its codec. It now states both figures in the same unit.
- **`ssh`, `rsync` and `flock` were used but never listed as requirements.**
  `src/ops/sync-node.sh` needs the first two to pull a probe's data and its
  matrix, `src/events/liveness-check.sh` needs `ssh` to see a remote unit, and
  `src/ops/compress-logs.sh` and `src/events/flood-capture.sh` lock with
  `flock`. The omission mattered most for multi-probe operation, which is the
  premise the whole method rests on. A single-probe measurement needs none of
  the three, and the requirement list now says so.

### Changed

- **The static ShellCheck badge is gone.** It was
  `img.shields.io/badge/ShellCheck-passing`, a hardcoded string that stays green
  whatever the linter does, next to a CI badge that reads the actual run. Two
  statements about one check, one of them unverifiable. `ci.yml` has a
  `ShellCheck` job, so the CI badge already covers it. The remaining badges
  assert properties rather than results, and those were checked too: Python
  3.11+ is forced by `from datetime import UTC`, Bash 4.0+ by `declare -A` and
  `mapfile`.
- **The README no longer restates the 1.0.0 promise.** It said the same thing as
  the head of this file and would have had to be kept in step with it. The
  commitment is unchanged and lives here.
- **Em dashes removed from every document except this changelog.** 220 of them
  across 13 files, rewritten rather than substituted: a dash that carried an
  explanation became a colon, one that carried an afterthought a comma or a full
  stop, and a pair enclosing an aside became parentheses. Two headings changed
  with them, so the anchors were re-checked: 17 documents, 34 anchor links, none
  dead and none unreachable. This file kept its own at that point; 0.2.3
  removed them from here as well.

## [0.2.0] - 2026-08-10: No verdict contradicts its own table, and the matrix travels with the data

The four findings from the same review that 0.1.1 left alone, because each of
them changes what the tools print or what they refuse to do. Three are the same
fault in three places: a tool stating more than it measured. The fourth is data
that was collected for two releases and read by nothing.

### Changed

- **A clean symptom host no longer produces the verdict `NETWORK CLEAN`.** The
  branch fires when the symptom host stayed reachable while something else
  failed, and it printed the word CLEAN over a table showing the failure. It is
  reproducible with this repository's own sample data: a 299-second gateway
  outage, listed in the table as `<-- affected`, under `VERDICT: NETWORK CLEAN`.
  The new label is `OUTAGE OUTSIDE THE SYMPTOM PATH`. The reasoning text was
  already correct; the label was not, and the label is the part that travels
  into a mail subject where the table does not follow. This is the rule the
  neighbouring branch already stated in a code comment: no verdict may
  contradict its own table. `client-path` failures now appear in that reasoning
  text too; they were being dropped from the list.
- **`correlate.py` resolves the target matrix from the data directory, and
  stops rather than guess.** The default was `LT_TARGETS` or
  `config/targets.conf` regardless of which directory was being analysed, while
  `sync-node.sh` writes each probe's data to `$LT_BASE_DIR/<node>/`. Analysing
  one probe against another's matrix fails in both directions at once and in
  silence: labels only the remote matrix knows never appear at all (no row, no
  `NO DATA`, no exit code), and labels only the local one knows appear as gaps
  for targets nobody measured. Measured in the campaign this came from: seven
  table rows became five, and the two that vanished were the `uplink-ref` rows
  the second measurement point had been built for. Resolution order is now
  `--targets`, then `targets.conf` beside the data directory, then `LT_TARGETS`
  **only** for `$LT_BASE_DIR/ping`, the directory this installation itself
  describes, and otherwise an abort naming both remedies. `LT_TARGETS` arrives
  from an `EnvironmentFile`; it is ambient, not a statement about somebody
  else's probe.
- **`probe-node.sh` writes its own matrix to `$LT_BASE_DIR/targets.conf`, and
  `sync-node.sh` pulls it.** So the matrix travels with the data by
  construction and the abort above has a remedy that needs no path-guessing. A
  probe without one is a warning, not an error: the message says the analysis
  will need `--targets`.
- **`switch-report.py` prints each port's link speed.** `switch-probe.py` has
  been collecting `ifSpeed` into every timeline record since 0.1.0, and the
  report's own header warns that discards are ordinary at a gigabit-to-100-Mbit
  step: the caveat was unusable without the number it applies to. Unknown
  prints as `?`, never `0`. A saturated 32-bit gauge prints as `>4 Gbit` rather
  than a confident wrong `4 Gbit`.
- **The synthetic sample data lays a `targets.conf` beside the data**, the way a
  probe directory carries one, so the Quick Start no longer needs `--targets`.
  Port 23 now runs at 100 Mbit, so the speed column has the case its caveat is
  about.

### Added

- **`correlate.py` reads `l2-events-*.log` and counts STP topology-change
  BPDUs in each symptom window.** `l2-sniffer.sh` and `probe-node.sh` have
  written that log since 0.1.0 and `log-formats.md` documented its format; no
  tool read it. A topology change at the second sessions tore down is the
  switch's own account of the same seconds, and it is what the capture was
  built for. The result has three states, not two: `None` when no line falls
  inside the window, an empty result when the capture ran and the topology
  held (a falsification worth having), and the matching lines otherwise. The
  count is of BPDUs, not events: the topology-change flag stays set for the
  length of the TC-while timer, so one change produces a run of flagged hellos,
  and the lines are printed underneath the count so a reader can tell which it
  is.
- **`--l2-dir`, defaulting to `l2/` beside the data directory.** The same
  coupling the matrix follows, and for the same reason: one probe's ping logs
  read against another probe's capture describe two different segments, and
  nothing in the output would say so.
- **A provenance line, `matrix: <path> (<n> targets)`, above the windows.**
  Which matrix was applied is part of the finding. Two probes with different
  matrices produce tables that look alike and mean different things.
- **Pitfall E7, "A capture file that exists is not a capture that ran".** The
  daily L2 file is opened at midnight, so it exists from the day's first second
  and a capture that dies at 09:00 leaves something indistinguishable from a
  quiet day. Coverage therefore comes from the lines, not the file: with an
  `stp` filter a live capture writes a BPDU every two seconds.
- **Pitfall F9, "A target missing from the matrix is missing from the table,
  not from the network".** The measured seven-rows-into-five case above,
  written down where the next person will meet it.
- **The sample generator produces L2 captures, starting on day 3 of the
  campaign.** The earlier days are uncovered on purpose, so the difference
  between `NO DATA` and "no topology changes" is visible in the output of the
  Quick Start rather than only described in the documentation.

### Fixed

- **`--targets` no longer silently ignores which data it is pointed at**, which
  is the defect behind the abort described above.

### Upgrade notes

Three changes to output that break anything parsing it. All three are the
reason this is a minor bump rather than a patch.

**1. A verdict label changed.** Anything matching on the old string (a mail
filter, a `grep` in a wrapper script, a spreadsheet column) needs the new one.
Only this branch is affected; a window in which nothing failed still reports
`NETWORK CLEAN`.

```
before:  VERDICT: NETWORK CLEAN
         The symptom host itself stayed reachable (no outage over 2.5s), so a
         network problem does not explain this window. Side finding: outage on
         gw - not part of the symptom picture, but noted.

after:   VERDICT: OUTAGE OUTSIDE THE SYMPTOM PATH
         The symptom host itself stayed reachable (no outage over 2.5s), so a
         network problem does not explain this window. But the network was not
         clean either: gw (fabric-ref) lost packets for more than 2.5s. Outside
         the symptom picture - and not to be reported as an all-clear.
```

The full vocabulary, nine labels and what each one means, is now listed in
[docs/explanation/target-matrix.md](docs/explanation/target-matrix.md).

**2. `correlate.py` gained two output shapes.** A `matrix:` line before the
first window, and an `L2:` line after each table:

```
matrix: /var/log/lan-tomography/probe2/targets.conf (14 targets)
  L2: 3 STP topology-change BPDU(s) in the window
  L2: no STP topology change in the window (the capture was running)
  L2: NO DATA - no capture covering this window. That is not "no topology changes".
```

**3. `switch-report.py` gained a `speed` column**, second from the left:

```
before:  | port | total | per sample | baseline (median) | factor |
after:   | port | speed | total | per sample | baseline (median) | factor |
```

**And one change to behaviour.** `correlate.py` now exits 2 where it previously
produced a table, if it cannot establish which target matrix belongs to the
data directory. A single-probe installation is unaffected: `$LT_BASE_DIR/ping`
still uses `LT_TARGETS`, and the shipped `lt-correlate.service` runs unchanged.
A multi-probe setup analysing `$LT_BASE_DIR/<node>/ping` will stop until the
probe's matrix sits beside its data, which `sync-node.sh` now does by itself
after the probes are updated, or which one `rsync` puts there by hand:

```bash
rsync -az probe2:/var/log/lan-tomography/targets.conf \
          /var/log/lan-tomography/probe2/targets.conf
```

`--targets` remains the escape hatch and always wins. The stop is deliberate:
the output it replaces was missing rows and said so nowhere.

## [0.1.1] - 2026-08-10: Defects that made a measurement quietly wrong

An independent review against the internal originals this repository was
extracted from. Four of its findings are defects that would have made a
measurement quietly wrong: the class of fault this repository is about, found
in the repository itself.

### Fixed

- **The liveness check counted its own log as measurement data.** It writes to
  `LT_BASE_DIR` on every run, and the freshness scan took the newest `*.log`
  under that directory. From the second run on it could not fire: with the
  shipped 30-minute timer against a 130-minute tolerance, the check reported OK
  with every probe stopped. Measured before and after the fix: 0 minutes of
  apparent data age against the true 300.
- **A day of logs could be lost on nothing but zstd's own report of its own
  run.** `compress-logs.sh` deleted the source with `zstd --rm`, which removes
  the original inside the same invocation, while the comment above it claimed
  the removal happened only after success. It is now compress, `zstd -t`, then
  remove, with the archive deleted and the source kept if verification fails. A
  `flock` came back with it: the timer and one hand-started run were enough to
  have two passes on the same file.
- **The `loop-detect` capture profile documented the withdrawn detector as
  fact.** Its header stated that a repeated identifier in those frames is a
  copy and a copy is a closed loop, and cited the chapter that refutes exactly
  that. A reader following it would rebuild the test this project withdrew. The
  header now says what the profile is good for and warns off the reading.
- **An installed tree could not find its own configuration.** `install.sh` put
  the config in `/etc/lan-tomography`, while `src/lib/common.sh` and the
  `--targets` default of `correlate.py` and `tcp-probe.py` look under the
  installation directory. Every tool run without explicit environment variables
  therefore searched a directory that did not exist, the tutorial's own
  commands among them. The installer now places the examples there and links the
  live files, so the single editable copy stays in `/etc` and the documented
  commands work verbatim. `VERSION` and `examples/` were not installed either,
  so every tool reported its version as `unknown` and the tutorial's
  synthetic-data step had nothing to run.
- **The case study named a real private range as its anonymisation range**, in
  the one paragraph explaining the anonymisation, while `CONTRIBUTING.md`
  requires RFC 5737 and says "never a real address, even a private one", and
  the rest of the repository uses `192.0.2.0/24` with the same host octets. It
  now names the range actually in use, which makes "no address outside RFC 5737
  anywhere in the repository" a check that can be run and stay at zero.
- **Two places carried an access point's address from the measured network**
  rather than the anonymised one the case study uses correctly throughout. The
  same short form therefore denoted two different hosts depending on which page
  it appeared on. Both now follow the published scheme.
- **`event-watch.sh` promised outage detection it does not perform.** Nothing
  in it reads the ping logs. It now says so, because a watcher believed to
  cover outages is worse than none.
- **`event-watch.sh` only ever opened the current day's packet-rate file**, so
  an event starting before midnight was reported as its own tail.
- **`LT_GAP_SECONDS` was a dead setting** in the example configuration:
  documented as the outage threshold, read by nothing. `LT_OUTAGE_THRESHOLD_S`
  is the one that decides.
- **`LT_CAPTURE_DIR` was missing from the configuration reference**, the only
  derived path of the five not listed.
- **`contrib/evtx-peek.py --version` answered with its own script name** while
  the other nineteen tools answer `lan-tomography <version>`. The number was
  right; the prefix made the one check that asks every tool for its version
  report a failure.
- **The withdrawn detector's table conflated its event windows with its
  validation run**, showing 3.86 as "the event window". The five event windows
  were 3.94, 3.83, 3.82, 3.81 and 3.83 against a quiet baseline of 3.95; 3.86
  is what it returned against the storm that exposed it.

### Changed

- CI now runs ShellCheck and `bash -n` over `install.sh`. It sits at the
  repository root and was the one script outside the sweep, while being the
  one that writes to `/etc/systemd/system`.

## [0.1.0] - 2026-08-10: The tools, the method, the pitfalls catalogue and the case study

First release: the tools, the method, the pitfalls catalogue and the case study
they all came out of.

The tooling is not the point of this repository. Anyone can write a ping loop or
an SNMP poller. What is hard to come by is the catalogue of analysis pitfalls
(the measurements that look plausible and are wrong) and the tomography idea:
choosing probe locations and a target matrix so the sections you care about
become individually determinable, instead of collecting targets by gut feeling.

### Probes and capture

- **`ping-target.sh`**, **`tcp-probe.py`**, **`pktrate.sh`**, **`l2-sniffer.sh`**:
  ICMP per target, TCP handshake timing, packet rates split by
  unicast/broadcast/multicast, passive STP capture. The network-side core runs
  on the Python standard library alone, deliberately: a diagnostic toolkit that
  needs a package install before it can measure anything is one you cannot
  deploy during an incident.
- **`frame-capture.sh`**: targeted passive capture of one frame class, one
  profile per service instance: `broadcast` (names the source of a flood a
  switch reports only as "behind port N"), `loop-detect` (ethertype 0x8899, a
  record of who speaks loop detection and when they fall silent, *not* a
  copy-counting circuit test; that reading was withdrawn), `roaming`
  (ethertype 0x890d, the 802.11r chatter that preceded surges by 0.1 to 4
  seconds), and `custom`.

  Daily profiles get `-U`. Without it tcpdump holds frames until 8 KB has
  accumulated, so a profile producing a kilobyte a day first becomes readable
  after about four days, and until then an empty file is indistinguishable
  from "no frames matched", which is the observation the capture exists to make.
- **`flood-capture.sh`**: copies the broadcast ring out before it is
  overwritten, on either of two triggers: a rate excursion, or a precursor
  frame. The second is what keeps a measurement alive after an intervention:
  once the fault was isolated in the campaign this came from, the floods
  stopped and only the precursors could still answer whether the circuit was
  still forming.

  Always keeps the current ring file **and** the one before it: the rotation
  fell inside an event once, four seconds after it began, and the run-up is
  where the precursor sits.

### Switch interrogation

- **`switch-probe.py`** and **`fdb-probe.py`**: port counters and
  forwarding-database movements over SNMPv2c, from a ~70-line BER client. No
  agent, no package to install on a production box. GetBulk rather than GetNext,
  because politeness towards a production switch is not cosmetic.

### Analysis

- **`correlate.py`**: holds symptom windows against the ping matrix and names
  the network *section* that can account for them.
- **`switch-report.py`**: switch discards against each port's own baseline
  rather than against zero. A switch discards packets in normal operation, so
  "it discarded during the window" is not a finding; the excursion over its own
  median is. A window containing sampling gaps is INCONCLUSIVE, never clean:
  SNMP counters were measured missing for 12 of 18 flood minutes and not
  randomly (p = 2.8e-14), because the agent stops answering while the device
  is busy. Reading a gap as a zero turns a device's silence into its innocence.
- **`arp-anomalies.py`**: IP conflicts, gratuitous ARP and the loudest senders,
  read out of the existing capture ring. A conflict is the one finding there
  that is a verdict rather than a lead.
- **`pktrate-scan.py`**, **`waves-from-log.py`**: flood and surge detection,
  and the symptom-window input built from any timestamped log.
- **`contrib/evtx-peek.py`**: judges a Windows event log over SMB2 range reads
  instead of transferring it. The logs in the campaign this came from ran to
  314 MB, and pulling one across the link under investigation competes with the
  traffic being measured; a dozen 64 KB chunks answer "is this worth fetching"
  for 768 KB. `--coverage` locates the ring buffer's wrap point by bisection,
  which is not a refinement: on a wrapped log first-record-minus-last-record
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

- **[Pitfalls](docs/explanation/pitfalls.md)**: the core chapter. **44**
  documented ways these measurements mislead, each with the symptom, why it
  looks plausible, and the cross-check that settles it. **17** are pinned by a
  named test.
- **[The method](docs/explanation/target-matrix.md)**: the rank calculation
  behind "tomography": how to place probes and assign roles so the sections you
  care about become individually determinable, and how to derive thresholds
  rather than copy them.
- **[Proving a loop](docs/explanation/proving-a-loop.md)**: three detection
  methods with their blind spots, and a fourth that was withdrawn after it
  returned the same factor in event and quiet windows. It had a control window
  and passed it; only a *positive* control, a case known to be true, exposed it.
- **[Case study](docs/explanation/case-study.md)**: nine days of measurement on
  a network the investigators did not administer, condensed from the original
  measurement diary. It carries the four things such a write-up usually leaves
  out: the falsification criteria fixed *before* the measurement windows, a
  correction table of 23 statements treated as established and later fallen, the
  withdrawn detection method, and an ending that is not a resolution. Four
  interventions took effect at once, the symptom stopped, and nothing was
  thereby attributed.
- **[Log formats](docs/reference/log-formats.md)**: every format this toolkit
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
  systemd unit parsing. The pytest job installs `requirements.txt` as well:
  without it the contrib tests skip themselves and the run reports green for a
  file it never imported.
- **Release notes come from this changelog.** `release.yml` cuts the section for
  the tag being pushed and uses it as the release body, rather than notes
  generated from commit messages. Two gates come with it: the workflow fails if
  the section is missing or empty, and if the tag does not match `VERSION`.

[Unreleased]: https://github.com/fidpa/lan-tomography/compare/v0.2.4...HEAD
[0.2.4]: https://github.com/fidpa/lan-tomography/releases/tag/v0.2.4
[0.2.3]: https://github.com/fidpa/lan-tomography/releases/tag/v0.2.3
[0.2.2]: https://github.com/fidpa/lan-tomography/releases/tag/v0.2.2
[0.2.1]: https://github.com/fidpa/lan-tomography/releases/tag/v0.2.1
[0.2.0]: https://github.com/fidpa/lan-tomography/releases/tag/v0.2.0
[0.1.1]: https://github.com/fidpa/lan-tomography/releases/tag/v0.1.1
[0.1.0]: https://github.com/fidpa/lan-tomography/releases/tag/v0.1.0
