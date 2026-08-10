#!/bin/bash
# ---
# deployment: systemd-service
# service: lt-pktrate.service
# status: active
# type: daemon
# requires_root: false
# ---
# pktrate.sh - record packet rates split into unicast / broadcast / multicast
#
# PURPOSE
#   Samples the interface counters at a fixed interval and writes the DELTA per
#   interval, split by frame type. This is the measurement that sees a flood.
#
#   Why it is needed at all, stated plainly, because it is the least obvious
#   tool here:
#
#     A switch that FLOODS is not discarding anything. It forwards correctly,
#     just to every port. Its own counters stay clean while the end devices'
#     NICs fill up.
#
#   That single fact explains an otherwise contradictory pair of observations -
#   packet loss at every probe AND a switch reporting no errors. It is only
#   visible at the end device, which is why this runs on the probes.
#
#   An L2 capture will not show it either, if the capture filters on 'stp' or
#   'stp or arp' the way the ones in this repository do. Multicast, unknown
#   unicast and other broadcast traffic are invisible in those. During the
#   campaign this came from, that meant nobody was looking at the decisive
#   event when it happened.
#
# INVOCATION
#   pktrate.sh [<interface>]     usually via lt-pktrate.service
#   pktrate.sh --help            this text
#
# CONFIGURATION (environment or config/lan-tomography.conf)
#   LT_IFACE             interface to sample
#   LT_BASE_DIR          where measurement data goes
#   LT_PKTRATE_INTERVAL  sampling interval in seconds   (default: 5)
#   LT_RETENTION_DAYS    how long to keep daily files   (default: 21)
#   LT_TZ                timezone for timestamps        (default: UTC)
#
# OUTPUT FORMAT - read docs/reference/log-formats.md before analysing this
#
#   [1786201899.326] 4211 106 88 4405 3902 0 0 0
#    |                uni  bc  mc  rx   tx  drop err missed
#    |
#    +-- Unix epoch with milliseconds, IN SQUARE BRACKETS, and it is the END
#        of the interval, not its start.
#
#   Both of those have caused real errors, so they are stated twice:
#
#   1. The brackets. awk silently evaluates "[1786201899.326]" as 0 in
#      arithmetic context rather than raising an error. A whole measurement day
#      once collapsed into a single hour that way, and the resulting table
#      looked entirely plausible. Always strip them first:
#          gsub(/[][]/, "", $1)
#      Cross-check: fewer than 24 hourly buckets on a full day means brackets.
#
#   2. The timestamp is the END of the interval. Treating it as the start
#      shifts every sample by one interval, which was enough to turn a
#      duplication factor of 53.5 into 1.00 - that is, to turn "there is a
#      loop" into "there is no loop".
#
#   Cross-check for column alignment, one line, worth running every time:
#       uni + bcast + mcast == rx
#   Getting $3/$4 and p[2]/p[3] mixed up is an off-by-one that produces
#   confident, wrong numbers with nothing visibly amiss.
#
# STANDALONE
#   Deliberately sources no library: it only reads kernel counters and must run
#   on a bare probe host. The help logic below is therefore a copy of
#   lt_usage() rather than a source for one function.

set -uo pipefail

# Timezone, explicitly: systemd units inherit no TZ, and correlating this
# series with the others is worthless if the probes disagree.
export TZ="${LT_TZ:-UTC}"

IFACE="${1:-${LT_IFACE:-eth0}}"
readonly IFACE
readonly LOG_DIR="${LT_BASE_DIR:-/var/log/lan-tomography}/pktrate"

# 5 s grid: fine enough to see a 35-second event as seven points, coarse enough
# to stay at ~17,280 lines a day (about 1.5 MB).
readonly INTERVAL="${LT_PKTRATE_INTERVAL:-5}"
readonly RETENTION_DAYS="${LT_RETENTION_DAYS:-21}"

# ---------------------------------------------------------------------------
# Reading the counters
# ---------------------------------------------------------------------------
# Only `ethtool -S` separates broadcast from multicast; /sys/class/net/*/
# statistics/ has no broadcast counter at all. Both sources are read, because
# rx_dropped and rx_missed_errors exist only in /sys - and those are the fields
# that show a receive buffer overflowing.
#
# CAREFUL, THE COUNTER NAMES ARE DRIVER-SPECIFIC. Found the hard way during
# rollout, on the second machine:
#   Realtek RTL8111H:  unicast:     broadcast:     multicast:
#   Intel:             rx_broadcast:  rx_multicast:   and NO unicast at all
#
# A parser that knows only one scheme writes zeros on the other machine
# silently - a measurement outage that looks exactly like "no flood". That is
# the worst failure mode this repository has: not an error, but a confident
# negative result.
#
# So: accept both spellings, and derive unicast from the difference if the
# driver does not report it.
read_counters() {
    local uni bcast mcast
    eval "$(ethtool -S "$IFACE" 2>/dev/null | awk -F: '
        /^[[:space:]]*(rx_)?unicast:/   { gsub(/ /,"",$2); print "uni="   $2 }
        /^[[:space:]]*(rx_)?broadcast:/ { gsub(/ /,"",$2); print "bcast=" $2 }
        /^[[:space:]]*(rx_)?multicast:/ { gsub(/ /,"",$2); print "mcast=" $2 }')"
    bcast="${bcast:-0}"; mcast="${mcast:-0}"

    local s="/sys/class/net/${IFACE}/statistics"
    local rx tx
    rx="$(cat "$s/rx_packets" 2>/dev/null || echo 0)"
    tx="$(cat "$s/tx_packets" 2>/dev/null || echo 0)"

    # No unicast counter in the driver -> derive it. That is the definition
    # (every frame is exactly one of the three) and therefore exact, as long as
    # all three values come from the same source.
    if [[ -z "${uni:-}" ]]; then
        uni=$(( rx - bcast - mcast ))
        (( uni < 0 )) && uni=0
    fi

    printf '%s %s %s %s %s %s %s %s\n' \
        "$uni" "$bcast" "$mcast" "$rx" "$tx" \
        "$(cat "$s/rx_dropped" 2>/dev/null || echo 0)" \
        "$(cat "$s/rx_errors"  2>/dev/null || echo 0)" \
        "$(cat "$s/rx_missed_errors" 2>/dev/null || echo 0)"
}

cleanup_old_logs() {
    find "$LOG_DIR" -maxdepth 1 -name '*.log' -type f \
         -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null || true
}

main() {
    if ! [[ -d "/sys/class/net/${IFACE}" ]]; then
        echo "interface ${IFACE} does not exist" >&2
        exit 2
    fi
    if ! command -v ethtool >/dev/null 2>&1; then
        # Without ethtool there is no broadcast/multicast split, and the split
        # is the entire point. Fail loudly rather than log zeros.
        echo "ethtool not found - broadcast and multicast cannot be separated" >&2
        exit 2
    fi
    mkdir -p "$LOG_DIR" || exit 1

    local node prev cur day file
    node="${LT_NODE_NAME:-$(hostname -s)}"
    prev="$(read_counters)"
    local last_cleanup=0

    while true; do
        sleep "$INTERVAL"
        cur="$(read_counters)"
        day="$(date '+%Y-%m-%d')"
        file="${LOG_DIR}/${node}-${day}.log"

        if [[ ! -f "$file" ]]; then
            {
                printf '# lan-tomography packet rates (%s, %s), interval %ss\n' "$node" "$IFACE" "$INTERVAL"
                printf '# Timestamp = Unix epoch, in brackets, at the END of the interval.\n'
                printf '# Values are DELTAS within the interval, not rates.\n'
                printf '# Fields: uni bcast mcast rx tx drop err missed\n'
                printf '# Cross-check: uni + bcast + mcast == rx\n'
            } >> "$file"
        fi

        # Field-wise delta. A counter reset gives a negative value, which is
        # written as 0 so no phantom spike appears. The counters are 64-bit, so
        # overflow is practically impossible - a reset on link-down is not.
        local out=""
        local -a a b
        read -r -a a <<< "$prev"
        read -r -a b <<< "$cur"
        local i d
        for i in "${!b[@]}"; do
            d=$(( b[i] - a[i] ))
            (( d < 0 )) && d=0
            out+=" $d"
        done

        printf '[%s.%s]%s\n' "$(date +%s)" "$(date +%3N)" "$out" >> "$file"
        prev="$cur"

        # Clean up once a day, not on every pass.
        if (( $(date +%s) - last_cleanup > 86400 )); then
            cleanup_old_logs
            last_cleanup=$(date +%s)
        fi
    done
}

# Help BEFORE main. Same awk logic as lt_usage(); see STANDALONE above.
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    awk 'NR == 1 && /^#!/ { next }
         /^# ---$/ { fm = !fm; next }
         fm        { next }
         /^#/      { found = 1; sub(/^# ?/, ""); print; next }
         found     { exit }' "$0"

    # Standalone: reads VERSION directly rather than sourcing the library.
    v="unknown"
    for d in "$(dirname "$(readlink -f "$0")")/../.." "$(dirname "$(readlink -f "$0")")"; do
        [[ -r "$d/VERSION" ]] && { v="$(<"$d/VERSION")"; break; }
    done
    printf '\nlan-tomography %s\n' "${v//[$'\t\r\n ']/}"
    exit 0
fi

main "$@"
