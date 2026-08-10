#!/bin/bash
# ---
# deployment: systemd-service
# service: lt-event-watch.service
# status: active
# type: daemon
# requires_root: false
# ---
# event-watch.sh - report measurement events while they are happening
#
# PURPOSE
#   Watches the incoming measurement data and reports floods, surges and target
#   outages as they occur, rather than when somebody next runs an analysis.
#
#   The value is not the alert. It is being able to look at a fault WHILE it is
#   happening - the difference between "we saw a gap yesterday" and "it is
#   happening now, go and look at the switch".
#
# INVOCATION
#   event-watch.sh            run continuously
#   event-watch.sh --once     evaluate the current state once and exit
#   event-watch.sh --help     this text
#
# CONFIGURATION (environment or config/lan-tomography.conf)
#   LT_BASE_DIR          where measurement data is
#   LT_FLOOD_MIN         frames per interval counting as a flood  (10000)
#   LT_SURGE_MIN         frames per interval counting as a surge  (1500)
#   LT_PKTRATE_INTERVAL  sampling interval of the pktrate log     (5)
#   LT_WATCH_WINDOW_S    how far back each pass looks             (300)
#   LT_WATCH_SLEEP_S     seconds between passes                   (60)
#   LT_ALERT_CMD         where alerts go
#
# THE THRESHOLDS ARE NOT DEFAULTS, THEY ARE SOMEBODY ELSE'S MEASUREMENTS
#   LT_FLOOD_MIN and LT_SURGE_MIN come from one campaign on one network, where
#   quiet periods sat at a median of about 100 broadcast frames per 5 s. Ported
#   unchanged to a busier network they report continuously; to a quieter one
#   they report nothing.
#
#   Derive them: run the measurement for a day without thresholds, take the
#   median and the maximum of the quiet periods, and set the surge threshold
#   about an order of magnitude above the median. docs/explanation/pitfalls.md
#   has the longer version.
#
# WHAT A QUIET WATCHER DOES NOT MEAN
#   That the network is quiet. It could equally mean the chain is dead. This
#   tool deliberately does NOT claim otherwise - lt-liveness-check answers that
#   question, on its own timer, and it is not optional.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
readonly SCRIPT_DIR

# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh" || exit 1

[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { lt_usage "$0"; exit 0; }

readonly LOG_PREFIX="[watch]"
readonly SCANNER="${SCRIPT_DIR}/../analyze/pktrate-scan.py"
readonly PYTHON="${LT_PYTHON:-/usr/bin/python3}"
readonly WINDOW_S="${LT_WATCH_WINDOW_S:-300}"
readonly SLEEP_S="${LT_WATCH_SLEEP_S:-60}"
readonly ALERT_COOLDOWN="${LT_ALERT_COOLDOWN:-3600}"

# Events already reported, so a flood lasting an hour is announced once rather
# than sixty times. Keyed by level plus start time.
declare -A SEEN=()

scan_pktrate() {
    local today
    today="$(date +%F)"
    local -a files=()
    local f
    for f in "${LT_PKTRATE_DIR}"/*"${today}".log; do
        [[ -f "$f" ]] && files+=("$f")
    done
    (( ${#files[@]} == 0 )) && return 0

    "$PYTHON" "$SCANNER" "${files[@]}" --json 2>/dev/null
}

report_events() {
    local json="$1"
    [[ -z "$json" ]] && return 0

    local now cutoff
    now=$(date +%s)
    cutoff=$(( now - WINDOW_S ))

    # Read the events the scanner found. jq is not assumed to be installed;
    # python is already a dependency of the scanner itself.
    local line
    while IFS='|' read -r level start dur peak_b peak_m; do
        [[ -z "$level" ]] && continue
        # Only events touching the current window.
        (( ${start%.*} < cutoff )) && continue

        local key="${level}-${start}"
        [[ -n "${SEEN[$key]:-}" ]] && continue
        SEEN["$key"]=1

        line="$level lasting ${dur}s, peak broadcast ${peak_b}/interval, multicast ${peak_m}/interval"
        if [[ "$level" == "flood" ]]; then
            log_error "$LOG_PREFIX $line"
            lt_alert_once "flood" "$ALERT_COOLDOWN" \
                "broadcast flood in progress" \
                "$(printf '%s\n\nA flood is happening NOW. If you can look at the switch, do it now:\nport counters, forwarding database, which port the traffic enters on.\n\n  %s --report\n\nAn hour from now this is a log entry. Right now it is evidence.\n' \
                          "$line" "${SCRIPT_DIR}/../switch/fdb-probe.py")"
        else
            log_warning "$LOG_PREFIX $line"
        fi
    done < <(printf '%s' "$json" | "$PYTHON" -c '
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
for e in data.get("events", []):
    print("|".join(str(x) for x in (
        e["level"], e["start"], e["duration_s"], e["peak_bcast"], e["peak_mcast"])))
' 2>/dev/null)

    # Column misalignment invalidates everything above it, so it is reported
    # separately and loudly rather than folded into the event list.
    local misaligned
    misaligned=$(printf '%s' "$json" | "$PYTHON" -c '
import json, sys
try: print(json.load(sys.stdin).get("misaligned", 0))
except ValueError: print(0)
' 2>/dev/null)
    if [[ "${misaligned:-0}" -gt 0 ]]; then
        log_error "$LOG_PREFIX $misaligned sample(s) fail uni+bcast+mcast==rx - the packet-rate numbers cannot be trusted"
    fi
}

one_pass() {
    local json
    json="$(scan_pktrate)"
    if [[ -z "$json" ]]; then
        log_info "$LOG_PREFIX no packet-rate data for today yet"
        return 0
    fi
    report_events "$json"
    return 0
}

main() {
    if [[ ! -r "$SCANNER" ]]; then
        log_error "$LOG_PREFIX scanner not found: $SCANNER"
        exit 1
    fi

    log_info "$LOG_PREFIX watching ${LT_PKTRATE_DIR} (flood >= ${LT_FLOOD_MIN:-10000}, surge >= ${LT_SURGE_MIN:-1500} per interval)"

    if [[ "${1:-}" == "--once" ]]; then
        one_pass
        return 0
    fi

    while true; do
        one_pass
        sleep "$SLEEP_S"
    done
}

main "$@"
