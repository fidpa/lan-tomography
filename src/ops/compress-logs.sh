#!/bin/bash
# ---
# deployment: systemd-service
# service: lt-compress-logs.service
# timer: lt-compress-logs.timer
# status: active
# type: oneshot
# requires_root: false
# ---
# compress-logs.sh - compress completed measurement days with zstd
#
# PURPOSE
#   A continuous measurement produces a lot of text. Ping logs at five packets
#   a second are around 100 MB per target per day; zstd takes that to roughly a
#   fifteenth. Without this, a campaign fills the disk in about a week and the
#   measurement stops - during the incident you are trying to record.
#
# THE RULE THAT MATTERS
#   Only files for days that are OVER, and only files nobody has open. A
#   compressed file that a running `ping` still holds open keeps its inode
#   alive: the disk is not freed, and the writer carries on into a file that no
#   longer has a name.
#
#   The analysis tools read .log and .log.zst transparently, so compression is
#   invisible downstream. That is not a nicety - a plain glob("*.log") silently
#   skips archived days, and the analysis then runs on less data with no error
#   and no exit code. See log_files() in src/analyze/correlate.py.
#
# THE SECOND RULE
#   An archive takes its final name only once it verifies. Until then it is
#   called <name>.zst.partial, a suffix nothing globs for. This is the only
#   routine step that deletes measurement data on purpose, so an interrupted
#   run must not be able to leave something that looks like a finished archive:
#   the next run would skip that day forever with "archive already exists", and
#   the .log beside it would eventually be tidied away as redundant. Leftover
#   .partial files are cleared at the start of each run. See pitfall B10.
#
# INVOCATION
#   compress-logs.sh             compress completed days
#   compress-logs.sh --dry-run   show what would happen, change nothing
#   compress-logs.sh --help      this text
#
# CONFIGURATION (environment or config/lan-tomography.conf)
#   LT_BASE_DIR          where measurement data lives
#   LT_ALERT_CMD         where a failure alert goes
#   LT_ALERT_COOLDOWN    seconds between repeats of the same alert

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
readonly SCRIPT_DIR

# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh" || exit 1

[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { lt_usage "$0"; exit 0; }

readonly LOG_PREFIX="[compress]"
DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true
readonly DRY_RUN

TODAY="$(date +%F)"
readonly TODAY
readonly ALERT_COOLDOWN="${LT_ALERT_COOLDOWN:-3600}"

# Subdirectories holding daily files worth compressing.
readonly -a DATA_DIRS=(ping l2 pktrate tcp)

# Name an archive carries while it is being written. Deliberately not ending in
# .zst, so that neither the skip check in compress_one() nor log_files() in
# correlate.py nor sync-node.sh's rsync filter can mistake a half-written
# archive for a finished one.
readonly PARTIAL_SUFFIX=".zst.partial"

is_open() {
    # A file still held open by a running process must not be touched.
    # lsof is not installed everywhere; /proc is, on any Linux this runs on.
    local target="$1" link
    for link in /proc/[0-9]*/fd/*; do
        [[ -e "$link" ]] || continue
        if [[ "$(readlink -f "$link" 2>/dev/null)" == "$target" ]]; then
            return 0
        fi
    done
    return 1
}

compress_one() {
    local file="$1"
    local tmp="${file}${PARTIAL_SUFFIX}"

    if [[ -f "${file}.zst" ]]; then
        log_info "$LOG_PREFIX archive already exists, skipped: $(basename "$file")"
        return 2
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "$LOG_PREFIX would compress: $(basename "$file")"
        return 2
    fi

    # Compress, VERIFY, only then remove the source. Never `zstd --rm`: that
    # deletes within the same invocation, on nothing but zstd's own report of
    # its own run. A measurement day was lost that way once - the archive
    # existed, the source was gone, and the archive did not read back. `zstd -t`
    # between the two steps is what makes the deletion safe, and it is the
    # entire point of this function.
    #
    # The archive is written under a name nothing looks for, and only takes its
    # real name once it verifies. A run killed mid-compression - a reboot, a
    # dropped ssh session, systemd's TimeoutStartSec - otherwise leaves a
    # truncated .log.zst under the final name, and the check above then skips
    # that day forever with "archive already exists". The source survives, so
    # nothing is lost yet; what is lost is the ability to tell the two apart,
    # and the next person to clear out "redundant" .log files beside their
    # archives deletes the only readable copy. See pitfall B10.
    if ! zstd -q -o "$tmp" "$file" 2>/dev/null; then
        log_error "$LOG_PREFIX compression failed: $file"
        rm -f "$tmp"
        return 1
    fi
    if ! zstd -q -t "$tmp" 2>/dev/null; then
        log_error "$LOG_PREFIX archive does not verify, source kept: $(basename "$file")"
        rm -f "$tmp"
        return 1
    fi
    mv -f "$tmp" "${file}.zst"
    rm -f "$file"
    return 0
}

main() {
    # One run at a time. The timer and a hand-started run overlapping means two
    # processes compressing the same file: the second finds the source gone
    # mid-flight, or writes over an archive the first is still producing.
    exec 200>"${LT_BASE_DIR}/.compress-logs.lock" 2>/dev/null || true
    flock -n 200 || {
        log_info "$LOG_PREFIX another run holds the lock - skipping"
        return 0
    }

    log_info "$LOG_PREFIX compressing completed measurement days"
    [[ "$DRY_RUN" == "true" ]] && log_info "$LOG_PREFIX DRY-RUN: nothing will be changed"

    if ! command -v zstd >/dev/null 2>&1; then
        log_error "$LOG_PREFIX zstd is not installed"
        return 1
    fi

    if [[ ! -d "$LT_BASE_DIR" ]]; then
        log_info "$LOG_PREFIX $LT_BASE_DIR does not exist - nothing to do"
        return 0
    fi

    # Leftovers from a run that was killed mid-compression. They never hold
    # data the source file does not also hold, because the source is removed
    # only after the archive verifies. Clearing them here, rather than leaving
    # them to accumulate, is what keeps a partial write from costing disk on
    # every reboot.
    local partial leftover=0
    while IFS= read -r partial; do
        [[ "$DRY_RUN" == "true" ]] || rm -f "$partial"
        leftover=$((leftover + 1))
    done < <(find "$LT_BASE_DIR" -type f -name "*${PARTIAL_SUFFIX}" 2>/dev/null)
    (( leftover > 0 )) && log_info "$LOG_PREFIX cleared $leftover partial archive(s) from an interrupted run"

    local before_mb after_mb
    before_mb=$(du -sm "$LT_BASE_DIR" 2>/dev/null | cut -f1)

    local done_count=0 fail_count=0 skip_count=0
    local dir file
    for dir in "${DATA_DIRS[@]}"; do
        [[ -d "${LT_BASE_DIR}/${dir}" ]] || continue
        for file in "${LT_BASE_DIR}/${dir}"/*.log; do
            [[ -f "$file" ]] || continue
            # Today's file is still being written to.
            [[ "$file" == *"$TODAY"* ]] && continue
            if is_open "$file"; then
                log_info "$LOG_PREFIX in use, skipped: $(basename "$file")"
                skip_count=$((skip_count + 1))
                continue
            fi
            compress_one "$file"
            case $? in
                0) done_count=$((done_count + 1)) ;;
                1) fail_count=$((fail_count + 1)) ;;
                2) skip_count=$((skip_count + 1)) ;;
            esac
        done
    done

    after_mb=$(du -sm "$LT_BASE_DIR" 2>/dev/null | cut -f1)
    log_success "$LOG_PREFIX done: $done_count compressed, $skip_count skipped, $fail_count failed"
    log_info "$LOG_PREFIX ${before_mb} MB -> ${after_mb} MB (freed: $((before_mb - after_mb)) MB)"

    if (( fail_count > 0 )); then
        lt_alert_once "compress_failed" "$ALERT_COOLDOWN" \
            "compression failed for $fail_count file(s)" \
            "$(printf 'Compressing completed measurement days produced %d error(s).\n\nsucceeded : %d\nskipped   : %d\ndirectory : %s (%s MB)\n\nThe affected source files were NOT deleted - no measurement data is lost,\nbut the disk is not being freed either. A full disk stops the measurement.\n\nCheck: journalctl -u lt-compress-logs.service -n 50\n       df -h %s\n' \
                     "$fail_count" "$done_count" "$skip_count" "$LT_BASE_DIR" "$after_mb" "$LT_BASE_DIR")"
        return 1
    fi

    return 0
}

main "$@"
