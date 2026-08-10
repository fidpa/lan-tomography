#!/bin/bash
# ---
# deployment: systemd-service
# service: lt-liveness-check.service
# timer: lt-liveness-check.timer
# status: active
# type: oneshot
# requires_root: false
# ---
# liveness-check.sh - verify the measurement chain is actually measuring
#
# WHY THIS EXISTS - the most important tool in the repository
#
#   A measurement that has stopped looks EXACTLY like a measurement that found
#   nothing. Both produce silence.
#
#   That is not a theoretical concern. When you are testing whether a fault has
#   stopped occurring - after an intervention, during a trial period - a dead
#   probe reads as success. You will report the good news, and you will be
#   wrong. Every quiet hour in the log is worthless unless something
#   independently confirms the chain was alive for it.
#
#   So this watcher runs on its own timer, independently of the probes, and its
#   alert says so in the subject line.
#
# WHAT IT CHECKS
#   1. The remote probe answers over SSH, and its units report active.
#      Both in one round trip: an unreachable host and a dead service are
#      distinguished by whether any output came back at all.
#      Deliberately not ping - a probe that answers ICMP while its measurement
#      processes are dead is the exact failure this tool exists to catch.
#   2. Data has actually ARRIVED here and is fresh. This is the real test: it
#      also covers the case where the probe is running fine and the sync is
#      stuck, which the first check cannot see.
#
# INVOCATION
#   liveness-check.sh            check and alert on problems
#   liveness-check.sh --report   print status, always exit 0
#   liveness-check.sh --help     this text
#
# CONFIGURATION (environment or config/lan-tomography.conf)
#   LT_REMOTE_HOST     SSH destination of the probe to check
#   LT_REMOTE_UNITS    space-separated systemd units expected active there
#   LT_BASE_DIR        where measurement data arrives
#   LT_MAX_DATA_AGE_S  how stale incoming data may be (default: 7800)
#   LT_ALERT_CMD       where the alert goes

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
readonly SCRIPT_DIR

# Set LOG_FILE BEFORE sourcing: under ProtectSystem=strict a write to an
# unlisted path fails SILENTLY, and this tool would then look healthy while
# logging nothing - which is precisely the failure mode it exists to detect.
: "${LT_LOG_FILE:=${LT_BASE_DIR:-/var/log/lan-tomography}/lan-tomography.log}"
export LT_LOG_FILE

# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh" || exit 1

[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { lt_usage "$0"; exit 0; }

readonly LOG_PREFIX="[liveness]"
readonly REMOTE_HOST="${LT_REMOTE_HOST:-}"

# Tolerance for the freshness of incoming data. The sync runs hourly, so
# anything under two hours is normal. It follows that this watcher reports an
# outage no sooner than that - tighter values only produce false alarms, and a
# watcher that cries wolf gets muted, which costs more than it saves.
readonly MAX_AGE_S="${LT_MAX_DATA_AGE_S:-7800}"

read -r -a REMOTE_UNITS <<< "${LT_REMOTE_UNITS:-lt-probe-node.service}"

main() {
    local mode="${1:-check}"
    local -a problems=()
    local newest age reachable="yes" units_out="" active_count=0

    if [[ -z "$REMOTE_HOST" ]]; then
        log_error "$LOG_PREFIX LT_REMOTE_HOST is not set - nothing to check"
        return 2
    fi

    # 1. Reachability AND services in one round trip.
    units_out=$(ssh -o ConnectTimeout=10 -o BatchMode=yes "$REMOTE_HOST" \
                    "systemctl is-active ${REMOTE_UNITS[*]}" 2>/dev/null)
    if [[ -z "$units_out" ]]; then
        reachable="NO"
        problems+=("probe $REMOTE_HOST not reachable over SSH")
    else
        local i=0
        while IFS= read -r state; do
            if [[ "$state" == "active" ]]; then
                active_count=$((active_count + 1))
            else
                problems+=("unit ${REMOTE_UNITS[$i]:-?} is '${state:-unknown}'")
            fi
            i=$((i + 1))
        done <<< "$units_out"
    fi

    # 2. Freshness of the data that actually arrived here.
    newest=$(find "${LT_BASE_DIR}" -name '*.log' -type f -printf '%T@\n' 2>/dev/null \
             | sort -rn | head -1 | cut -d. -f1)
    if [[ -z "$newest" ]]; then
        problems+=("no measurement data under ${LT_BASE_DIR}")
        age="-"
    else
        age=$(( $(date +%s) - newest ))
        (( age > MAX_AGE_S )) && \
            problems+=("data is $((age / 60)) min old (limit $((MAX_AGE_S / 60)) min)")
    fi

    if [[ "$mode" == "--report" ]]; then
        printf '=== measurement chain ===\n'
        printf '  probe %-20s reachable : %s\n' "$REMOTE_HOST" "$reachable"
        printf '  units active                  : %s of %s\n' \
               "$active_count" "${#REMOTE_UNITS[@]}"
        printf '  incoming data                 : %s\n' \
               "$([[ "$age" == "-" ]] && echo "missing" || echo "$((age / 60)) min old")"
        if (( ${#problems[@]} == 0 )); then
            printf '  verdict                       : CHAIN ALIVE\n'
        else
            printf '  verdict                       : IMPAIRED\n'
            printf '    - %s\n' "${problems[@]}"
        fi
        return 0
    fi

    if (( ${#problems[@]} == 0 )); then
        log_success "$LOG_PREFIX chain alive (data $((age / 60)) min old)"
        return 0
    fi

    log_error "$LOG_PREFIX chain impaired: ${problems[*]}"
    lt_alert \
        "measurement chain impaired - an absence of events is NOT a finding" \
        "$(printf 'The measurement chain is not fully reporting.\n\n%s\n\nWhy this matters right now: while the chain is impaired, quiet logs prove\nnothing. If you are testing whether a fault has stopped recurring, a dead\nprobe looks exactly like success. Mark this period as UNOBSERVED in your\nnotes rather than as clean.\n' \
                  "$(printf '  - %s\n' "${problems[@]}")")"
    return 1
}

main "$@"
