# Pitfalls

Measurements that look plausible and are wrong.

This is the core of this repository. The tools are ordinary — ping loops, an
SNMP poller, some parsing. What is hard to come by is this list, because every
entry cost somebody hours, and several of them produced a confident statement
that was later withdrawn.

They are grouped by what they attack. Where a pitfall lives in code, the test
that pins it is named: `pytest tests/ -k <name>`.

**The pattern behind most of them:** the failure mode is not an error message.
It is a plausible number. A tool that crashes gets fixed in ten minutes; a tool
that returns 1.00 instead of 53.5 gets believed and quoted in an email.

---

## A. The measurement point itself

### A1. Your probe is part of the network you are measuring

A machine on a 100-Mbit port, behind a desk phone, with a busy uplink, will
report loss that belongs to its own connection. Before trusting any probe,
measure its floor: latency to something one hop away, under no load.

One probe in the case this came from showed a latency ceiling of 36–40 ms
against a median of 0.7 ms, purely from its own port. Everything it said about
"the network being slow" was about itself.

### A2. An outage on *all* targets is the probe

If every target fails at the same second, the common element is not the
network — it is the machine doing the measuring, or its uplink. Check the
probe's own load, its interface counters and its uplink before anything else.

This is also why a single probe cannot localise anything. Two probes that
disagree are the entire method.

### A3. Only one probe sits on the path users actually take

Servers measuring servers tells you about the server room. If the complaint
comes from a desk, something has to measure from a desk. It is the most
awkward probe to place and the one most often skipped.

### A4. Saturating your own link looks exactly like a network fault

`ping` reporting "Destination Host Unreachable" during a large file copy is
your own interface, not the network. Counter-check in three steps: is the local
interface saturated (`pktrate`), do other probes see the same target fail, does
the target fail from a probe that is idle?

And: do not read the culprit off the CPU list. In one case the process at the
top of `top` was not the one filling the link.

---

## B. Reading the ping logs

### B1. `no answer yet` is neither an answer nor a loss

`ping -O` writes it as soon as a reply is late, then writes the reply if it
arrives. A sequence number counts as lost only if **no** reply for it exists
anywhere in the window.

This one cost hours in **both** directions:

- counted as loss → invented outages that never happened
- counted as replies (the line does contain `icmp_seq=`) → a smooth answer
  density and the conclusion "reachable throughout". Actual loss: 9 to 21 %.

*Tests: `no_answer_yet_with_later_reply`, `no_answer_yet_does_not_count_as_a_reply`*

### B2. `icmp_seq` wraps after 3 h 38 min

16 bit at five packets a second. Analyse a window longer than that and the same
number occurs twice; an answered sequence then excuses a later real loss.
Analyse along the **time axis**, not the sequence number.

*Test: `seq_wrap_masks_loss_in_an_oversized_window`*

### B3. Never sum gaps across a restart marker

Each `# ---- measurement starts` line is a fresh ping process with a fresh
counter. The gap either side is a process event.

**This rule was itself over-applied**, discarding real outages that happened to
sit near a restart — a second-order pitfall, and the reason the counter-check
is written down: a genuine outage leaves `no answer yet` lines *without* a
start marker between them.

### B4. A target that never answers is off, not failed

Workstations are switched off after hours. Without the distinction, every
overnight analysis reports a total outage and skews the verdict.

The other half of the same rule matters just as much: this excuse applies
**only** to roles that are allowed to be off. A server silent for a whole
window *is* a total outage — excusing it lets the worst possible failure
produce a clean verdict.

*Tests: `permanently_silent_target_counts_as_offline`, `offline_still_counts_against_a_server_role`*

### B5. Scattered single losses are not an outage; contiguous ones are

One to three packets in a thousand go missing on any LAN and no session ever
notices. Measure the **longest uninterrupted run**, not the total.

*Tests: `scattered_single_losses_are_not_an_outage`, `contiguous_burst_is_an_outage`*

### B6. When ping goes silent entirely there is nothing to count

The worst outages leave no lines at all — no replies, no `no answer yet`,
nothing. Sequence analysis finds nothing because there is nothing. Only the
**distance between two consecutive lines** finds it.

One real case: last reply 12:55:57, next 13:18:11, and every packet-based
metric reported a clean window.

*Test: `gap_with_no_lines_at_all_is_found_on_the_time_axis`*

### B7. Unreachable lines carry a sequence number — parse it

`From 192.0.2.1 icmp_seq=7 Destination Host Unreachable`. Drop the number and
a whole unreachable phase collapses into one pseudo-loss, landing below the
outage threshold. A long, obvious outage gets reported as a blip.

*Test: `unreachable_lines_carry_their_sequence_number`*

### B8. A missing file is not zero loss

Missing daily files have harmless causes. Scored as "0 % loss" they prop up a
clean verdict for a window nobody measured. **"No loss" is a statement. "No
data" is not one.** Every analysis function here returns `None`, never `0`.

*Tests: `missing_log_file_yields_none_not_zero_loss`, `window_without_lines_yields_none`*

### B9. Compressed archive days are skipped silently

`glob("*.log")` does not match `*.log.zst`. The analysis runs on less data,
with no error and no exit code — it just quietly considers fewer days.

And the reverse: a probe mirrored with `rsync` without `--delete` returns an
uncompressed copy of a day that was already archived, so the same day sits
there twice and gets counted twice.

*Tests: `log_files_finds_compressed_days`, `log_files_does_not_count_the_same_day_twice`*

---

## C. Reading the packet-rate logs

### C1. The timestamp is in square brackets, and awk makes it 0

`[1786201899.326]` in awk arithmetic evaluates to **0**. Silently. No error.

A whole measurement day once collapsed into a single hour this way, and the
table looked entirely plausible — it had one row, and one row does not look
wrong.

```awk
{ gsub(/[][]/, "", $1); ... }
```

Counter-check: a full day must produce **24 hourly buckets**.

*Tests: `bracketed_timestamp_is_parsed_not_zeroed`, `full_day_covers_24_hourly_buckets`*

### C2. The timestamp is the END of the interval

Treating it as the start shifts every sample by one interval. That shift once
turned a duplication factor of **53.5 into 1.00** — "there is a loop" into
"there is no loop".

*Test: `timestamp_is_the_end_of_the_interval`*

### C3. The columns are easy to swap, and the error is invisible

`uni bcast mcast rx tx drop err missed`. In awk they are `$2 $3 $4`; in a
Python split `[1] [2] [3]`. Swapping broadcast and multicast produces confident
wrong numbers with nothing visibly amiss — in one case two of them went into an
email to the service provider.

One line settles it:

```
uni + bcast + mcast == rx
```

*Tests: `alignment_check_catches_a_swapped_column`, `fields_are_read_in_the_documented_order`*

### C4. A flood definition counting only broadcast misses multicast storms

Measured in one window: 30,672 multicast against 5,267 broadcast. A
broadcast-only threshold called that quiet.

*Test: `multicast_alone_triggers_an_event`*

### C5. Repeated alerts with an identical value are ONE event

A watcher reporting the sliding maximum of a window emits the same peak on
every pass. Counting those as separate events turned 6 into 18, and the
inflated number reached a report.

*Test: `consecutive_samples_are_one_event`*

### C6. Counter names are driver-specific

Realtek reports `unicast:` / `broadcast:` / `multicast:`; Intel reports
`rx_broadcast:` / `rx_multicast:` and **no unicast at all**. A parser knowing
one scheme writes zeros on the other machine — silently. A measurement outage
that looks exactly like "no flood", which is the worst failure this repository
has: not an error, but a confident negative.

### C7. A quiet minute is not a quiet minute

Below the aggregation threshold lies a whole class of events. A one-minute
bucket showing "normal" can contain a two-second storm. When the symptom is
sub-second, aggregate at the second.

---

## D. Switches and SNMP

### D1. A switch's management IP measures its CPU, not its forwarding path

A switch answers ICMP from its management CPU. Under load that CPU delays or
discards replies **while forwarding traffic perfectly**.

Measured: switch targets produced apparent outages up to **24 seconds** while
every target *behind* the same switch stayed clean in the same window, and
other probes saw the same switch answering normally. Three probes at 5
packets/s each is 15 packets/s arriving at a CPU not built for it — the
measurement was measuring itself.

The tell is scatter: 1.6–5.5 ms to the switch, 0.6 ms to ordinary hosts in the
same segment.

Keep these targets — they raise the rank of the matrix and they are how you see
the switch at all — but never let one carry a verdict.

*Test: `outage_in_an_unjudged_role_is_not_reported_as_clean`*

### D2. SNMP counters go blind exactly when it matters

In one measured case, counters were missing for **12 of 18 flood minutes**, and
the gaps were not random (p = 2.8e-14 against the flood windows). The agent
stops answering while the device is busy.

So an empty counter result means either nothing happened **or** the device
could not tell you. Check for gaps in `interval_s` before concluding anything.
A quiet timeline is not an all-clear.

### D3. Read counters against a baseline, not against zero

Counters are cumulative and often huge. `ifOutDiscards` of four million says
nothing without knowing whether it accumulated over twenty days or in the last
hour. Only the **difference between two samples** is a finding.

### D4. Zero errors and huge discards is a finding, not a contradiction

`ifInErrors = 0` across every interface for three weeks means the cabling is
fine. `ifOutDiscards` in the millions on one port means send-queue overruns —
loss without a link failure, without an event-log entry, and without a ping
matrix necessarily seeing anything. They are different mechanisms and the pair
is informative.

### D5. A MAC at the return port accuses nobody

While a loop is running, the switch learns **every** sender's MAC on the return
port — including the measuring machine's own. A MAC appearing there is a
passenger.

### D6. Never infer identity from the OUI

The vendor is a hint. Establish identity from LLDP chassis IDs, TLS
certificates or HTTP banners. In one case a device's own vendor prefix pointed
firmly at the wrong conclusion.

### D7. An address is not a device

One address changed hands mid-campaign: the access point at `.11` was powered
off, another device took the lease, and anyone checking "the AP" by address was
measuring a stranger and reading its silence as the AP's. Track devices by MAC
or by a proven identity, not by address.

---

## E. Captures

### E1. `tcpdump -w` without `-U` can sit at 0 bytes for days

Output is buffered. The file looks exactly like "no frames matched the filter".
Use `-U`, or a ring buffer (`-C`/`-W`) whose rotation forces flushes.

### E2. A narrow filter makes whole phenomena invisible

`stp or arp` sees no broadcast flood, no multicast storm, no unknown-unicast
flooding. In the case this came from, the decisive event was a broadcast flood
and **nobody was looking at it**, because the only running capture filtered on
`stp`. Know what your filter excludes, and cover it with a counter instead.

### E3. `tcpdump -tttt` writes local time without an offset

The line claims a timezone the reader cannot determine. Fix `LT_TZ`, set it the
same on every probe, and write down which value you used.

### E4. "Frame captured" is not "frame sent"

A capture on the sending host records what the stack handed to the driver. A
frame can be captured and still never reach the wire.

### E5. The ring rotates mid-event, and the run-up is in the older file

A ring capture writes a fixed number of files and overwrites the oldest. The
rotation is not aware of your incident: in the case this came from it fell at
20:02:36, inside an event that began at 20:02:32. Keeping only the current file
preserved the flood and lost the four seconds before it — which is where the
precursor was.

Always keep the current file **and the one before it**. `flood-capture.sh` does
this; if you copy a ring out by hand, do the same.

The same reasoning applies to the far end: a capture that stops does not mean
the event stopped.

### E6. A frame-length test only works on your own frames

The 42-vs-60-byte padding test distinguishes frames the local stack produced.
Applied to somebody else's traffic it reports "everything is a copy" and is
worthless.

### E7. A capture file that exists is not a capture that ran

The daily `l2-events-<date>.log` is opened at midnight, so it exists for the
whole day from its first second. A capture that dies at 09:00 leaves behind a
file that is indistinguishable, by its presence, from a quiet day — and quiet
is what "no topology change in the window" means to a reader.

This is [B8](#b8-a-missing-file-is-not-zero-loss) one layer down, and it is
worse here, because at layer 3 a missing file at least draws attention. Here
the file is present and the honest answer still has to be "no data".

Coverage has to come from the **lines**, not the file: with an `stp` filter a
live capture writes a BPDU every two seconds, so a window containing no lines
at all is a window with no capture. `correlate.py` distinguishes all three
states — `None` for uncovered, an empty result for "the capture ran and the
topology held", and the matching lines otherwise.

One more, when the lines are there: the topology-change flag stays set for the
length of the TC-while timer, so a single change produces a **run** of flagged
hellos. Counting lines counts BPDUs, not events — which is why the lines
themselves are printed underneath the count.

*Tests: `window_without_l2_lines_yields_none_not_zero_changes`,
`a_running_capture_without_changes_is_a_finding`,
`topology_change_in_the_window_is_counted`, `l2_timestamps_follow_lt_tz`*

---

## F. Verdicts and reasoning

### F1. Fix falsification criteria BEFORE the measurement window

Write down what result would kill each hypothesis, and do it in advance. Three
corrections in a single day is what made this non-negotiable in the case this
came from: without a pre-registered criterion, the criterion drifts towards
whichever hypothesis you have grown fond of.

### F2. A negative result from a blind test is not a negative result

The sharpest lesson here. A circuit test returned a factor of 3.8–3.95 in event
windows **and** in quiet windows. It looked like a clean negative. Validated
against a storm that was independently proven, it returned 3.86 where the
working test returned 44.1.

The test was **blind, not negative**. It could not have detected the thing
whose absence it appeared to confirm.

Before believing a negative result, validate the method against a case where
you already know the answer. If you cannot, say "this method found nothing",
never "there was nothing".

### F3. A verdict must never contradict its own table

The label is what travels. The table stays in the terminal; the label goes into
a mail subject, a ticket title, somebody else's spreadsheet. If it says "clean"
while the table underneath shows a five-minute outage, the table has lost.

There are two ways to arrive at that contradiction, and the second is much
easier to miss than the first:

1. **Nothing with a judging role failed, but something else did.** The honest
   output is "outage outside the judgement matrix" — `switch-ref`, `uplink-ref`
   and `wan-ref` deliberately carry no verdict, which is not the same as
   carrying a clean one.
2. **The symptom host stayed reachable while a judging role failed hard.** The
   symptom is genuinely not explained by the network — but the network was not
   clean either. This one shipped as `NETWORK CLEAN` until 0.2.0 and was
   reproducible with this repository's own sample data: a 299-second gateway
   outage, printed in the table, under the word CLEAN. It now reads "outage
   outside the symptom path".

The general form: **the narrowest true statement, never the most convenient
one.** "The symptom path held" and "the network was clean" are different
statements, and only one of them was measured.

*Tests: `outage_in_an_unjudged_role_is_not_reported_as_clean`,
`outage_outside_the_symptom_path_is_not_reported_as_clean`,
`nothing_failed_at_all_is_still_network_clean`*

### F4. Simultaneous interventions destroy attribution

Four changes were made at once in the case this came from — a powered-off
access point, firmware and meshing changes, a replaced cable and switch, and an
ARP source that dried up. The symptom stopped. **Nothing was thereby proven.**

If you must change several things at once, say so in the report, and keep the
counter-test — restoring one variable — on the list.

### F5. Absence of the symptom is not a finding unless the chain was alive

A dead probe produces silence. So does a fixed network. Confirm the measurement
chain was alive for every quiet period you intend to cite, from something
independent of the probes — see `src/events/liveness-check.sh`.

### F6. Correlation across sources needs one clock

Four sources logging in UTC while the analysis assumed local time cost two
wrong conclusions in one day — in both directions. Once it produced "the source
is silent" about a source that had been running the whole time and turned out
to be the most informative one available.

### F7. An offset can lie

A device sending syslog without a timezone gets stamped with the *receiver's*
zone. The line then claims an hour the device never meant. Four measurements
gave 7206, 7217, 7218 and 7218 seconds of apparent drift — exactly 7200. A
clock that is genuinely wrong does not land on precisely two hours.

### F8. Keep withdrawn statements, with a note

Deleting a wrong intermediate state removes the evidence that it was wrong, and
somebody re-derives it a week later. Every retraction in the case study is
still there, marked.

### F9. A target missing from the matrix is missing from the table, not from the network

The measurements and the matrix that says what they mean are collected in
different places: the probe writes the logs, and the matrix belongs to the
probe — but the analysis runs on the machine the data was pulled to, which has
a matrix of its own.

Analyse one probe's data against another probe's matrix and the output is
wrong in **both** directions at once, silently. Labels only the remote matrix
knows never appear at all — no row, no `NO DATA`, no exit code. Labels only the
local one knows appear as measurement gaps for targets that were never
measured.

Measured: seven table rows became five, and the two that vanished were the two
`uplink-ref` rows — the targets the second measurement point had been built
for. The table looked complete, because a table with five rows looks exactly
like a table with five rows.

The fix is not care, it is coupling: the matrix travels with the data, and an
analysis that cannot establish which matrix belongs to a directory **stops**
rather than guess. A stop in a chain of evidence costs a minute; a silent
omission costs the conclusion.

*Tests: `foreign_data_directory_without_a_matrix_aborts`,
`targets_beside_the_data_win_over_the_configured_default`*

---

## G. Windows event logs

### G1. An EVTX file is a ring buffer, and chunk 0 is not the oldest

The obvious way to ask "how far back does this log reach" is to read the first
record and the last one and subtract. On a log that has already wrapped, that
answer is wrong by up to the entire retention period, and it is wrong in the
reassuring direction: it reports a span that covers your incident when the
records from that day were overwritten days ago.

After the wrap, the newest record sits somewhere in the middle of the file and
the oldest sits immediately after it. The record numbers rise monotonically and
break exactly once, at that seam.
[`evtx-peek.py --coverage`](../../src/contrib/evtx-peek.py) finds the seam by
bisection and reports the real span.

Before concluding that a log does not cover a window, establish that the log
still *reaches* that window.

*Tests: `wrapped_file_finds_the_seam_not_chunk_zero`, `unwritten_tail_is_not_mistaken_for_a_wrap`*

### G2. Transferring the log changes the network you are measuring

These files run to hundreds of megabytes — 314 MB in the case this came from.
Fetching one across the link under investigation is a sustained transfer over
exactly the path whose behaviour is in question, and it competes with the
traffic you are trying to characterise. See [A4](#a4-saturating-your-own-link-looks-exactly-like-a-network-fault):
the resulting loss is indistinguishable from the fault.

Read ranges instead. `smbclient get` cannot; SMB2 READ with an offset can, and
the EVTX layout — a 4096-byte header, then independent 64 KB chunks — is built
for it. A dozen chunks answer "is this log worth fetching" for 768 KB.

### G3. Event-log timestamps are UTC; your capture timestamps are not

EVTX stores UTC. `tcpdump -tttt` writes local time with no offset
([E3](#e3-tcpdump--tttt-writes-local-time-without-an-offset)). Putting the two
side by side without converting produces a correlation that looks convincing
and is off by the local UTC offset — an hour or two, which is wider than most
of the events worth correlating.

Convert at read time, to the same `LT_TZ` the probes use, and put the timezone
in the output header so the reader can see which one applied.

*Tests: `display_tz_follows_lt_tz`, `unknown_timezone_falls_back_to_utc`*

---

## Contributing a pitfall

If a tool here led you to a confident, incorrect conclusion, that is the most
valuable thing you can report — more than a crash. Include what the data looked
like, what you concluded, what turned out to be true, and the cross-check that
would have caught it. See [CONTRIBUTING.md](../../CONTRIBUTING.md).
