#!/bin/bash
# ---
# deployment: systemd-service
# service: lt-ping@.service
# status: active
# type: daemon
# requires_root: false
# ---
# ping-target.sh - continuous ICMP measurement of one target
#
# PURPOSE
#   Measures reachability and latency of a single target on a fixed interval
#   and records EVERY missing reply with an absolute timestamp. One instance
#   per target; the fault is located by comparing several of them.
#
#   The missing replies are the measurement. A tool that only logs successful
#   pings tells you nothing about the moment you care about.
#
# INVOCATION
#   ping-target.sh <IP>          usually via lt-ping@<IP>.service
#   ping-target.sh --help        this text
#
# CONFIGURATION (environment or config/lan-tomography.conf)
#   LT_BASE_DIR        where measurement data goes
#   LT_PING_INTERVAL   seconds between echo requests   (default: 0.2)
#   LT_RETENTION_DAYS  how long to keep daily files    (default: 21)
#   LT_TARGETS         target matrix, for label lookup
#
# ONE INSTANCE PER TARGET, ON PURPOSE
#   A single process pinging ten targets loses all ten when it dies. As a
#   systemd template unit, each target is its own service with its own restart
#   policy, and a dead one is visible in `systemctl --failed` instead of being
#   a gap somebody notices next week.
#
#   This is also why this script may exit on a persistent startup failure while
#   src/node/probe-node.sh never does - see the note at that branch below. The
#   difference is deliberate.

set -uo pipefail  # NO -e: ping exits 1 on packet loss, which is the normal case

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
readonly SCRIPT_DIR

# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh" || exit 1

# Help BEFORE main: the text comes from the header comment above, so there is
# no second text to fall out of date with it.
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { lt_usage "$0"; exit 0; }

readonly LOG_PREFIX="[ping]"
readonly TARGETS_FILE="${LT_TARGETS:-${LT_REPO_ROOT}/config/targets.conf}"

# 5 packets/s. Intervals below 0.2 require root; 0.2 is the finest resolution
# allowed unprivileged, and enough for sub-second dropouts.
readonly PING_INTERVAL="${LT_PING_INTERVAL:-0.2}"
readonly RETENTION_DAYS="${LT_RETENTION_DAYS:-21}"

resolve_label() {
    local ip="$1" label
    label=$(awk -v ip="$ip" '$1 == ip { print $2; exit }' "$TARGETS_FILE" 2>/dev/null)
    printf '%s' "${label:-unknown}"
}

seconds_until_midnight() {
    local now tomorrow
    now=$(date +%s)
    tomorrow=$(date -d 'tomorrow 00:00:00' +%s)
    printf '%s' "$(( tomorrow - now ))"
}

cleanup_old_logs() {
    local label="$1"
    find "$LT_PING_DIR" -maxdepth 1 -name "${label}-*.log" \
         -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null
}

main() {
    local ip="${1:-}" label logfile deadline

    if [[ -z "$ip" ]]; then
        log_error "$LOG_PREFIX no target given (usage: $0 <IP>)"
        exit 1
    fi

    label=$(resolve_label "$ip")
    lt_ensure_dirs || exit 1

    log_info "$LOG_PREFIX starting continuous measurement of $ip ($label), interval ${PING_INTERVAL}s"

    # Endless loop with daily files: ping is stopped precisely at midnight via
    # -w and continued into a new file.
    #
    # Why not logrotate: with copytruncate the running process keeps its write
    # offset. After the truncate, ping would carry on writing at the old
    # position and the file would fill up with null bytes at the front.
    local started ran quick_failures=0
    while true; do
        logfile="${LT_PING_DIR}/${label}-$(date +%F).log"
        deadline=$(seconds_until_midnight)

        printf '# ---- measurement starts %s | target %s (%s) | interval %ss ----\n' \
               "$(date -Iseconds)" "$ip" "$label" "$PING_INTERVAL" >> "$logfile"

        started=$(date +%s)

        # -D absolute Unix timestamps, -O reports MISSING replies (exactly the
        # signal this is about), -n no DNS resolution.
        ping -D -O -n -i "$PING_INTERVAL" -w "$deadline" "$ip" >> "$logfile" 2>&1
        local rc=$?

        ran=$(( $(date +%s) - started ))

        # A regular run only ends at midnight. If ping returns within seconds
        # there are two cases, and the exit code separates them:
        #
        #   >=2  startup failure, e.g. missing CAP_NET_RAW. Without a brake the
        #        loop would run thousands of times a minute and bury its own
        #        error message.
        #   <=1  target is not answering - a workstation switched off after
        #        hours. ping gives up by itself after a run of unreachables.
        #        Not an error: keep retrying, throttled, until it comes back.
        #        The correlator reports that phase as "offline" rather than as
        #        an outage, which matters - the first version of this script
        #        exited on it and produced 79 restarts in one evening against
        #        Restart=always.
        #
        # NOTE on exiting after three failures: correct HERE, wrong in
        # src/node/probe-node.sh. Here systemd owns the restart and a failed
        # unit is visible. There, all loops share one process, so an exiting
        # loop is invisible - the service stays "active (running)" on the
        # strength of its capture while measuring nothing.
        if (( ran < 5 )); then
            if (( rc >= 2 )); then
                quick_failures=$(( quick_failures + 1 ))
                if (( quick_failures >= 3 )); then
                    log_error "$LOG_PREFIX ping aborts immediately (3x under 5s, exit $rc) - target $ip." \
                              "Last output: $(tail -3 "$logfile" | tr '\n' ' ')"
                    exit 1
                fi
                sleep 5
            else
                sleep 30
            fi
        else
            quick_failures=0
        fi

        cleanup_old_logs "$label"
    done
}

main "$@"
