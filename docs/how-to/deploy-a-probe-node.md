# Deploy a probe node

A probe node is a machine somewhere else on the network that runs the same
measurement and ships its data back. Two probes that disagree are the entire
method.

## What it needs

Any Linux machine with a wired connection to the segment you want to see: a
spare mini-PC, a Raspberry Pi, a virtual machine on a host in that segment. It
must be **wired**: measuring a wireless client tells you about the wireless.

## Install

```bash
git clone https://github.com/fidpa/lan-tomography
cd lan-tomography
sudo ./install.sh --role node
```

The node role installs only what a probe needs. Installing the switch poller
here too would leave a unit failing every minute, and a permanently failing
unit trains people to ignore `systemctl --failed`.

## Configure

```bash
sudoedit /etc/lan-tomography/lan-tomography.conf
```

Three values matter, and one of them is easy to get wrong:

```bash
LT_IFACE="eno1"          # differs per machine
LT_NODE_NAME="probe2"    # MUST be unique across probes
LT_TZ="UTC"              # MUST be identical across probes
```

`LT_NODE_NAME` collisions make two probes overwrite each other's evidence.
`LT_TZ` disagreements make correlation worthless. Four sources disagreeing
about the timezone once cost two wrong conclusions in a single day.

The target list can differ per probe, and usually should: a probe exists to see
a path the others cannot.

```bash
sudo systemctl enable --now lt-probe-node.service
```

## Capture privileges

The unit grants `CAP_NET_RAW` and `CAP_NET_ADMIN` ambiently. If the capture
exits immediately, that is what to check:

```bash
journalctl -u lt-probe-node.service | grep -i capab
```

## Ship the data back

Any file sync works. Pull from the collecting machine:

```bash
rsync -az probe2:/var/log/lan-tomography/ping/ \
          /var/log/lan-tomography/probe2/ping/
rsync -az probe2:/var/log/lan-tomography/targets.conf \
          /var/log/lan-tomography/probe2/targets.conf
```

**Do not use `--delete`.** A day compressed on the probe would come back
uncompressed and sit next to its own archive, and both would be counted. The
analysis handles that case, but only because it was hit once.

**Bring the matrix with the data.** `probe-node.sh` writes its own
`targets.conf` into its base directory for this; `src/ops/sync-node.sh` pulls
it automatically. Without it, `correlate.py` stops rather than analyse this
probe's data against a matrix from another machine:

```
no target matrix for --ping-dir /var/log/lan-tomography/probe2/ping
```

That stop is the point. The target list *should* differ per probe, so the local
matrix silently omits every target only this probe measures: seven rows became
five once, and the two that vanished were the reason the probe existed.

## Watch the chain, not just the probe

```bash
sudoedit /etc/lan-tomography/lan-tomography.conf
#   LT_REMOTE_HOST="probe2"
#   LT_REMOTE_UNITS="lt-probe-node.service lt-pktrate.service"

sudo systemctl enable --now lt-liveness-check.timer
/opt/lan-tomography/src/events/liveness-check.sh --report
```

This is not optional. `systemd` cannot see a single dead ping loop inside
`probe-node.sh`; the unit stays `active (running)` on the strength of its
capture while measuring nothing. That is how one probe's user path went
unobserved for a day.
