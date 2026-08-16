#!/bin/bash
# ---
# deployment: systemd-service
# service: lt-sync-node@.service
# timer: lt-sync-node@.timer
# status: active
# type: oneshot
# requires_root: false
# ---
# sync-node.sh - pull measurement data from a probe node
#
# PURPOSE
#   Copies a probe's text logs to the machine that does the analysis. Text only:
#   pcap ring buffers stay where they are (up to 500 MB each) and get fetched
#   deliberately when a specific event needs them.
#
#   The probe's target matrix comes along, to $LT_BASE_DIR/<node>/targets.conf.
#   Data and matrix are collected in different places and mean nothing apart:
#   a table read against the wrong matrix is missing rows and says so nowhere.
#
# INVOCATION
#   sync-node.sh <node>          pull from that node
#   sync-node.sh <node> --dry-run
#   sync-node.sh --help
#
#   <node> is an SSH destination. Data lands in $LT_BASE_DIR/<node>/.
#
# CONFIGURATION (environment or config/lan-tomography.conf)
#   LT_BASE_DIR       where the collected data goes
#   LT_REMOTE_BASE    the probe's own base directory (default: same as local)
#   LT_SYNC_DIRS      which subdirectories to pull
#                     (default: "ping l2 pktrate tcp")
#
# WHY NOT --delete
#   Because a day compressed on the probe would come back uncompressed and sit
#   next to its own archive - and both would be read. The analysis survives that
#   (log_files() prefers the uncompressed copy), but only because it was hit
#   once. Adding --delete would trade this handled case for an unhandled one:
#   evidence disappearing here when it rotates there.
#
# EXIT CODES
#   0   pulled, or nothing to pull. This includes rsync's own exit 24, "a source
#       file vanished while transferring": the probe compresses yesterday's log
#       on its own timer, so a pull that overlaps watches a .log become a
#       .log.zst mid-flight. Everything else transferred and the archive arrives
#       on the next pass. Failing the run for that would mean reporting an error
#       on a schedule, which is how people learn to stop reading the output.
#   75  node unreachable - transient, the next run catches up. The unit carries
#       SuccessExitStatus=75, so a probe that is briefly down does not leave a
#       permanently failed unit that people learn to ignore.
#   1   a transfer failed. The rsync exit code is named in the log line.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
readonly SCRIPT_DIR

# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh" || exit 1

[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { lt_usage "$0"; exit 0; }

readonly NODE="${1:-}"
DRY_RUN=false
[[ "${2:-}" == "--dry-run" ]] && DRY_RUN=true
readonly DRY_RUN

readonly LOG_PREFIX="[sync]"
readonly REMOTE_BASE="${LT_REMOTE_BASE:-$LT_BASE_DIR}"
readonly LOCAL_BASE="${LT_BASE_DIR}/${NODE}"

read -r -a SYNC_DIRS <<< "${LT_SYNC_DIRS:-ping l2 pktrate tcp}"

main() {
    if [[ -z "$NODE" ]]; then
        log_error "$LOG_PREFIX no node given (usage: $0 <ssh-destination>)"
        exit 2
    fi

    if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$NODE" true 2>/dev/null; then
        # Transient. The data stays on the probe; the next run collects it.
        log_warning "$LOG_PREFIX $NODE not reachable - pass skipped"
        exit 75
    fi

    local rc=0 dir args=()
    [[ "$DRY_RUN" == "true" ]] && args+=(--dry-run)

    for dir in "${SYNC_DIRS[@]}"; do
        # A probe that does not run every tool simply has no such directory.
        # That is not an error, and treating it as one would make every unit
        # on a minimal probe report failure.
        if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$NODE" \
                 "test -d '${REMOTE_BASE}/${dir}'" 2>/dev/null; then
            log_info "$LOG_PREFIX $NODE has no ${dir}/ - skipped"
            continue
        fi

        mkdir -p "${LOCAL_BASE}/${dir}" || {
            log_error "$LOG_PREFIX cannot create ${LOCAL_BASE}/${dir}"
            exit 1
        }

        # Text logs only, compressed archives included. No --delete: see header.
        local rsync_rc=0
        rsync -az --timeout=60 "${args[@]}" \
              --include='*/' --include='*.log' --include='*.log.zst' --exclude='*' \
              "${NODE}:${REMOTE_BASE}/${dir}/" "${LOCAL_BASE}/${dir}/" 2>/dev/null \
              || rsync_rc=$?

        # 24 is "a source file vanished while transferring", and this project
        # produces it in normal operation: the probe runs lt-compress-logs on
        # its own timer, so a pull that overlaps it watches yesterday's .log
        # turn into a .log.zst mid-flight. Everything else transferred, and the
        # archive arrives on the next pass. Treating it as a failure means the
        # unit reports an error on a schedule, which is how people learn to
        # stop reading its output.
        if (( rsync_rc == 24 )); then
            log_info "$LOG_PREFIX ${dir}/ pulled from $NODE (a file was compressed mid-transfer)"
        elif (( rsync_rc == 0 )); then
            log_info "$LOG_PREFIX ${dir}/ pulled from $NODE"
        else
            log_error "$LOG_PREFIX rsync of ${dir}/ from $NODE failed (exit ${rsync_rc})"
            rc=1
        fi
    done

    # The probe's own target matrix, beside the data it describes.
    #
    # Without it, correlate.py has no way to know what this probe's labels mean
    # and refuses to analyse the directory rather than silently apply the local
    # matrix - which would drop every target only this probe measures, with no
    # message and no exit code. probe-node.sh writes it to the probe's base
    # directory for exactly this pull.
    if rsync -az --timeout=60 "${args[@]}" \
             "${NODE}:${REMOTE_BASE}/targets.conf" "${LOCAL_BASE}/targets.conf" \
             2>/dev/null; then
        log_info "$LOG_PREFIX target matrix pulled from $NODE"
    else
        # Not fatal: a probe may be running an older version, or a hand-built
        # one. Say what the consequence is, because it surfaces later and
        # somewhere else.
        log_warning "$LOG_PREFIX $NODE has no ${REMOTE_BASE}/targets.conf - analysis of ${LOCAL_BASE} will need --targets"
    fi

    if (( rc == 0 )); then
        log_success "$LOG_PREFIX $NODE synced (${SYNC_DIRS[*]})"
    fi
    return "$rc"
}

main "$@"
