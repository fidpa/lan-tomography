#!/bin/bash
# ---
# deployment: systemd-service
# service: lt-l2-sniffer.service
# status: active
# type: daemon
# requires_root: false
# ---
# l2-sniffer.sh - passive layer-2 observation
#
# PURPOSE
#   Records spanning-tree and ARP traffic. A flapping switch port or a topology
#   change produces STP Topology Change Notifications - the direct fingerprint
#   of a fault in the switching fabric rather than in an endpoint.
#
#   Completely passive: not a single packet is sent.
#
# INVOCATION
#   l2-sniffer.sh          run in the foreground (systemd Type=simple)
#   l2-sniffer.sh --help   this text
#
# CONFIGURATION (environment or config/lan-tomography.conf)
#   LT_IFACE           interface to capture on
#   LT_BASE_DIR        where measurement data goes
#   LT_RETENTION_DAYS  how long to keep daily text logs   (default: 21)
#   LT_TZ              timezone for timestamps            (default: UTC)
#
# KNOW WHAT YOU CAN SEE FROM WHERE
#   This captures the topology events of the switch THIS HOST is plugged into,
#   and only those. STP BPDUs do not propagate past a bridge as data; a
#   topology change two switches away may never reach you.
#
#   Establish where you actually sit before drawing conclusions: an LLDP
#   capture names the neighbouring switch and port. During the campaign this
#   came from, the machine everybody assumed was on the central switch turned
#   out to be behind a small one that declared itself STP root - which made an
#   entire class of "we saw no topology change" statements meaningless until
#   the assumption was checked.
#
# WHAT THIS CAPTURE CANNOT SHOW YOU
#   The filters below are narrow on purpose - a wide filter on a busy LAN fills
#   a disk and collects user payloads. The cost is that broadcast floods,
#   multicast storms and unknown-unicast flooding are INVISIBLE here.
#
#   That is not hypothetical. In the campaign this came from, the decisive
#   event was a broadcast flood, and nobody was looking at it, because the only
#   L2 capture running filtered on 'stp'. Use src/probes/pktrate.sh for that -
#   it counts what this cannot see.

set -uo pipefail  # NO -e

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
readonly SCRIPT_DIR

# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh" || exit 1

# Help BEFORE main: the text comes from the header comment above.
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { lt_usage "$0"; exit 0; }

readonly LOG_PREFIX="[l2]"
readonly IFACE="${LT_IFACE:-eth0}"

# `tcpdump -tttt` writes local time WITHOUT an offset ("2026-07-27
# 21:11:51.137960"). Fix the zone explicitly rather than inheriting it, because
# systemd units get no TZ and would silently fall back to /etc/localtime -
# producing lines that claim a zone the reader has no way to determine.
export TZ="${LT_TZ:-UTC}"

# The pcap ring gets both: STP carries the topology-change flags, ARP shows
# storms, gratuitous ARP and duplicate addresses.
readonly BPF_FILTER_PCAP='stp or arp'

# The plain-text log gets STP ONLY. ARP alone is around 700 lines a minute on
# an ordinary LAN (~100 MB/day) and is close to unreadable as text. ARP stays
# in the pcap ring for deeper analysis.
readonly BPF_FILTER_TEXT='stp'

readonly PCAP_SIZE_MB="${LT_PCAP_SIZE_MB:-50}"
readonly PCAP_FILES="${LT_PCAP_FILES:-10}"
readonly RETENTION_DAYS="${LT_RETENTION_DAYS:-21}"

start_pcap_ring() {
    # -C/-W form a ring buffer: at most PCAP_SIZE_MB * PCAP_FILES, after which
    # tcpdump overwrites the oldest file. The disk cannot fill up.
    #
    # The rotation also forces periodic flushes. Without -C/-W you want -U:
    # plain `-w` buffers, so the file can sit at 0 bytes for days and look
    # exactly like "no frames matched the filter".
    tcpdump -i "$IFACE" -n -s 128 \
            -C "$PCAP_SIZE_MB" -W "$PCAP_FILES" \
            -w "${LT_L2_DIR}/l2.pcap" \
            "$BPF_FILTER_PCAP" >/dev/null 2>&1 &
    printf '%s' "$!"
}

seconds_until_midnight() {
    printf '%s' "$(( $(date -d 'tomorrow 00:00:00' +%s) - $(date +%s) ))"
}

main() {
    local pcap_pid logfile deadline

    lt_ensure_dirs || exit 1

    if ! command -v tcpdump >/dev/null 2>&1; then
        log_error "$LOG_PREFIX tcpdump is not installed"
        exit 1
    fi

    log_info "$LOG_PREFIX starting passive L2 capture on $IFACE (pcap: $BPF_FILTER_PCAP, text: $BPF_FILTER_TEXT)"

    pcap_pid=$(start_pcap_ring)
    add_exit_trap "kill $pcap_pid 2>/dev/null"
    log_info "$LOG_PREFIX pcap ring buffer running (PID $pcap_pid, max $((PCAP_SIZE_MB * PCAP_FILES)) MB)"

    # Second run as plain text, for quick correlation against the ping logs
    # without having to open a pcap. Daily rollover via timeout rather than
    # logrotate - see ping-target.sh on why copytruncate is wrong here.
    while true; do
        logfile="${LT_L2_DIR}/l2-events-$(date +%F).log"
        deadline=$(seconds_until_midnight)

        printf '# ---- L2 capture starts %s on %s | line timestamps: %s (no offset shown) ----\n' \
               "$(date -Iseconds)" "$IFACE" "$TZ" >> "$logfile"

        # -tttt readable absolute timestamp, -l line-buffered. Without -l the
        # lines only reach the file after 4 KB has accumulated - fatal when you
        # are looking into it during an incident in progress.
        timeout "$deadline" tcpdump -i "$IFACE" -n -l -tttt \
                "$BPF_FILTER_TEXT" >> "$logfile" 2>&1

        find "$LT_L2_DIR" -maxdepth 1 -name 'l2-events-*.log' \
             -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null
    done
}

main "$@"
