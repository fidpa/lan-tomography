# Tear down a campaign

When the investigation is over. Do this deliberately: the data is evidence, and
deleting it is not reversible.

## 1. Stop measuring

On every probe and on the collecting machine:

```bash
sudo systemctl disable --now 'lt-*.timer' 'lt-*.service'
systemctl list-units 'lt-*' --all
```

## 2. Secure the evidence before deleting anything

```bash
sudo /opt/lan-tomography/src/ops/compress-logs.sh
sudo tar -C /var/log -czf ~/campaign-$(date +%F).tar.gz lan-tomography/
```

Keep it as long as the conclusions are still being relied on. If findings went
to anyone who will act on them (a service provider, your own management, a
customer), the raw data is what supports them later.

**Captures contain payloads.** Treat the archive as confidential, and check
what your agreement with the network's owner says about retention before
keeping or sharing it.

Check the size before archiving: `<base>/capture/` holds the pcaps, and any
daily profile run without `LT_PCAP_RETENTION_DAYS` has been growing without a
cap for the whole campaign. `du -sh /var/log/lan-tomography/capture/*` shows
which profile that was.

## 3. Write down what the measurement could not see

More useful than it sounds, and easy to skip while relieved.

- Which windows was the chain impaired for? Those are **unobserved**, not
  clean.
- Which filters were narrow? A `stp`-only capture saw no broadcast flood.
- Which thresholds were in force? A quiet log at `LT_SURGE_MIN=1500` means
  something different from a quiet log at 150.
- Were several changes made at once? Then nothing is attributable, and saying
  so is the finding.

## 4. Remove the tools

```bash
sudo rm -f /etc/systemd/system/lt-*.service /etc/systemd/system/lt-*.timer
sudo systemctl daemon-reload
sudo rm -rf /opt/lan-tomography
```

Configuration and data are kept deliberately. Remove them explicitly when you
have decided to:

```bash
sudo rm -rf /etc/lan-tomography /var/log/lan-tomography
sudo userdel lan-tomography
```
