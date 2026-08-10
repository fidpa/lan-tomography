# The method: choosing probes and targets

How to place measurement points and assign target roles so that the network
sections you care about become individually determinable.

This is the part that is actually hard. Installing the tools takes an hour;
getting the matrix right is the difference between nine days of data that
answers your question and nine days of data that does not.

---

## The idea

Treat every measured path as the sum of the sections it crosses.

With `p` probes and `t` targets you get `p × t` path measurements over some
number of unknown sections. That is a linear system, `A x = b`, where `x` is
the per-section loss you would like to know and `b` is what you measured.

What you can determine is not "how many targets did I ping". It is the **rank**
of that system.

This has one consequence that is easy to get wrong and expensive to discover
late:

> Adding more targets **behind the same bottleneck** adds rows to `A` without
> adding rank.

Ten servers in one rack, measured from one probe, tell you about one uplink.
You will have ten times the data and exactly as much information.

### A worked example

From the campaign this repository came from:

| Configuration | Rank | Sections | Individually determinable |
|---|---|---|---|
| 3 probes, 8 targets | 9 | 11 | **1** |
| \+ one target terminating ON a switch | 10 | 11 | 4 |
| \+ the remaining switches | 12–13 | 14 | most of the path |

Two quantities stayed indeterminable in the first configuration no matter how
long the measurement ran. Nine days of data would not have fixed it; a
different matrix did, in an afternoon.

The move that changed it was adding a target that **terminates on a switch** —
the switch's own management address. Until then every path ended at an endpoint,
so the switch was only ever measurable together with the endpoint's own
cabling, never by itself.

### Before you start

Write down which sections you want separated. Then, for each candidate target,
ask: **does its path differ from a target I already have?** If it shares its
entire path with something you already measure, it is worth almost nothing.

You do not need to do the linear algebra formally. Drawing the topology and
marking which sections each path crosses is enough to see where the rank is
missing.

---

## Probe placement

Three probes is usually the point where this becomes useful, and the third one
is the one people skip.

| Probe | Why |
|---|---|
| **Server side** | Sees the symptom host's own segment. Usually the easiest to place, and on its own it tells you almost nothing about the fault. |
| **Across the uplink** | The one that separates "my floor" from "everything above it". Without it, a floor switch and its uplink are one blob. |
| **On the user path** | A machine where the complaints come from. Everything else measures what servers experience; only this measures what users experience. |

Rules that come out of experience rather than theory:

- **Measure the probe itself first.** A machine on a 100-Mbit port behind a
  desk phone will report its own limits as the network's. Establish its floor
  before trusting anything it says (pitfall A1).
- **An outage on every target is the probe**, not the network (pitfall A2).
- **Probes must agree on the clock.** Set `LT_TZ` identically everywhere and
  write down which value you used. Four sources disagreeing about the timezone
  cost two wrong conclusions in a single day.
- **Probes must produce byte-comparable output.** That is why there is one
  `probe-node.sh` and not one per site.

---

## Roles

The role is what turns a set of measurements into a verdict. It states what an
outage of *this* target rules in and rules out.

| Role | What its failure means |
|---|---|
| `symptom` | The host showing the reported problem. Everything is relative to this. |
| `hypervisor` | The physical host underneath the symptom, measured directly. Separates "in front of the host" from "inside the host". |
| `same-host` | Another guest on the same physical host. Fails with the symptom → the host or its virtual switch. Symptom fails alone → inside that guest. |
| `other-host` | Different hardware, same network. Separates "this machine" from "this network". |
| `fabric-ref` | An ordinary endpoint deep in the shared path, usually the gateway. Loss here is the shared path. |
| `uplink-ref` | Reachable only across the uplink between segments. |
| `client-path` | A workstation on the path users take. Allowed to be offline overnight. |
| `switch-ref` | A switch's own management address. **Carries no verdict** — see below. |
| `wan-ref` | Beyond the gateway. Use **two**, at different operators. |

### Why `switch-ref` carries no verdict

A switch answers ICMP from its **management CPU**, not from its forwarding
path. Under load that CPU delays or discards replies while the device forwards
traffic perfectly.

Measured, not assumed: switch targets produced apparent outages of up to **24
seconds** while every target *behind* the same switch stayed clean in the same
window, and other probes saw the same switch answering normally at the same
moment. Three probes at five packets a second is fifteen packets a second
arriving at a CPU that was not built for it. The measurement was measuring
itself.

The visible tell is scatter: 1.6–5.5 ms to the switch against 0.6 ms to
ordinary hosts in the same segment.

So keep these targets — they raise the rank, and they are the only way to see
the switch at all — but exclude them from the judgement. When only `switch-ref`
targets fail, `correlate.py` reports **"outage outside the judgement matrix"**
rather than calling the network clean, because a clean verdict would contradict
the table printed underneath it.

### Why two `wan-ref` targets

A single external target can fail for reasons that have nothing to do with your
network: the operator, peering, that specific host. As a judging role it would
produce a confident "the fabric is broken".

With two at different operators the reading is unambiguous: one fails → that
target. Both fail together → the way out.

## The verdicts

`correlate.py` prints one of nine labels per window. Treat the label as an
interface: it is the part that gets pasted into a mail subject or a ticket, so
it has to survive being read on its own, without the table underneath it.

| Verdict | What failed |
|---|---|
| `NETWORK CLEAN` | Nothing. No target had a contiguous outage over the threshold. |
| `OUTAGE OUTSIDE THE SYMPTOM PATH` | Something with a judging role, but **not** the symptom host. The symptom is not explained — and the network was not clean. |
| `OUTAGE OUTSIDE THE JUDGEMENT MATRIX` | Only roles that carry no verdict (`switch-ref`, `uplink-ref`, `wan-ref`). The statement arises from the difference between probes, not from this window. |
| `CLIENT PATH` | Workstations, while the servers held — between desk and server room. |
| `FABRIC` | The symptom host together with targets on other hardware — switch, cabling, uplink. |
| `HOST UPLINK` | The symptom host **and** its hypervisor — in front of the hypervisor. |
| `HOST INTERNAL` | The symptom host and its sibling guests, hypervisor clean — inside the host. |
| `GUEST SPECIFIC` | The symptom guest alone — its virtual NIC or queue. |
| `UNCLEAR` | No ping data for the window. Not a result. |

Two of the nine say "something failed, but it does not explain the symptom",
and both are deliberately worded so that neither can be misread as an
all-clear. That distinction was bought the hard way — see
[pitfalls F3](pitfalls.md#f3-a-verdict-must-never-contradict-its-own-table).

---

## Deriving thresholds

**Do not copy the defaults.** They come from one campaign on one network. Ported
unchanged to a busier network they report continuously; to a quieter one they
report nothing. Either way the operator stops reading them.

### Outage threshold (`LT_OUTAGE_THRESHOLD_S`)

How long an uninterrupted outage must last to count.

Run the measurement for a day with no events, then look at the longest
uninterrupted losses in that quiet data. Set the threshold above them.

The shipped default of 2.5 s was raised from 1.0 s after the first hour of
measurement, because Windows guests deprioritise ICMP: three of them dropped
1.2–2.6 s of replies individually and at different times, while physical
targets answered continuously. At 1.0 s every one of those everyday hiccups
would have produced a false verdict.

### Flood and surge (`LT_FLOOD_MIN`, `LT_SURGE_MIN`)

Run `pktrate.sh` for a day. Then:

```bash
src/analyze/pktrate-scan.py <base>/pktrate/*.log --surge 999999 --flood 999999
```

Take the median and the maximum of the quiet periods. Set the **surge**
threshold about an order of magnitude above the median, and the **flood**
threshold near the point where the interface is visibly saturated.

Reference values from the campaign this came from: quiet median about 100
broadcast frames per 5 s, quiet maximum 243, storm peaks above 30,000. The
**ratio** is the signature, not the absolute number.

---

## Checking the matrix before you rely on it

1. **Does every section you care about appear in at least one path that no
   other target shares?** If not, add a target or a probe.
2. **Does at least one target terminate on a switch?** Without it the switches
   are invisible as objects and only measurable through somebody's cabling.
3. **Is there a target on the actual user path?** If not, you are measuring the
   server room.
4. **Are the offline-tolerant roles marked?** Otherwise every overnight
   analysis reports the workstations as a total outage.
5. **Do the probes agree on the clock?** `LT_TZ`, written down.
6. **Is there something that will tell you the chain died?**
   `src/events/liveness-check.sh`, on its own timer. An absence of events from
   a dead probe looks exactly like an absence of events from a fixed network.

---

## What this method cannot do

It localises **sections**, not devices. A verdict of `FABRIC` says the fault is
in the shared path — not which port, and certainly not which device is at
fault. Going from a section to a device needs the switch's own data
(`switch-probe.py`, `fdb-probe.py`), a capture, or somebody walking to the
rack.

It also cannot tell you about a section no path crosses. That is the whole
point of computing the rank first, and the reason to do it before the
measurement rather than after.
