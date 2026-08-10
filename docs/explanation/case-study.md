# Case study: an intermittent layer-2 loop

Nine days of measurement on a network somebody else administered, ending without
a resolution.

Sessions on a Windows terminal server were dropping in waves — a dozen users at
the same second, several times a day, for weeks before anyone measured. The
provider responsible for the network had already replaced the central switch
without a diagnosis, and it had not helped. Nobody on our side had access to a single switch when the
measurement started; the access that made the switch chapters possible was
granted on Day 5, once the probe data had made the case for it.

The measurement found a broadcast frame going round in a circle somewhere on one
floor, intermittently, multiplying everything broadcast by a factor between 17 and
56 while it did. That closed path is what this chapter calls **the circuit**
throughout: not an electrical one, but a route by which a broadcast frame leaves a
switch and arrives back at it. It never found what closed it. The waves stopped after
the investigators' own intervention on Day 6; four further changes on Day 9 then
landed at once, and the counter-test that would attribute the quiet to any one of
them is still outstanding.

**This chapter is the evidence, not the lesson**; the generalisations live in
[Pitfalls](pitfalls.md) and [Proving a loop](proving-a-loop.md), and this links to
them rather than retelling them. It contains the four things such a write-up
usually leaves out: the **falsification criteria** fixed before the measurement
windows, the **correction table** of every statement treated as established and
later fallen, the **withdrawn detection method** that looked like a clean negative
and was blind, and an **ending that is not a resolution**.

It is long, so: the proof rests on
[finding 1](#1-the-probe-receives-its-own-broadcasts-back), where the probe
receives its own broadcasts back, and
[finding 3](#3-the-spanning-tree-names-the-return-path), where the spanning tree
names the return path from the protocol itself. The
[correction table](#the-correction-table) and
[the withdrawn detection method](#the-withdrawn-detection-method) are where the
method is on trial rather than the network. Everything else supports those four.

## How this has been anonymised

The network belongs to the investigators' own organisation and is administered by
an external service provider. Everything identifying is replaced, everything
measured is unchanged, and the same entity keeps the same replacement throughout —
otherwise the evidence chain falls apart. **Addresses** are reassigned by role in
`192.0.2.0/24` (RFC 5737, as everywhere else here): gateway `.1`, floor switch
`.2`, core switches `.3`/`.4`, access
points `.11`–`.14`, terminal server `.20`, hypervisor `.21`, probes `.31`–`.33`.
**Dates are relative** — Day 1 is the day the measurement started and earlier days
count backwards, with no Day 0; the campaign ran from the evening of Day 1 to Day
10, which is the nine days of the subtitle — while **clock times are unchanged**,
because half the argument rests on events agreeing to the second. **Switch port numbers are real**: they
identify nothing, and the three-port triangle is the finding. Firmware versions,
credentials, part numbers and the state of anything patchable are out.

**No MAC addresses are published.** Vendor class is kept where it carries the
argument — it separates the access points from the unmanaged distributor, and that
distributor was identified by its vendor's loop-detection ethertype rather than by
a guess from an address
([D6](pitfalls.md#d6-never-infer-identity-from-the-oui)). Which particular device
it was identifies nothing.

## The network

```
  sw-floor (.2) ─┬─ port 10 ── 24-port switch ── ap3 (.13), ap4 (.14)
                 ├─ port 13 ── gateway (.1)
                 ├─ port 23 ── unmanaged Realtek distributor ── ap2 (.12),
                 │                                   phone, printer, NAS
                 ├─ port 25 ── small switch ── ap1 (.11)
                 ├─ port 37 ── probe1 (.31)
                 ├─ port 41 ── second unmanaged distributor
                 └─ port 40 ── uplink ── sw-core-b (.4) ── hv01 (.21) ── ts01 (.20)
                                              │
                                         sw-core-a (.3, STP root) ── probe2, probe3
```

| | |
|---|---|
| `ts01` (`.20`) | Windows terminal server, the symptom host. A guest on `hv01`. |
| `hv01` (`.21`) | Hypervisor: one 1-GbE uplink, no teaming, four production guests. |
| `sw-floor` (`.2`) | 48-port managed access switch on the affected floor, **not** in the vendor's controller — no activity log, no health score, no history, SNMP the only source. Speaks MSTP in a region of its own while the core speaks RSTP. |
| `sw-core-a/b` (`.3`, `.4`) | Two switches in the server room, in the vendor's controller. `sw-core-a` is the STP root. |
| `ap1` … `ap4` (`.11`–`.14`) | Four access points of one vendor, orphaned: no controller had run for months and all four kept ARPing for a stored controller address that answered nothing. `ap1` and `ap2` were identified by LLDP as one model, which has two Ethernet ports; the two behind port 10 were never pinned to a model. |
| `probe1` (`.31`) | On `sw-floor` port 37, proven by LLDP — one source, no intermediate device. **The only probe on the path the affected clients take.** |
| `probe2`, `probe3` | Server room and office, both on `sw-core-a`. `probe3` sits on a 100-Mbit port behind a desk phone, which took a while to notice ([A1](pitfalls.md#a1-your-probe-is-part-of-the-network-you-are-measuring)). |

The client path crosses exactly one device boundary: *office client → `sw-floor`
→ uplink → `sw-core-b` → `hv01` → `ts01`*, never touching `sw-core-a`. Probes 2
and 3 both hang on `sw-core-a` and reach `ts01` by a detour no real client takes,
which is why only `probe1` could settle anything in the end — not known when it
was built, since it went in as a gap filler.

## Timeline

| Day | |
|---|---|
| **−18** | `sw-floor` reboots at 21:55. The reason is never reconstructible: persistent logging is off, and its event buffer reaches back about 69 minutes because one port flaps every ~160 seconds. |
| **−11** | Earliest evidence of the fault: a TCP-retransmit spike on two independent hosts at nearly identical values. `sw-floor` port 23 has been up without interruption since about 13:40 this day. |
| **−8** | First user report, 14:34, a Sunday. SMB timeouts and interface events in the terminal server's log begin 19 minutes earlier. |
| **−7 … −1** | Waves on most working days, up to 13 sessions in one minute. Sunday **−1** is completely quiet. |
| **1** | The provider replaces the central switch without a diagnosis — one 48-port device becomes two, of 16 and 24 ports. The campaign is deployed the same evening: ping matrix, passive STP/ARP capture, and a TCP-only control session to `ts01` as a transport control. |
| **3** | Day 2 had been quieter than the comparison days and read like a success. It was not one: 55 drops and five waves by noon. Management reports that a locally hosted line-of-business application and video conferences failed at the same times, including from home. **The terminal server is a casualty, not a cause.** Three conclusions are corrected within hours; the falsification criteria are written down that evening. `probe1`, the hypervisor and two WAN targets are added. |
| **4** | **The loop is proven directly**: `probe1` receives its own broadcasts back — 9 sent, 673 copies. At the minute of every one of 14 waves, loss on the client path is 8.8–20.9 % against 1.0–1.2 % at rest. Also this day: somebody unrelated switches the router off at the wall from 13:22 to 13:44, and the circuit runs on without it. |
| **5** | The provider grants access to `sw-floor`. Two independent measurements put the entry point on **port 23**: 42.5 % of its inbound traffic is broadcast (22.9 M packets, almost as much as the entire uplink) against 13 % on the two structurally identical neighbour ports, and 370 of the day's 655 address migrations run through it. Behind it sits an unmanaged switch with no IP and no LLDP. |
| **6** | The IP-ID duplication test is built and validated. A precursor is found: a wired 802.11r frame from `ap1` before every surge, and none at all in 242 minutes of quiet. The prediction that follows comes true two hours later, at 20:02, on an event that was not known when it was made — one precursor frame after 4 h 9 min without any, four seconds later the surge, gateway gone 285.9 s, duplication factor 44.1. **20:40: first intervention**, ports 23 and 25 isolated against each other. |
| **7** | 24 hours quiet. And the day's real finding: the loop detector built the day before is **withdrawn** as unfit, and the probability calculation behind the isolation test is withdrawn with it. |
| **8** | A full working day under isolation. Flood minutes fall from 21–33 to **2**, and it is the first **working** day of the campaign without a single wave — Days 6 and 7 also had none, but a Saturday and a Sunday have nobody logged in and prove nothing. But the circuit still forms, twice, with the highest factor of the campaign. At 11:16 the spanning tree names the return path from the protocol itself. And circuit and gateway outage occur **separately** for the first time, breaking a run of 16 out of 16. |
| **9** | The provider is on site 09:00–12:00 — a window agreed in advance to carry no verdict. A 494.4-second gateway outage at 09:35:58, far outside the 287–330 s band the others cluster in, and 16 session drops in two waves at 09:38 and 09:39 fall inside it; they are set aside by that agreement, not by the data. The provider names a defective PoE injector in front of `ap1`, removes it, replaces a cable and a switch, updates the other three access points and disables meshing. `ap1` is dead from 10:41:42. **12:09: our own isolation is lifted**, to stop being a confounder. |
| **10** | A fourth effect surfaces: the constant ARP flood from the orphaned access points — 118,874 requests per 11.5 hours — has stopped. The broadcast base load the circuit fed on is gone. Quiet since. |

## The load-bearing findings

### 1. The probe receives its own broadcasts back

The proof is direct and needs no statistics. Between 13:39:50 and 13:40:30 on Day
4, `probe1` sent **9** ARP requests and received **673 copies of them**. A device
cannot send its own broadcast back to itself; if it arrives, the network carried it
round. Four alternative explanations were put to that deliberately, and all fail:

| Objection | Refutation |
|---|---|
| The DHCP server simply sends repeatedly | 86,769 packets of one transaction spread over only **344 IP IDs**, the most common appearing 534 times. The copies are byte-identical including checksums, and the TTL is unchanged. |
| Capture artefact, the same packet recorded twice | The returning frames carry Ethernet padding the sender does not yet have locally — 42 bytes out, 60 bytes back. |
| Ring-buffer overlap between capture files | The four files cover disjoint time windows. |
| A loop without an STP reaction is impossible | The BPDUs arrive **unmultiplied**, on their 2-second cadence, with no topology change. The circuit runs through something that does not take part in spanning tree, or does not pass BPDUs on. |

It is not confined to DHCP: 519,579 ARP requests in the storm window resolve to
only **809 distinct queries**. Whatever is broadcast gets multiplied. And the
connection to the symptom is measured, not inferred — at the minute of each of
that day's 14 waves, `probe1` lost **8.8 % to 20.9 %** of packets to `ts01`
against 1.0–1.2 % in samples at rest, and the loss hit every target in the same
moment (37.4 %, 38.8 %, 17.9 %, 13.1 % on four targets in one three-minute
window), which is the recorded signature of a fault in the shared fabric.

Two properties mattered for everything that followed. **The circuit is not
permanently closed**: a sample at 22:00 the same day found 11 requests sent and
**zero** copies, which is why so much of this toolkit is about capturing *during* an
event. And **the router is not part of it**: the proof window at 13:39:50 falls
inside **13:22–13:44**, in which the router was physically switched off. The
circuit was multiplying frames while the router was dead. (That day had two
router outages of almost identical length — see the correction table — so this
one is named by its window rather than by its duration.)

### 2. The duplication factor by IP ID

The frame-length test above is sharp but only works on frames the probe itself
sent, and `probe1` at rest sends an ARP only every few minutes. For the two
1.3-second surges on Day 6 it reported **zero copies** — not because there were
none, but because there were no originals in the window: a blind test, not a
negative one ([E6](pitfalls.md#e6-a-frame-length-test-only-works-on-your-own-frames)).

The replacement needs no packets of one's own. Every IP packet carries a 16-bit
identification field, and within a short window a sender's packets have distinct
IDs, so the **duplication factor is IP broadcast packets divided by distinct IP
IDs** — always measured with a quiet window from the **same capture file**, so the
control is independent of the capture technique:

| Event | Window | Packets | Distinct IDs | Factor (`id=0` excluded) |
|---|---|---|---|---|
| Day 6, 20:02 surge | 20:02:33–20:03:30 | 24,319 | 657 | **37.02** |
| Day 6, quiet before the intervention | 20:20–20:40 | 6,430 | 6,430 | **1.00** |
| Day 6, quiet after the intervention | 20:42–21:01 | 6,071 | 6,071 | **1.00** |
| Day 6, sub-threshold surge 1 | 13:09:50–13:10:05 | 5,066 | 250 | 20.3 |
| Day 6, sub-threshold surge 2 | 13:11:20–13:11:35 | 5,407 | 315 | 17.2 |
| Day 8, 03:15 surge | 03:15:39–03:15:41 | 12,716 | 222 | **56.11** |
| Day 8, run-up to it | 03:10–03:15:30 | 1,858 | 1,745 | 1.01 |
| Day 8, quiet | 02:00–02:20 | 6,723 | 6,349 | **1.00** |
| Day 8, 14:41 storm | 14:41:41–14:41:56 | 29,049 | 693 | **41.92** |

The packet column is the raw count and the factor excludes `id=0`, so the two do
not divide out exactly on every row.

A factor of exactly 1.00 in quiet is the control: without a circuit, every frame
appears once. **This was the only detector in the campaign whose readings were
checked against circuits already proven by other means** — the returning-own-frames
test of finding 1 — and it reproduces them. That grounding is why its negatives can
be believed and the withdrawn method's cannot. The 44.1 quoted later for the 20:02
storm is this same method on a shorter and differently-filtered window, not a
second opinion.

A second check separates a circuit from a sender stuck retransmitting: in a
circuit, **consecutive** IDs come back with nearly equal copy counts, because
every packet goes round the same number of times. In the 14:41 storm, IDs 53153
to 53161 returned 143, 142, 142, 141, 141, 141, 141, 140 and 139 times. The
loudest sender in that capture was the domain controller with 20,286 packets — at
~140 copies per original, about ten genuine broadcasts a second, ordinary for a
domain controller. In a circuit the loudest sender is the chattiest machine on
the network, not the guilty one
([D5](pitfalls.md#d5-a-mac-at-the-return-port-accuses-nobody)).

One operational note that cost real time: the packet-rate timestamp marks the
**end** of its interval, and reading it as the start once shifted a window by five
seconds and turned a factor of **53.5 into 1.00** — which reads as "there is no
loop" ([C2](pitfalls.md#c2-the-timestamp-is-the-end-of-the-interval)).

### 3. The spanning tree names the return path

The best single piece of evidence in nine days needed neither a storm nor a
capture: three lines of `show spanning-tree` on `sw-floor`, read at 11:16 on Day 8.

```
0/23      Forwarding        Designated
0/25      Discarding        Backup
0/40      Forwarding        Root
```

A **backup port** exists, by definition, exactly when a bridge receives its
**own** BPDU back on a second port. The protocol is stating that a path runs
from `sw-floor` to `sw-floor`. The counters say it in numbers:

| Port | MSTP sent | MSTP received | Up since |
|---|---|---|---|
| 0/25 | 1,003,493 | **56,886** | Day −18 |
| 0/23 | 1,060,733 | 1,652 | Day −11 |

**And the same counters contain a trap.** Over the port's whole standing time the
return flow is 5.7 % of what was sent, but a single measurement 302 seconds apart
showed **150 of 150**, essentially 100 %. Both are correct: the short window caught
an active burst, and summed over 24 days the return flow amounts to about 1.4 days.
Extrapolating the window to a steady state overstates the circuit by a factor of 17.

Three further readings from the same session: the **gateway's MAC** stood in the
forwarding database on **port 25**, not port 13 where the gateway physically hangs;
**port 13 had zero learned addresses** while `Forwarding`; and MAC aging is
**300 s**, suspiciously close to the gateway outage durations of 286.8–330.3 s.

That set up an elegant causal chain — the gateway's address is learned on the
discarding port, traffic for it disappears, and after 300 s of aging it corrects
itself. **Its own timeline refutes it.** The MAC moved once that day, at
08:57:47, and stayed; the gateway outage began at 10:19:53, **82 minutes later**;
in between the gateway answered without a gap; and at 11:17 it answered in
0.585 ms while the MAC still stood on port 25. A forwarding-database entry on a
discarding port is not forwarding-effective — the switch delivered over port 13
throughout. The 300-second signature remains a strong hint at an aging or timeout
mechanism *somewhere*, but not there.

One further check went with it: comparing gateway latency with the MAC on port 13
against latency with it on port 25 (medians 0.581 vs 0.580 ms) was meant to rule
out a radio path, but if nothing flows over port 25 that is one path measured twice.

The port timers date the beginning. Port 23 had been up continuously for
17 d 22 h 36 min when they were read an hour later, putting the link event at
about 13:40 on Day −11 — the same day the earliest independent signature of the
fault appears; port 25's uptime equals the system uptime, so no counter was
cleared by hand there. What is
**not** proven is that port 23 was disconnected before that day: the switch counts
three link-down events without timestamps, so all that is established is how long
the connection has been unbroken.

### 4. What the port isolation did — and did not — do

At 20:40 on Day 6 the campaign stopped being pure observation. Ports 23 and 25 went
into the same protected-port group, verified on the device at 20:41:15
(`Member Ports : 0/23, 0/25`), suppressing exactly the step the switch contributes
to the circuit: flooding broadcast out of port 23 towards port 25.

**Isolating a single port would have done nothing.** The device implements
protected ports, not private-VLAN isolation: a protected port may still talk to
every unprotected port, so only both ports in one group separate the path.
Shutting a port down instead was rejected because the switch has no PoE — the
access point would keep its power and fall back to a radio uplink, i.e. into
exactly the state under investigation.

The expectation was written down **before** the window: no flood for 24 h means the
23↔25 path was part of the circuit; continuing floods mean it is not, and the next
candidate is port 10. In either case the counter-test — lift the isolation, see
whether the storms return — is mandatory, because without it the result is a
coincidence.

A before/after baseline was taken from **one capture file** with identical
technique, to distinguish "the storm stopped" from "the probe went blind":
loop-detection frames from port 23 at 30.0/min on both sides, from port 41 at
59.9/min on both, broadcast plus multicast 31.3/s against 31.4/s. Both sources
still arrived, because `probe1` sits on port 37 and is not in the protected group;
had it been isolated too, every subsequent quiet hour would have been a
measurement artefact
([F5](pitfalls.md#f5-absence-of-the-symptom-is-not-a-finding-unless-the-chain-was-alive)).

**The result, after a full working day: the isolation damped the mechanism and
did not break it.** Intervals above 1,000 broadcast fell from **280 in 66 hours**
before the intervention to **6 in 46 hours** after it, and the peak per 5 s from
49,760 to 19,569 — a thirty-two-fold drop in the hourly rate. Flood minutes on the
following working day fell from 21–33 to **2**, and it was the first **working**
day of the campaign without a wave. And yet on that same day the circuit closed twice, once at the
highest duplication factor ever measured here. The pre-registered criterion — 24
hours without a flood proves the path — came out **negative**: the 24 hours
arrived, and so did the storm.

Two honest deductions followed. **The 1 % probability was withdrawn**: it assumed
five to six events a day, which held only for Days 3 to 6, whereas a packet-rate
series from monitoring that predated the investigation showed **five completely
quiet days** before Day 3, including the Sunday of Day −1. The one point that
survives for the isolation is that the quietest day of the entire series was Day 7.

**And the tidy explanation of the return path was withdrawn too**, within an hour
of being written. An active test after the intervention found **zero copies** in
99 minutes across 1,549 of the probe's own ARP frames, while the forwarding
database kept relearning addresses from behind the uplink on port 23 without a
single port change — at 300 s aging, refreshed continuously. The explanation
offered was "the return path works once, the second lap is suppressed", and it
does not hold: `probe1` is on port 37 and **not** in the protected group, so a
broadcast entering on port 23 would be flooded to it and seen a second time. It
never was. Either that return path carries only unicast, in which case it is
irrelevant to a broadcast storm, or the address learning has another cause
entirely. Never settled.

What *is* established is sharper: a frame from behind the uplink can only return
to port 23 over a connection **outside the switch fabric**, and the isolation made
that path visible by removing the multiplication that had buried it.

### What else the data said

Three results are worth carrying out of the case, each having replaced a plausible
assumption.

- **The dose acts, not the peak.** Over 54 flood minutes, broadcast per minute
  correlates with loss at Spearman **0.887**, the extrapolated peak of the
  strongest 5-second interval only at 0.458.
- **A drop needs duration, not depth.** A 17-minute window held **48** gaps over
  1 s and produced 8 waves; a 10-second surge at the same percentage loss held
  **3** and produced none, with four sessions open. No single gap in either case
  reached a session timeout.
- **The gateway outages are a timer, not queue drainage.** Fourteen clean
  outages — the series as it stood when this was computed, excluding the
  22-minute gap of Day 4 as an outlier: median 300.8 s, span
  286.8–330.3 s, coefficient of variation **4.6 %**, while the triggering flood
  strength varies by a factor of 18 and the rank correlation between the two is
  0.209. Queue drainage would scale with load. The fifteenth outage, at 20:02 on
  Day 6, arrived afterwards at **285.9 s** and sits 0.9 s below that span —
  close enough to leave the finding intact, and the reason the figure quoted in
  the timeline is not inside the range quoted here.

## Falsification criteria, fixed in advance

On Day 3, three conclusions were revised within a few hours: whether the
hypervisor was reachable at all, when the switch swap had actually happened, and
which switch port belonged to which device. Every revision was correct, because
new data had arrived — but all three were *retrospective* interpretations, which
is exactly the situation in which the criterion quietly drifts towards whichever
hypothesis one has grown fond of. From that evening on, what would kill each
hypothesis was written down **before** the next measurement window, with the rule
that changing it later requires a date and a reason
([F1](pitfalls.md#f1-fix-falsification-criteria-before-the-measurement-window)).

| | Hypothesis | What kills it | Status at the end |
|---|---|---|---|
| **H1** | Switch / LAN fabric | A wave in which `probe1` measures **0.00 %** loss to `ts01` at the wave minute ±5 s | **Open, and measured to the contrary**: at all 14 waves of Day 4, loss was 9–21 % |
| **H2a** | Host connection (NIC, host switch port) | A wave **without** a link event in the hypervisor's log | **Satisfied** — 11 of 12 waves, with a positive control (below) |
| **H2b** | Virtual switch discards without link loss | A wave in which the discard counters do not rise over baseline in the enclosing 5-minute window | Open — but devalued: 94 % link utilisation with 2 % loss and fifteenfold latency tore **no** session |
| **H2c** | Queue / processor bottleneck | "Received packets with low resources" = 0 **and** processor time < 50 % at the wave minute | **Satisfied** — 0 packets, 1.6 % CPU |
| **H3** | Transport-specific (session protocol over UDP) | A wave in which the **TCP-only control session tears** while the network measures clean | **Satisfied** — it tore during heavy waves, and it provably never uses UDP |
| **H4** | Update regression | Symptom onset demonstrable **before** the last update | **Satisfied** — signature on Day −11, last update two months earlier |
| **H1a** | The circuit runs over the 23↔25 path | **24 h without a flood** after the isolation; counter-test by lifting it is mandatory | **Answered negatively** on Day 8 — the 24 hours came, the storm came anyway |

H1a is reproduced as written and is the odd one out: "24 h without a flood" would
have *confirmed* the path rather than killed it. Worth noticing rather than tidying
away — the one row framed to be met rather than to fail is also the one that failed.

Two rules of application came with the table, and they are what makes it work.
**1. A satisfied row stays satisfied.** A later contrary finding does not undo it;
it is a *new* entry with its own date. Otherwise the criterion travels with
expectation. **2. "No finding" is a finding only if the measurement worked.** H2a
counts as satisfied rather than merely "nothing found" because one wave provides
the positive control: on Day 1 at 15:22:53 the hypervisor's physical NIC lost link
for 59 seconds and the log recorded it, while 14 sessions dropped. The host *does*
log these events. The same reasoning disqualified another source entirely — SNMP
counters from `sw-floor` were missing for **12 of 18 flood minutes** and for only
31 of the remaining 1,114 (p = 2.8·10⁻¹⁴), because the switch stops answering SNMP
under broadcast load, so an empty discard result at a wave minute is a blind spot
rather than an acquittal
([D2](pitfalls.md#d2-snmp-counters-go-blind-exactly-when-it-matters)).

One criterion was amended on the evening of Day 3, and the note is part of the
record. H1 originally read
"**all three** probes measure 0.00 %". The switch port views then showed that the
hypervisor hangs on `sw-core-b` while probes 2 and 3 hang on `sw-core-a`, so their
path to `ts01` runs `sw-core-a → sw-core-b → hv01` — a switch-to-switch leg **no
real client takes**, and one that did not exist before the swap on Day 1. "0.00 %
at all three points" would have killed H1 too cheaply; only `probe1` is decisive
([A3](pitfalls.md#a3-only-one-probe-sits-on-the-path-users-actually-take)).

What none of these rows can do is *prove* a cause. They exclude. If one hypothesis
is left standing, it is the best available explanation, nothing more.

## The correction table

Every statement below was treated as established at some point and later fell. All
are kept, with their refutations, because deleting a wrong intermediate state
removes the evidence that it was wrong and somebody re-derives it a week later
([F8](pitfalls.md#f8-keep-withdrawn-statements-with-a-note)).

| Stated | Corrected to | What brought it down |
|---|---|---|
| "The symptom began on Day −8" | Not the beginning — only the first *report* | An independent metric shows the same signature on Day −11 |
| "The hypervisor has no interface in the client VLAN and cannot be measured" | It answers on `.21` | `ipconfig` at its console; ping with 0 % loss |
| "A host fault explains neither the application nor the video-conference outage" | The second terminal server runs on the **same metal** — the host explains two of three symptoms | Guest list read from the host |
| "The switch swap began at 15:22" | It began around 18:06; the short link losses at 15:22 are something else | Durations: 59 s and 2 s against 23 min |
| "The wave at 15:22 on Day 1 is an artefact of the rebuild work" | It stays in the statistics — and is the **only** positive evidence for the host hypothesis | The same correction |
| "The workstation appearing in the waves is an office machine on the affected floor" | It hangs on `sw-core-b` in the server room, so from `probe1` the path to it crosses the uplink | Switch port view |
| "All three probes are equivalent for H1" | Only `probe1` reproduces the client path | Hypervisor on `sw-core-b`, probes 2 and 3 on `sw-core-a` |
| "`probe3`'s latency is a network signal" | It measures its own 100-Mbit port | Throughput measurement: 98 Mbit/s on a port negotiated at 1000 |
| "Missing NIC teaming could explain the waves" | Refuted | 94 % utilisation measured live: 2 % loss, fifteenfold latency, **zero** drops |
| "The terminal server was reachable throughout every wave" | Evaluation error — actual loss 9–21 % | `no answer yet` lines had been counted as replies ([B1](pitfalls.md#b1-no-answer-yet-is-neither-an-answer-nor-a-loss)) |
| **"On Day 4 the gateway was gone for 1,334 s, and only one probe saw it"** — then, a day later, "no, that was 44 restart gaps summed together and never happened" | **Corrected twice.** The "only one probe" half was wrong; the retraction was wronger still. The router was gone **twice** that day, seen at all three probes, and both times for almost exactly 22 minutes: 1,334 s from 12:55:57 for reasons never established, then 13:22–13:44, when somebody switched it off at the wall. The near-identical durations are a coincidence, and naming either as "the 22 minutes" is what makes the record ambiguous | First a restart-marker miscount, then a recount: 6,564 `no answer yet` lines in the hole and **zero** start markers ([B3](pitfalls.md#b3-never-sum-gaps-across-a-restart-marker), [B6](pitfalls.md#b6-when-ping-goes-silent-entirely-there-is-nothing-to-count)) |
| "The multiplication of DHCP packets comes from the server" | A circulating frame; the server behaves normally | 86,769 packets on 344 IP IDs, plus the returning own ARP |
| "The distributor behind port 23 is a switch from the vendor its MAC prefix names" | An unmanaged switch with a Realtek chipset | That vendor's loop-detection ethertype in the capture, not the OUI ([D6](pitfalls.md#d6-never-infer-identity-from-the-oui)) |
| "The device on the port-25 segment is a switch and the BPDU source there" | A media endpoint that merely carries that vendor's NIC. The BPDU source stays unidentified | HTTP fingerprint |
| "The four devices flooding ARP are VoIP phones" | Four access points searching for their controller | TLS certificates and LLDP capabilities |
| "A controller removed around the time the fault began is the trigger" | Withdrawn — the address came from neither DHCP nor DNS, so it is a stored URL from a one-off adoption and dates nothing | DHCP without the relevant option, NXDOMAIN, no PTR |
| "Loop Guard breaks the circuit over these ports either way" | Wrong — it is an STP function and has no grip on a path outside the STP domain | Own error, corrected on technical grounds |
| "The device on port 41 speaks loop detection, so its silence is a port block" | It speaks a different, undocumented frame type of the same vendor. **The observation stands eightfold; the explanation is gone** | Frame-type analysis, plus a third silence duration falling into the supposed gap between two fixed values |
| "One gateway outage has no flood in its window" | It has one — it was **multicast-dominated** (30,672 multicast per 5 s against 5,267 broadcast) and fell through a broadcast-only threshold | Reading field 4 as well as field 3 ([C4](pitfalls.md#c4-a-flood-definition-counting-only-broadcast-misses-multicast-storms)) |
| "Every gateway outage falls in a flood window — 16 of 16" | **Broken on Day 8.** The 17th has no flood at all: broadcast maximum 240 per 5 s against a median of 112 | The multicast explanation does not apply — there was nothing there |
| "`ap1` is on port 23, `ap2` on port 25" | Exactly swapped | The provider's on-site port list. The LLDP observation had seen the AP at the wrong port — probably the effect itself rather than a measurement error |
| "The address `.11` identifies that access point" | It changed hands mid-campaign; anyone checking "the AP" by address was measuring a stranger and reading its silence as the AP's | A new MAC at that address, behind the uplink ([D7](pitfalls.md#d7-an-address-is-not-a-device)) |
| "The circuit detector shows no excess of copies in the event windows" | **The detector is unfit** — see below | Validation against a positively known storm |

## The withdrawn detection method

A detector built on Day 6 looked better than anything else available: it needed no
packets of its own, ran continuously, resolved to two seconds, and had an
empirically established baseline. It read the per-frame identifier out of the
loop-detection frames the unmanaged distributor behind port 23 emitted every two
seconds — any identifier arriving twice is a copy, and copies are the circuit.

Run over the five precursor events of the first 24 hours under isolation, it
returned **3.94, 3.83, 3.82, 3.81 and 3.83** against a quiet baseline of **3.95**,
sampled three times at 300 arrivals over 76 identifiers each — event windows
indistinguishable from quiet. Read at face value that is a clean negative: the
precursor still fires, the circuit no longer forms, the intervention works. It was
written down as a finding.

**Then it was validated against a storm that had already been proven.** On the
20:02 event of Day 6, where the IP-ID method returned 44.1, this test returned
**3.86** — the same number as every quiet window.

The cause was in the identifier: those bytes are not a per-frame identifier, the
source repeats the same value on its 2-second cadence, and that is where the
baseline of 3.95 instead of 1.0 comes from. The detector could not have seen a
circuit at all.

It was **blind, not negative**. Every conclusion drawn from it was withdrawn, and
it is kept in the record here and in [Proving a
loop](proving-a-loop.md#the-method-that-was-withdrawn) because the failure
generalises: **a control window is not a validation**. This test had one and passed
it — event and quiet agreed, which read as consistency and was in fact the symptom.
Only a **positive** control, a case known to be true, exposed it
([F2](pitfalls.md#f2-a-negative-result-from-a-blind-test-is-not-a-negative-result)).

There is a second, quieter cost: the IP-ID test could not be applied to those five
events either, because the capture trigger fired on a **broadcast flood** and after
the intervention there were no floods, while the continuous capture was filtered to
`stp or arp` and held no IP broadcast at all
([E2](pitfalls.md#e2-a-narrow-filter-makes-whole-phenomena-invisible)). No material
exists with which those events could be checked. The fix was to trigger on the
**precursor frame** instead, which is why `flood-capture.sh` here takes two
triggers.

## How it ended

It did not end; the symptom stopped. On Day 9 the provider named a cause: a
defective PoE injector in front of `ap1`, whose intermittent contact made that
access point flip between cable and radio, so that over the radio path to `ap2`
broadcasts were passed on "as if through a physical loop".

That explanation matches three findings made here independently. A snapshot of the
switch's address table at 08:04 that morning, before the visit, put `ap1` on
**port 23** although the device hangs on port 25 — over copper, not possible.
Address migrations between ports 23 and 25 continued **after** those ports were
isolated against each other. And the circuit went round about **37 times faster at
night** (shortest lap 0.33 ms) than by day (12.32 ms), despite the night flood
being some thirty times stronger, where more load should mean more queueing and
therefore *longer* laps. Copper has no time-of-day-dependent propagation delay.

**And none of it is attributed**, because four things changed at once
([F4](pitfalls.md#f4-simultaneous-interventions-destroy-attribution)): the injector
was removed, leaving `ap1` **dead** from 10:41:42 with its replacement outstanding
— a missing power supply, not "the AP was switched off" and not a configuration
change; the other three got new firmware with meshing disabled; a cable and a small
switch in that room were replaced; and, noticed only the next day as a **fourth**
simultaneous effect, the ARP flood dried up, because the orphaned access points
sending 118,874 requests per 11.5 hours after a controller that no longer existed
were adopted onto a new one that morning.

Our own intervention was lifted at 12:09 the same day, verified on the device,
precisely so as not to be a fifth variable. That is **not** the counter-test, which
requires the isolation gone **and** `ap1` running normally and cannot happen until
the injector is replaced. Until then every quiet day is an observation, not a
confirmation.

One more complication sits in the data, and smoothing it over would be dishonest:
**the break in the series does not lie on Day 9.** It lies on the evening of Day 6,
at our own isolation. Day 8 was a full working day under isolation and before the
provider touched anything — flood minutes down from 21–33 to 2, and the first
working day of the campaign without a wave. On the last day that carries a
verdict, the symptom was therefore already gone before the named cause was
addressed. Day 9 did produce two more waves, but inside the provider's working
window, which was agreed beforehand to prove nothing either way.

That is not the same as the isolation having worked. It was demonstrably not
tight: on **Day 8** the circuit formed twice, once at the highest factor ever
measured, and on that same day the two phenomena that had been inseparable through
16 events occurred **separately** for the first time, in both directions — a record
duplication factor with no gateway consequence at 03:15, and a 298.8-second gateway
outage with no **broadcast** surge at all at 10:19, though the precursor pattern was
there in a form never seen before: 153 frames from one access point in two seconds,
beginning in the same second as the gateway's last reply.

The honest closing position is therefore three statements, not one. The circuit
is **proven**, directly, and localised to a three-port triangle on one switch.
**How it closed** is not proven: a radio path is the best-supported candidate and
the provider's account fits it, but a second cable between the segments was never
excluded, and neither was a bridged endpoint. **What started it**, somewhere
around Day −11, is unknown, and only the site's own history could answer it — a
condition that has existed for months explains no start date.

Nine days of measurement narrowed a fault from "the terminal server is broken" to
three ports of one switch, and could not close the last step from a section to a
device. That step needs somebody in the room ([what this
method cannot do](target-matrix.md#what-this-method-cannot-do)) — a passive WLAN
scan for an extra unnamed BSSID, or simply looking at whether both Ethernet
sockets on one of those access points are occupied.

## Where the lessons live

Everything generalisable from the above is in the chapters this one supports:
**[Pitfalls](pitfalls.md)**, 44 entries, most of them from this case;
**[Proving a loop](proving-a-loop.md)**, the three detection methods used here
and the fourth one in full; and **[The method](target-matrix.md)**, why `probe1`
was the only probe that could decide anything. The measurement data is not
published — the analysis is demonstrable end to end on
[synthetic data](../../examples/synthetic/generate.py) carrying the same
signatures: a duplication factor, a broadcast surge, a gateway gap.
