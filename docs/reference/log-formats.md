# Log formats

Every file this toolkit writes or reads, defined. Without this you cannot feed
in your own data, and you cannot check somebody else's analysis.

This document exists because these formats were, for a long time, only implicit
in the code that read them. Three of the four wrong conclusions this project
recorded came from misreading a format, not from misreading a network.

**Shared conventions**

- Timestamps in measurement lines are the **Unix epoch**, so series from
  different probes correlate without timezone arithmetic.
- Timestamps in **filenames** are local dates (`date +%F`), controlled by
  `LT_TZ`. Set it identically on every probe.
- Daily files roll over at local midnight. Completed days are compressed with
  zstd and keep their name plus `.zst`.
- Reading tools must handle `.log` and `.log.zst` transparently. See
  `log_files()` and `read_log()` in `src/analyze/correlate.py`. A plain
  `glob("*.log")` skips archived days **silently**, and the analysis then runs
  on less data with no error and no exit code.
- A file ending in `.zst.partial` is an archive still being written, or the
  remains of a compression run that was interrupted. It holds nothing the
  `.log` beside it does not also hold, and the next `compress-logs.sh` run
  removes it. Reading tools must ignore it: the suffix exists precisely so
  that a half-written archive cannot be mistaken for a finished one
  (pitfall B10).

---

## Ping log

`<base>/ping/<label>-<YYYY-MM-DD>.log`

Written by `src/probes/ping-target.sh` and `src/node/probe-node.sh`, which just
redirect `ping -D -O -n`. The format is therefore ping's own; the flags are the
design decision.

```
# ---- measurement starts 2026-08-08T12:00:00+00:00 | target 192.0.2.1 (gw) | interval 0.2s ----
PING 192.0.2.1 (192.0.2.1) 56(84) bytes of data.
[1786201899.326352] 64 bytes from 192.0.2.1: icmp_seq=1 ttl=64 time=0.061 ms
[1786201899.833366] no answer yet for icmp_seq=2
[1786201900.033366] From 192.0.2.1 icmp_seq=3 Destination Host Unreachable
```

| Element | Meaning |
|---|---|
| `[…]` | Unix epoch with microseconds, from `ping -D` |
| `icmp_seq=N` | 16-bit sequence number; **wraps**, see below |
| `time=… ms` | round-trip time; its presence means the line is a reply |
| `no answer yet for icmp_seq=N` | from `ping -O`. **Neither a reply nor a loss** |
| `Destination Host Unreachable` | carries its own `icmp_seq` |

### The three rules for reading this file

**1. `no answer yet` is neither an answer nor a loss.** ping writes it as soon
as a reply takes longer than one send interval, and then writes the reply if it
arrives. A sequence number counts as lost only if **no** reply line for it
exists anywhere in the window.

Both readings have caused errors, in opposite directions. Counting these lines
as loss invents outages. Counting them as replies (the line does contain
`icmp_seq=`, after all) produces a smooth answer density and a healthy-looking
network; the real loss in that case was 9 to 21 percent.

**2. `icmp_seq` wraps after 3 h 38 min** at five packets a second (16 bit,
65536 × 0.2 s). Analysing a window longer than that makes the same number occur
twice, and an answered sequence then excuses a later real loss. **Analyse along
the time axis, not along the sequence number.** `analyse_window()` reports
`max_gap_s` alongside the burst measure for exactly this reason.

**3. Never sum gaps across a restart marker.** Each `# ---- measurement starts`
line is a new ping process with a fresh sequence counter. The gap either side of
it is a process event, not a network event.

The counter-check for rule 3: a genuine outage leaves `no answer yet` lines
**without** a start marker between them. A restart leaves a marker and no such
lines. This rule was itself once over-applied, discarding real outages that
happened to sit near a restart, which is why the counter-check is written down
here rather than left to judgement.

---

## Packet-rate log

`<base>/pktrate/<node>-<YYYY-MM-DD>.log`

Written by `src/probes/pktrate.sh`. Read by `src/analyze/pktrate-scan.py`.

```
# lan-tomography packet rates (probe1, eth0), interval 5s
# Timestamp = Unix epoch, in brackets, at the END of the interval.
# Values are DELTAS within the interval, not rates.
# Fields: uni bcast mcast rx tx drop err missed
# Cross-check: uni + bcast + mcast == rx
[1786201899.326] 4211 106 88 4405 3902 0 0 0
```

| Field | Meaning |
|---|---|
| `[…]` | Unix epoch with milliseconds, **at the end of the interval** |
| `uni` | unicast frames received in the interval |
| `bcast` | broadcast frames received |
| `mcast` | multicast frames received |
| `rx` | total frames received |
| `tx` | frames transmitted |
| `drop` / `err` / `missed` | `rx_dropped`, `rx_errors`, `rx_missed_errors` |

Values are **deltas per interval**, not rates. Divide by `LT_PKTRATE_INTERVAL`
for frames per second. A negative delta, a counter reset on link-down, is
written as `0` rather than a phantom spike.

### The three ways this file gets misread

**The timestamp is in square brackets.** In awk, `[1786201899.326]` in
arithmetic context evaluates to **0**, silently. No error, no warning. A whole
measurement day once collapsed into a single hour that way, and the resulting
table looked perfectly plausible, because it had one row.

```awk
{ gsub(/[][]/, "", $1); ... }      # always, before any arithmetic
```

Counter-check: a full measurement day must produce **24 hourly buckets**. Fewer
means brackets.

**The timestamp is the END of the interval.** Treating it as the start shifts
every sample by one interval. In one analysis that shift turned a duplication
factor of 53.5 into 1.00, that is, "there is a loop" into "there is no loop".

**The columns are easy to swap.** In awk the fields are `$2 $3 $4` after
stripping the brackets; in a Python split they are `[1] [2] [3]`. Getting
broadcast and multicast the wrong way round produces confident, wrong numbers
with nothing visibly amiss. One line settles it, and it costs nothing to run
every time:

```
uni + bcast + mcast == rx
```

`check_alignment()` in `pktrate-scan.py` does this and reports loudly, because
if it fails, every number derived from the file is suspect.

---

## Symptom windows: `waves.csv`

`<base>/waves.csv`

**This is an INPUT, not an output.** It is the one file this toolkit does not
produce for you, because only you know what your symptom is.

A "wave" is a window in which the thing you are investigating happened:
sessions dropping, an application erroring, users calling. `correlate.py` holds
each window against the ping matrix and asks which network section can account
for it.

```csv
start_epoch,end_epoch,count,note
1786201800,1786201860,7,seven sessions dropped
1786205400,1786205460,3,
```

| Column | Required | Meaning |
|---|---|---|
| `start_epoch` | yes | Unix epoch, start of the window |
| `end_epoch` | yes | Unix epoch, end of the window |
| `count` | no | how many occurrences; used for ordering only |
| `note` | no | free text, carried into the report |

Rows that cannot be parsed are skipped rather than aborting the run.

### Producing it

Whatever records your symptom. Some sources that work:

- Any timestamped log. `src/analyze/waves-from-log.py` turns one into
  waves.csv given a regex and a timestamp format. Standard library only.
- A VPN or remote-access gateway's session log.
- Application error logs, grouped by minute.
- A support ticket queue exported by timestamp.
- Hand-written, from user reports. Three careful rows beat three hundred
  machine-generated ones.

**Grouping threshold, and what it hides.** Producers usually emit a row when
*N or more* events fall in one minute. That threshold is a filter: with N=5,
individual disconnects below five a minute never appear, so an intermittent
fault affecting one user at a time is invisible in this file while being
plainly visible to that user. Know your N, and analyse the raw source as well
when the symptom is sparse.

---

## Switch timeline

`<base>/switch-timeline.jsonl`

One JSON object per line, written by `src/switch/switch-probe.py`.

```json
{"ts":"2026-08-08T12:00:00+00:00","switch":"192.0.2.2","interval_s":60.0,
 "ports":{"5":{"descr":"uplink","oper_status":1,"speed":1000000000,
                "d":{"in_discards":0,"out_discards":142,"in_octets":998877},
                "dt":60.0}}}
```

| Key | Meaning |
|---|---|
| `ts` | ISO 8601 UTC, when the sample was taken |
| `interval_s` | seconds since the previous sample, `null` on the first |
| `ports` | keyed by interface index |
| `ports.*.d` | **deltas** since the previous sample |
| `ports.*.dt` | seconds the deltas cover |

A delta of `null` means the counter went backwards, from a wrap or a switch reboot.
That is deliberately **not** `0`: "unknown" and "nothing happened" are different
statements, and this whole toolkit turns on keeping them apart.

Ports with no link and no counter movement are omitted to keep the file half
the size. A port gaining link appears on its own.

**A quiet timeline is not an all-clear.** SNMP counters were blind for 12 of 18
flood minutes in one measured case: the agent stopped answering while the
device was busy, and the gaps were not random (p = 2.8e-14 against the flood
windows). The device stops reporting precisely when there is something to
report. Check `interval_s` for gaps before concluding anything from silence.

---

## Forwarding-database movements

`<base>/fdb-flapping.jsonl`

One JSON object per line, written by `src/switch/fdb-probe.py`, only when
something moved.

```json
{"ts":"2026-08-08T12:00:00+00:00","switch":"192.0.2.2","macs_total":112,
 "flaps":[{"mac":"aa:bb:cc:00:00:01","from":23,"to":25}]}
```

Only addresses present in **both** consecutive samples with a changed port are
recorded. New and vanished addresses are normal ageing, and logging them would
bury the finding.

**A movement names a path, not a culprit.** While a loop is running the switch
learns every sender's MAC on the return port, including the measuring machine's
own.

---

## TCP probe log

`<base>/tcp/<label>-<YYYY-MM-DD>.log`

Written by `src/probes/tcp-probe.py`.

```
# lan-tomography TCP probe
# Target: ts01-rdp (192.0.2.20:3389), role: symptom, interval: 15.0s
# Start: 2026-08-08T12:00:00+00:00, timeout: 1.5s
# Timestamps are Unix epoch (timezone-free)
[1786201899.940000] connect 192.0.2.20:3389 ok time=1.23 ms
[1786201914.940000] connect 192.0.2.20:3389 failed err=timeout
[1786201929.940000] connect 192.0.2.20:9 failed err=refused
```

`err=refused` is a fast, clean answer from a live host: a stopped service, not
a network fault. Folding it in with `timeout` turns a stopped service into a
phantom outage.

---

## Layer-2 capture

`<base>/l2/l2-events-<YYYY-MM-DD>.log`, plain text, STP only
`<base>/l2/l2.pcap*`, ring buffer, STP and ARP

The text log is `tcpdump -n -l -tttt` output. Note that `-tttt` writes **local
time without an offset**: the line claims a timezone the reader cannot
determine. `LT_TZ` fixes it; set it the same everywhere and record which value
you used.

`correlate.py` reads this file: it counts the STP topology-change BPDUs inside
each symptom window and prints them under the verdict. A path rebuilt at the
second sessions tore down is a different statement from a path that merely lost
packets, and it is the reason the capture exists.

It looks for `l2/` **beside the data directory**, the same coupling the target
matrix follows, so a probe's captures are read with that probe's ping logs and
not with another machine's. `--l2-dir` overrides.

A window with no lines at all reports `NO DATA`, never "no topology changes":
the daily file is opened at midnight, so its existence says nothing about
whether the capture was still running by the time your window came round. See
[pitfall E7](../explanation/pitfalls.md#e7-a-capture-file-that-exists-is-not-a-capture-that-ran).

The capture filters are narrow on purpose: a wide filter on a busy LAN fills a
disk and collects user payloads. The cost is that **broadcast floods, multicast
storms and unknown-unicast flooding are invisible here.** Use the packet-rate
log for those. In the case this toolkit came from, the decisive event was a
broadcast flood and nobody was looking at it, because the only running capture
filtered on `stp`.

---

## Frame captures

```
<base>/capture/broadcast/broadcast.pcap0..N   ring buffer, bounded
<base>/capture/loop-detect/loop-detect-<YYYY-MM-DD>.pcap
<base>/capture/roaming/roaming-<YYYY-MM-DD>.pcap
<base>/capture/keep/<trigger>-<YYYYMMDD-HHMM>-<ring file>
<base>/capture/kept-events.log
```

Written by `src/probes/frame-capture.sh`, one profile per instance. The `keep/`
directory and the event log are written by `src/events/flood-capture.sh`.

**The date in a daily filename is the date of the rotation, not of the
contents.** `tcpdump -G 86400` counts from service start, so a file named for
one day routinely holds frames from the next. Select these files by
modification time and read two of them across a boundary, or a rotation opens a
gap in your analysis exactly where it looks like an absence of frames.

`kept-events.log` records every trigger, including those where nothing was
copied because an identical capture had already been kept:

```
2026-08-08T22:47:14+02:00 rate event at 22:45:14, 2 file(s) kept
2026-08-08T22:47:55+02:00 precursor at 22:47:15, capture already kept
```

The distinction matters when reading it back: "already kept" is a statement
about the copy, never about the event. The event happened either way.

---

## Operational log

`<base>/lan-tomography.log`

```
[2026-08-08 12:00:00] [INFO] [ping] starting continuous measurement of 192.0.2.1 (gw)
```

Local time, human-readable, **not** measurement data. Nothing in this toolkit
parses it, and no analysis depends on it. Delete it freely; deleting anything
else above loses evidence.
