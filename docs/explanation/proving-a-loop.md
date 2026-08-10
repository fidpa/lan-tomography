# Proving a layer-2 loop

Three methods for showing that frames are circulating, what each one is blind
to, and one that had to be withdrawn.

The withdrawn one is the most useful part of this page. A detection method that
returns nothing is only evidence of absence if you have shown it would have
found the thing.

---

## Why this is hard

A forwarding loop is not a device fault. Every switch involved is doing exactly
what it was told: forwarding a broadcast frame out of every port except the one
it arrived on. If two of those paths meet, the frame comes back, and the
switches forward it again.

The consequences are what you notice:

- broadcast and multicast rates rise by orders of magnitude
- MAC addresses appear to move between ports
- endpoints lose packets while every switch reports zero errors
- it can be **intermittent**, because it only happens when a particular path
  exists — a specific access point in a specific state, a cable somebody moves,
  a link that comes up under load

Intermittency is what makes this worth tooling. A permanent loop takes down the
network and gets fixed in an hour.

---

## Method 1: duplication factor by IP identification field

**The one that worked.**

Every IP packet a host sends carries a 16-bit identification field. Within a
short window, a sender's packets have distinct IDs. If a frame circulates, the
**same ID arrives repeatedly** at the capturing host.

So: capture IP broadcast at the probe, count total frames against distinct IDs.
It needs no packets of your own — the traffic being counted is whatever the
network is broadcasting, which is why it works where the frame-length test
([pitfalls E6](pitfalls.md#e6-a-frame-length-test-only-works-on-your-own-frames))
goes blind for want of an original in the window.

```
duplication factor = frames observed / distinct IP IDs
```

| Window | Factor |
|---|---|
| quiet | **1.00** |
| storm | **37.02** |
| a later storm | **41.92** |
| the strongest measured | **56.11** |

A factor of 1.00 in quiet periods is the control: without a loop, every frame
appears once. Values in the tens are frames going round.

**Why it is trustworthy:** its readings were checked against loops already proven
by other means — a probe receiving its own broadcasts back — and it reproduces
them, returning large numbers where those had established a loop and 1.00 where
they had not. Note that the 44.1 quoted for one of these storms elsewhere is this
same method on a shorter window, not a second opinion.

**What it is blind to:** it needs the capture to include IP broadcast. A filter
of `stp or arp` excludes exactly the traffic this method needs, which is why
several event windows in the campaign this came from could not be tested this way
at all — the floods had stopped, the trigger only fired on floods, and the
continuous capture held no IP broadcast. It also needs enough broadcast in the
window to divide by: a handful of packets gives a ratio near 1.00 whether or not
a circuit exists.

**Exclude `id=0` before dividing.** Senders that never fragment may emit a
constant zero ID, and one dominant zero swamps the ratio in either direction.

---

## Method 2: MAC movements in the forwarding database

Poll the switch's forwarding database repeatedly and log addresses that change
port. A loop makes the switch relearn addresses back and forth, because the
same frame arrives from two directions.

`src/switch/fdb-probe.py` does this. In the campaign this came from, one port
pair accounted for **113 movements**, which is what identified the return path.

**What it proves:** that frames are arriving at a port they should not arrive
at. That is a topology statement and a strong one.

**What it does not prove:** which device is responsible. While a loop is
running the switch learns **every** sender's address on the return port,
including the measuring machine's own. A MAC at the return port is a passenger.

**What it is blind to:** loops entirely behind an unmanaged device, which never
reach a switch you can poll. And the polling has the blindness described in
`pitfalls.md` D2 — the agent stops answering while the device is busy, which is
exactly when you want it.

---

## Method 3: counting by the second

The crudest and the most robust: does the broadcast rate rise, and does it fall
again, and does that line up with the symptom?

`src/analyze/pktrate-scan.py` on the packet-rate logs. Quiet median around 100
frames per 5 s, storm peaks above 30,000. The **ratio** is the signature.

**What it proves:** that something is flooding. Not that it is a loop — a
broken device emitting broadcast at line rate looks identical.

**Why it still matters:** it works when everything else is blind. It needs no
switch access, no capture filter luck, and no cooperation from anybody. It is
also the method most likely to be running when an intermittent event happens,
because it is cheap enough to leave on.

**What it is blind to:** a definition counting only broadcast misses multicast
storms. One measured window had 30,672 multicast against 5,267 broadcast.

---

## The method that was withdrawn

**Read this one even if you skip the others.**

A fourth test was built on frame-level markers: count how many captured frames
appeared to be copies of one another, using a vendor loop-detection frame's own
identifiers.

It looked sound. It ran. It produced:

| Window | Factor |
|---|---|
| five event windows | 3.94, 3.83, 3.82, 3.81, 3.83 |
| quiet baseline | 3.95 |

The same number in both. Read naively, that is a clean negative: no excess of
copies during the event, therefore no loop.

It was then validated against a storm that had **already been proven** by
method 1. On that storm — where method 1 returned 44.1 — this test returned
**3.86**.

The test was **blind, not negative.** It could not have detected the thing
whose absence it appeared to confirm. Every conclusion drawn from it was
withdrawn.

Two things follow, and they generalise well beyond loops:

1. **Before believing a negative result, validate the method against a case
   where you already know the answer.** If you cannot, the honest wording is
   "this method found nothing", never "there was nothing".
2. **A control window is not a validation.** This test had a control window and
   passed it — both windows agreed, which looked like consistency and was
   actually the symptom. Only a positive control, a case known to be true,
   exposed it.

The withdrawn test is kept in the record deliberately. A repository that shows
only its successful methods teaches the wrong lesson about how this work goes.

---

## What to run, in what order

1. **`pktrate.sh` on every probe, continuously.** Cheap, always on, works
   without cooperation. If nothing here ever rises, the rest is moot.
2. **`fdb-probe.py`, if you can reach a switch.** Movements localise the return
   path.
3. **A capture including IP broadcast, during an event.** The IP-ID test needs
   it, and a narrow filter is why several events in the original campaign could
   never be tested. Widen the filter when a storm is in progress, not
   afterwards.
4. **`event-watch.sh`, so you find out while it is happening.** An hour later
   this is a log entry. During the event it is evidence, and you can widen a
   filter, poll a switch, or walk to the rack.

---

## Related

- [Pitfalls](pitfalls.md), sections D and E — how switch polling and captures
  mislead
- [The method](target-matrix.md) — placing probes so the loop's location is
  determinable at all
- [Log formats](../reference/log-formats.md) — what the packet-rate and FDB
  files contain
