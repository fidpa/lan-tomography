# Getting started

A first measurement on one machine, end to end, in about twenty minutes. No
switch access needed, and nothing to install on the network equipment — whether
or not it is yours to install on.

One probe cannot localise a fault — that needs at least two. But one probe
answers the first question worth asking: *is anything actually being lost?*

## 1. Get the tools

```bash
git clone https://github.com/fidpa/lan-tomography
cd lan-tomography
sudo ./install.sh --role server
```

The installer enables and starts nothing. That is deliberate: which units
belong on this machine is a decision about the measurement.

## 2. Describe your network

```bash
sudoedit /etc/lan-tomography/targets.conf
```

Start with four targets. More is not better — see
[the method](../explanation/target-matrix.md).

```
198.51.100.20   app01    symptom        # the host showing the problem
198.51.100.1    gw       fabric-ref     # your gateway
198.51.100.44   db01     other-host     # something on different hardware
198.51.100.51   ws01     client-path    # a desk where complaints come from
```

Then set the interface and probe name:

```bash
sudoedit /etc/lan-tomography/lan-tomography.conf
#   LT_IFACE="enp1s0"
#   LT_NODE_NAME="probe1"
```

## 3. Start measuring

```bash
sudo systemctl enable --now lt-ping@198.51.100.20.service
sudo systemctl enable --now lt-ping@198.51.100.1.service
sudo systemctl enable --now lt-pktrate.service
```

Confirm it is alive — silence from a dead probe looks exactly like silence from
a healthy network:

```bash
systemctl --failed
ls -la /var/log/lan-tomography/ping/
tail -3 /var/log/lan-tomography/ping/gw-$(date +%F).log
```

You should see lines with a `[unix.timestamp]` prefix and `time=… ms`.

## 4. Look at it

```bash
/opt/lan-tomography/src/analyze/pktrate-scan.py \
    /var/log/lan-tomography/pktrate/*.log
```

On a quiet network: some samples, no events. Note the numbers — that baseline is
what you will derive your thresholds from, and it is worth a day of patience.

## 5. Analyse an incident

When the symptom next occurs, write down when — to the minute — in
`/var/log/lan-tomography/waves.csv`:

```csv
start_epoch,end_epoch,count,note
1786201800,1786201860,7,seven sessions dropped
```

`date -d '2026-08-08 14:41:41' +%s` converts a timestamp. Then:

```bash
/opt/lan-tomography/src/analyze/correlate.py
```

You get a verdict per window and a table showing which targets lost packets.

## 6. Try it without waiting for a fault

```bash
cd /opt/lan-tomography
examples/synthetic/generate.py --out /tmp/demo --days 4
LT_PING_INTERVAL=1 src/analyze/correlate.py \
    --ping-dir /tmp/demo/ping --waves /tmp/demo/waves.csv \
    --targets config/targets.conf.example
```

Two windows, two different verdicts, and the reasoning for each.

## Next

- **[Pitfalls](../explanation/pitfalls.md)** — read this before drawing any
  conclusion. It is the reason this repository exists.
- [The method](../explanation/target-matrix.md) — how to choose the second and
  third probe so the results actually localise something.
- [Deploy a probe node](../how-to/deploy-a-probe-node.md)
