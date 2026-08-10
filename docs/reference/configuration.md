# Configuration

Every setting, where it is read from, and what it does.

## Where values come from

Later wins:

1. built-in defaults
2. `$LT_CONFIG`, or `<repo>/config/lan-tomography.conf`
3. the environment

That order lets a systemd unit override one value without a second config file.
The units carry `EnvironmentFile=-<config-dir>/lan-tomography.conf`, so setting
something in the unit takes precedence over the file.

## Paths

| Variable | Default | Notes |
|---|---|---|
| `LT_BASE_DIR` | `/var/log/lan-tomography` | All measurement data. **Evidence, not logs** — a capture contains whatever crossed the wire. |
| `LT_LOG_FILE` | `$LT_BASE_DIR/lan-tomography.log` | Operational log only. Nothing parses it. |
| `LT_CONFIG` | `<repo>/config/lan-tomography.conf` | |
| `LT_TARGETS` | `<repo>/config/targets.conf` | |
| `LT_TCP_TARGETS` | `<repo>/config/tcp-targets.conf` | |
| `LT_SECRETS` | unset | `KEY=value` lines. Read line by line, **never sourced**. `chmod 600`, outside the repository. |

Derived and not settable individually: `LT_PING_DIR`, `LT_L2_DIR`,
`LT_PKTRATE_DIR`, `LT_SWITCH_DIR`.

| `LT_LOG_TO_STDOUT` | `true` | Set to anything else to keep the operational log out of stdout. Under systemd that means out of the journal's stdout capture too. |
| `LT_LOG_TO_JOURNAL` | `true` | Set to anything else to skip the `logger` call. Useful on a probe with no journald. |

Set by the library and not meant to be configured: `LT_VERSION` (read from
`VERSION`), `LT_LIB_DIR`, `LT_REPO_ROOT`, `LT_PING_DIR`, `LT_L2_DIR`,
`LT_PKTRATE_DIR`, `LT_SWITCH_DIR`, `LT_COMMON_LOADED`.

> **`LT_LOG_FILE` under `ProtectSystem=strict`:** if the path is not in
> `ReadWritePaths`, the write fails **silently**. The tool then looks healthy
> and logs nothing. Export it before the tool sources the library —
> `liveness-check.sh` shows the pattern.

## This probe

| Variable | Default | Notes |
|---|---|---|
| `LT_IFACE` | `eth0` | Differs per machine. |
| `LT_NODE_NAME` | `hostname -s` | **Must be unique across probes**, or two probes overwrite each other's files. |
| `LT_TZ` | `UTC` | **Must be identical across probes.** `tcpdump -tttt` writes local time with no offset. |

## Measurement

| Variable | Default | Notes |
|---|---|---|
| `LT_PING_INTERVAL` | `0.2` | Below 0.2 needs root. |
| `LT_PKTRATE_INTERVAL` | `5` | The log timestamp is the **end** of this interval. |
| `LT_RETENTION_DAYS` | `21` | Daily files older than this are deleted by the probe. |
| `LT_PCAP_SIZE_MB` / `LT_PCAP_FILES` | `50` / `10` | Ring buffer bound, 500 MB total. A **hard** cap: tcpdump overwrites its own oldest file. |

## Frame capture

`frame-capture.sh` runs one profile per instance. Ring profiles are capped by
`LT_PCAP_SIZE_MB` × `LT_PCAP_FILES`; daily profiles are **not capped** unless
you set a retention.

| Variable | Default | Notes |
|---|---|---|
| `LT_PCAP_RETENTION_DAYS` | unset | Daily pcaps older than this are deleted. Unset means **keep everything** — a capture is evidence, and silently deleting evidence is worse than a full disk. Measure your volume first: the profiles differ by three orders of magnitude. |

## Keeping captures on an event

`flood-capture.sh` copies the broadcast ring out of the ring before it is
overwritten. Run it from a timer, every two minutes.

| Variable | Default | Notes |
|---|---|---|
| `LT_KEEP_THRESHOLD` | `1000` | Broadcast **or** multicast frames per interval that trigger a save. Deliberately far below `LT_FLOOD_MIN`: bursts of one or two seconds stay under a per-minute flood definition and are still real events. |
| `LT_KEEP_LOOKBACK_S` | `900` | How far back each pass looks, so a missed run still catches the event. |
| `LT_KEEP_RETENTION_DAYS` | `14` | Kept captures older than this are deleted. This one **has** a default: it is a copy of a bounded ring, and an unbounded copy of a bounded thing fills a disk by itself. |
| `LT_PRECURSOR_NAME` | `roaming` | Which capture profile acts as the precursor trigger. |
| `LT_PRECURSOR_FILTER` | `ether proto 0x890d` | BPF filter used when reading it. |
| `LT_PRECURSOR_GROUP_S` | `300` | Collapse a burst of precursor frames into one event. |

## Thresholds — derive these, do not copy them

| Variable | Default | Notes |
|---|---|---|
| `LT_OUTAGE_THRESHOLD_S` | `2.5` | Raised from 1.0 because Windows guests drop 1.2–2.6 s of ICMP under load with nothing wrong in the network. |
| `LT_FLOOD_MIN` | `10000` | Frames per interval counting as a flood. |
| `LT_SURGE_MIN` | `1500` | The smaller precursor. About 15× a measured quiet median of ~100. |

The defaults come from one campaign on one network. Ported unchanged to a
busier network they report continuously; to a quieter one they report nothing.
[The method](../explanation/target-matrix.md) has the derivation procedure.

## Switch polling

| Variable | Default | Notes |
|---|---|---|
| `LT_SWITCH_IP` | unset | Required by the SNMP tools; they exit 2 without it rather than guessing. |
| `LT_SNMP_COMMUNITY` | unset | Read-only. Prefer `LT_SECRETS` over the config file. |

## Windows event logs (`src/contrib/` only)

Used by `evtx-peek.py`, the one tool here with third-party dependencies
(`requirements.txt`). Nothing on the network side reads these.

| Variable | Default | Notes |
|---|---|---|
| `LT_SMB_HOST` | unset | Host to read event logs from. `--host` overrides. Exits 2 without either. |
| `LT_SMB_DOMAIN` | empty | Domain or workgroup. Empty means a local account. |
| `LT_SMB_USER` | unset | From the environment or `LT_SECRETS`. |
| `LT_SMB_PASSWORD` | unset | **`LT_SECRETS` only, in practice** — reading `C$` needs a privileged account, and an environment variable is visible to anything that can read `/proc`. |

`LT_TZ` applies here too: EVTX stores UTC, and the displayed times are converted
to `LT_TZ` so they line up with the probe logs. Set it to the same value
everywhere or the correlation is off by the local offset —
[pitfall G3](../explanation/pitfalls.md#g3-event-log-timestamps-are-utc-your-capture-timestamps-are-not).

## Alerting

| Variable | Default | Notes |
|---|---|---|
| `LT_ALERT_CMD` | unset | Subject as `$1`, body on stdin. Unset means alerts go to the log and the journal only. |
| `LT_ALERT_COOLDOWN` | `3600` | Seconds between repeats of the same alert key. |
| `LT_ALERT_STATE_DIR` | `$LT_BASE_DIR/.alert-state` | |

This repository ships no mailer on purpose. Examples:

```bash
LT_ALERT_CMD="logger -t lan-tomography"
LT_ALERT_CMD="/usr/local/bin/notify-msmtp"
LT_ALERT_CMD="/usr/bin/ntfy publish mytopic"
```

## Liveness watching

| Variable | Default | Notes |
|---|---|---|
| `LT_REMOTE_HOST` | unset | SSH destination of the probe to check. |
| `LT_REMOTE_UNITS` | `lt-probe-node.service` | Space-separated units expected active there. |
| `LT_MAX_DATA_AGE_S` | `7800` | Just over two hours, matching an hourly sync. Tighter values only produce false alarms, and a watcher that cries wolf gets muted. |

## Pulling data from a probe

| Variable | Default | Notes |
|---|---|---|
| `LT_REMOTE_BASE` | same as `LT_BASE_DIR` | The probe's own base directory, if it differs from this machine's. |
| `LT_SYNC_DIRS` | `ping l2 pktrate tcp` | Space-separated subdirectories to pull. A probe that does not run a given tool simply has no such directory, which is skipped rather than reported as an error. |

`sync-node.sh` never passes `--delete` to rsync. A day compressed on the probe
would otherwise disappear here when it rotates there, and losing evidence to a
housekeeping flag is not a trade worth making. The cost is that a day archived
on the probe can come back uncompressed and sit next to its own archive — which
`log_files()` in `correlate.py` handles, preferring the uncompressed copy.

## Event watching

| Variable | Default | Notes |
|---|---|---|
| `LT_WATCH_WINDOW_S` | `300` | How far back each pass looks. |
| `LT_WATCH_SLEEP_S` | `60` | Seconds between passes. |
| `LT_PYTHON` | `/usr/bin/python3` | Interpreter for the helper tools. |
