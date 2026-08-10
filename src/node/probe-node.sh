#!/bin/bash
# ---
# deployment: node
# status: active
# type: daemon
# requires_root: false
# ---
# probe-node.sh - continuous measurement from one probe location
#
# PURPOSE
#   Runs one measurement point: a continuous ICMP loop per target from
#   config/targets.conf, a plain-text STP capture, and a packet capture ring
#   buffer. Output format and directory layout are identical across probes so
#   the correlator can read any probe's data unchanged.
#
#   A single probe tells you something is wrong. Two probes that disagree tell
#   you WHERE. That disagreement is the entire method, which is why every probe
#   must produce byte-comparable output and agree on the clock.
#
# INVOCATION
#   probe-node.sh              run in the foreground (systemd Type=simple)
#   probe-node.sh --help       this text
#
# CONFIGURATION (environment or config/lan-tomography.conf)
#   LT_IFACE        interface to capture on            (default: eth0)
#   LT_NODE_NAME    how this probe labels itself       (default: hostname -s)
#   LT_BASE_DIR     where measurement data goes        (default: /var/log/lan-tomography)
#   LT_TZ           timezone for all timestamps        (default: UTC)
#   LT_PING_INTERVAL  seconds between echo requests    (default: 0.2)
#   LT_RETENTION_DAYS how long to keep daily files     (default: 21)
#
# DEPLOYMENT
#   This script works standalone. A probe is often a machine you are allowed to
#   touch once, so it must run from a copy of this file plus a targets.conf and
#   nothing else. If src/lib/common.sh happens to be alongside it, it is used;
#   otherwise the minimal logger below takes over. Two loggers is a small price
#   for not requiring a full checkout on every probe.
#
# THIS SCRIPT REPLACES TWO NEAR-IDENTICAL COPIES
#   The tools this repository came from carried one copy per probe. Stripped of
#   comments the two differed in exactly four lines: the interface default and
#   three occurrences of the probe's name in output. In a project whose whole
#   idea is "several probes", that is the first thing a reader would object to.

set -uo pipefail  # NO -e: packet loss is what we measure, not an error

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
readonly SCRIPT_DIR

# Use the shared library when it is there, fall back to a minimal logger when
# it is not. See DEPLOYMENT above.
if [[ -r "${SCRIPT_DIR}/../lib/common.sh" ]]; then
    # shellcheck source=../lib/common.sh
    source "${SCRIPT_DIR}/../lib/common.sh"
elif [[ -r "${SCRIPT_DIR}/common.sh" ]]; then
    # shellcheck source=/dev/null
    source "${SCRIPT_DIR}/common.sh"
else
    log() {
        printf '[%s] [%s] %s\n' "$(date '+%H:%M:%S')" "${1}" "${2}"
        return 0
    }
    log_info()    { log INFO "$*"; }
    log_error()   { log ERROR "$*" >&2; }
    log_warning() { log WARNING "$*"; }
    lt_usage() {
        awk '
            NR == 1 && /^#!/ { next }
            /^# ---$/ { fm = !fm; next }
            fm        { next }
            /^#/      { found = 1; sub(/^# ?/, ""); print; next }
            found     { exit }
        ' "${1:-$0}"
    }
fi

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    lt_usage "$0"
    exit 0
fi

# Timezone, explicitly.
#
# systemd units inherit no TZ, and `tcpdump -tttt` writes local time WITHOUT an
# offset - a timestamp that silently claims whatever zone the writer happened
# to be in. Every probe must agree, or correlating them is worthless.
#
# Default UTC because it is unambiguous. In the campaign this came from, four
# sources logged in UTC while the analysis assumed local time, and the mistake
# cost two wrong conclusions in one day - in both directions: once "the source
# is silent" when it had been running the whole time and was the most
# informative one available.
export TZ="${LT_TZ:-UTC}"

readonly BASE_DIR="${LT_BASE_DIR:-/var/log/lan-tomography}"
readonly PING_DIR="${BASE_DIR}/ping"
readonly L2_DIR="${BASE_DIR}/l2"
readonly IFACE="${LT_IFACE:-eth0}"
NODE_NAME="${LT_NODE_NAME:-$(hostname -s 2>/dev/null || echo probe)}"
readonly NODE_NAME

# targets.conf next to the script wins, so a node deployed as a single
# directory keeps its own target list.
if [[ -r "${SCRIPT_DIR}/targets.conf" ]]; then
    TARGETS_FILE="${SCRIPT_DIR}/targets.conf"
else
    TARGETS_FILE="${LT_TARGETS:-${SCRIPT_DIR}/../../config/targets.conf}"
fi
readonly TARGETS_FILE

readonly PING_INTERVAL="${LT_PING_INTERVAL:-0.2}"
readonly RETENTION_DAYS="${LT_RETENTION_DAYS:-21}"

# STP and ARP into the capture; STP only into the text log. ARP produces around
# 100 MB of text per day on an ordinary LAN and is close to unreadable as text -
# it stays in the pcap, where it can be filtered.
readonly BPF_PCAP='stp or arp'
readonly BPF_TEXT='stp'
readonly PCAP_SIZE_MB="${LT_PCAP_SIZE_MB:-50}"
readonly PCAP_FILES="${LT_PCAP_FILES:-10}"

CHILD_PIDS=()

cleanup() {
    local pid
    for pid in "${CHILD_PIDS[@]:-}"; do
        [[ -n "$pid" ]] && kill "$pid" 2>/dev/null
    done
    return 0
}
# No exit of our own in the EXIT trap - the original exit code must flow
# through. INT/TERM exit explicitly, otherwise the signal is swallowed and
# `systemctl stop` needs SIGKILL.
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

seconds_until_midnight() {
    printf '%s' "$(( $(date -d 'tomorrow 00:00:00' +%s) - $(date +%s) ))"
    return 0
}

# ---------------------------------------------------------------------------
# ping_loop <ip> <label>
#
# Continuous measurement of one target into daily files.
#   -D  prefixes each line with a Unix timestamp
#   -O  reports MISSING replies, which is the whole point: without it, a gap in
#       the data is indistinguishable from a gap in the measurement
# ---------------------------------------------------------------------------
ping_loop() {
    local ip="$1" label="$2"
    local logfile deadline started ran quick_failures=0

    while true; do
        logfile="${PING_DIR}/${label}-$(date +%F).log"
        deadline=$(seconds_until_midnight)
        started=$(date +%s)

        printf '# ---- measurement starts %s | target %s (%s) | interval %ss | probe %s ----\n' \
               "$(date -Iseconds)" "$ip" "$label" "$PING_INTERVAL" "$NODE_NAME" >> "$logfile"

        ping -D -O -n -i "$PING_INTERVAL" -w "$deadline" "$ip" >> "$logfile" 2>&1
        local rc=$?

        ran=$(( $(date +%s) - started ))

        # Brake against restart storms. The exit code separates two cases:
        #   >=2  startup failure - missing CAP_NET_RAW, but ALSO a network
        #        outage: "connect: Network is unreachable" and
        #        "SO_BINDTODEVICE: No such device" both exit 2 (measured).
        #   <=1  target is switched off; ping gives up after a run of
        #        unreachables, which is normal for a workstation at night.
        #
        # The loop NEVER gives up, and that is not defensiveness - it is a
        # scar. An earlier version returned after three failed starts. A
        # 15-second network outage therefore killed EVERY loop at once, while
        # the service stayed blocked in `wait` on the still-running capture:
        # systemd kept reporting "active (running)", nothing was being
        # measured, and nobody found out. The probe would have switched itself
        # off during exactly the event it exists to record.
        #
        # Instead: long backoff and keep trying. Reported on the third failure
        # and hourly after that - the journal stays readable, the fault stays
        # visible.
        if (( ran < 5 )); then
            if (( rc >= 2 )); then
                quick_failures=$(( quick_failures + 1 ))
                if (( quick_failures >= 3 )); then
                    if (( quick_failures == 3 || quick_failures % 60 == 0 )); then
                        log_error "ping will not start (${quick_failures}x, exit $rc) - target $ip, still trying"
                    fi
                    sleep 60
                else
                    sleep 5
                fi
            else
                sleep 30
            fi
        else
            quick_failures=0
        fi

        find "$PING_DIR" -maxdepth 1 -name "${label}-*.log" \
             -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null
    done
}

# ---------------------------------------------------------------------------
# l2_loop - plain-text capture of STP BPDUs into daily files
# ---------------------------------------------------------------------------
l2_loop() {
    local logfile deadline

    while true; do
        logfile="${L2_DIR}/l2-events-$(date +%F).log"
        deadline=$(seconds_until_midnight)

        printf '# ---- L2 capture starts %s on %s | probe %s | timestamps: %s (no offset) ----\n' \
               "$(date -Iseconds)" "$IFACE" "$NODE_NAME" "$TZ" >> "$logfile"

        timeout "$deadline" tcpdump -i "$IFACE" -n -l -tttt "$BPF_TEXT" >> "$logfile" 2>&1

        find "$L2_DIR" -maxdepth 1 -name 'l2-events-*.log' \
             -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null
    done
}

main() {
    local ip label role pid

    mkdir -p "$PING_DIR" "$L2_DIR" || {
        log_error "cannot create measurement directories under $BASE_DIR"
        exit 1
    }

    if [[ ! -r "$TARGETS_FILE" ]]; then
        log_error "targets.conf missing: $TARGETS_FILE"
        exit 1
    fi

    log_info "probe $NODE_NAME starting on $IFACE (targets from $TARGETS_FILE, TZ=$TZ)"

    # pcap ring buffer: bounded at PCAP_SIZE_MB * PCAP_FILES, overwrites itself.
    #
    # -w without -U buffers: the file can sit at 0 bytes for days and look
    # exactly like "no frames seen". The ring buffer's own rotation forces
    # flushes, which is why this is survivable here - see
    # docs/explanation/pitfalls.md before removing -C/-W.
    tcpdump -i "$IFACE" -n -s 128 -C "$PCAP_SIZE_MB" -W "$PCAP_FILES" \
            -w "${L2_DIR}/l2.pcap" "$BPF_PCAP" >/dev/null 2>&1 &
    pid=$!
    CHILD_PIDS+=("$pid")

    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        log_info "pcap ring buffer running (PID $pid)"
    else
        log_warning "pcap ring buffer exited immediately - missing CAP_NET_RAW?"
    fi

    l2_loop &
    CHILD_PIDS+=("$!")

    while read -r ip label role; do
        [[ -z "$ip" || "$ip" == \#* ]] && continue
        ping_loop "$ip" "$label" &
        CHILD_PIDS+=("$!")
        log_info "target $ip ($label, $role) started"
    done < "$TARGETS_FILE"

    log_info "${#CHILD_PIDS[@]} measurement processes active"

    # Wait for the children. NOTE: `wait` only returns when ALL children have
    # exited - systemd does NOT notice a single dead ping_loop. That is exactly
    # how one probe's client-path went unobserved for a day. The loops must
    # therefore only ever exit on genuine startup failure.
    wait
    return 0
}

main "$@"
