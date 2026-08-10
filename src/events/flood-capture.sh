#!/bin/bash
# ---
# deployment: systemd-timer
# service: lt-flood-capture.service (via lt-flood-capture.timer)
# status: active
# type: timer
# requires_root: false
# ---
# flood-capture.sh - set aside the ring capture when something happens
#
# PURPOSE
#   The broadcast profile of frame-capture.sh runs in a ring buffer. At the
#   load measured in the campaign this came from it held about twelve hours,
#   after which tcpdump overwrote its own oldest file. Any event nobody noticed
#   within half a day was gone for good.
#
#   That is not a hypothetical risk. Three of the decisive captures in that
#   campaign survive only because somebody looked by hand and copied them out
#   in time. Everything derived from them - the duplication proof, the
#   precursor, the round-trip timing - would otherwise not exist. This turns
#   that habit into a timer.
#
#   Passive and read-only towards the running capture: files are copied out,
#   never written back.
#
# INVOCATION
#   flood-capture.sh          one pass; run it from a timer, every 2 minutes
#   flood-capture.sh --help
#
# THE TWO TRIGGERS
#   1. RATE. Any interval in the lookback window whose broadcast OR multicast
#      count exceeds LT_KEEP_THRESHOLD.
#
#      Both fields, not just broadcast: an event that was multicast-dominated
#      (30,672 against 5,267) slipped through every broadcast-only test in the
#      campaign this came from, and it carried a 309-second gateway outage.
#      See pitfall C4.
#
#      The threshold sits well below the flood threshold used for alerting -
#      about nine times the quiet median rather than ninety. That is
#      deliberate: bursts of one or two seconds stay under a per-minute flood
#      definition, and two of those were proven loop events.
#
#   2. PRECURSOR. A frame appeared in the precursor capture that is newer than
#      the last one processed.
#
#      This trigger is what keeps the measurement alive after an intervention.
#      Once the loop was isolated in that campaign, the floods stopped and the
#      rate trigger went quiet - but the precursor frames continued, and only
#      those events could still answer whether the circuit was forming at all.
#      A test that only fires while the fault is present cannot observe the
#      fix.
#
# WHY TWO RING FILES, ALWAYS
#   The current file and the one before it. In the campaign this came from the
#   ring rotated at 20:02:36, in the middle of an event that began at 20:02:32.
#   Keeping only the current file loses the run-up - which is precisely where
#   the precursor sits.
#
# CONFIGURATION (environment or config/lan-tomography.conf)
#   LT_BASE_DIR             where measurement data goes
#   LT_NODE_NAME            which pktrate log to read
#   LT_KEEP_THRESHOLD       frames per interval that trigger a save (default: 1000)
#   LT_KEEP_LOOKBACK_S      how far back to look each pass       (default: 900)
#   LT_KEEP_RETENTION_DAYS  delete saved captures after this     (default: 14)
#   LT_PRECURSOR_NAME       capture profile acting as precursor  (default: roaming)
#   LT_PRECURSOR_FILTER     BPF filter for reading it            (default: ether proto 0x890d)
#   LT_PRECURSOR_GROUP_S    collapse a burst of precursors into one (default: 300)
#
# TWO WAYS THIS FILLED A DISK, BOTH FIXED HERE
#   Grouping alone is not enough. LT_PRECURSOR_GROUP_S was calibrated over a
#   weekend, when precursors arrived every few hours. On the next working day
#   they arrived every few minutes: 37 saves in ten hours, 2.6 GB. The
#   size-and-mtime check below is what actually holds, because a ring file that
#   has filled up no longer changes and every further copy of it is waste.
#
#   Size alone does not identify a file either: after a rotation the file
#   starts small and can coincidentally match the size of an earlier save.
#   Size AND modification time together.
#
#   The check only ever skips a copy. It never deletes anything.

set -uo pipefail  # NO -e

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
readonly SCRIPT_DIR

# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh" || exit 1

[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { lt_usage "$0"; exit 0; }

readonly LOG_PREFIX="[keep]"

readonly RING_DIR="${LT_CAPTURE_DIR}/broadcast"
readonly KEEP_DIR="${LT_CAPTURE_DIR}/keep"
readonly PRECURSOR_NAME="${LT_PRECURSOR_NAME:-roaming}"
readonly PRECURSOR_DIR="${LT_CAPTURE_DIR}/${PRECURSOR_NAME}"
readonly PRECURSOR_FILTER="${LT_PRECURSOR_FILTER:-ether proto 0x890d}"
readonly PRECURSOR_STATE="${LT_CAPTURE_DIR}/.precursor-last"
readonly EVENT_LOG="${LT_CAPTURE_DIR}/kept-events.log"

# The lock lives in the data directory, not under /var/lock: the shipped unit
# runs with ProtectSystem=strict and has write access only there.
readonly LOCK_FILE="${LT_CAPTURE_DIR}/.flood-capture.lock"

readonly THRESHOLD="${LT_KEEP_THRESHOLD:-1000}"
readonly LOOKBACK_S="${LT_KEEP_LOOKBACK_S:-900}"
readonly RETENTION_DAYS="${LT_KEEP_RETENTION_DAYS:-14}"
readonly GROUP_S="${LT_PRECURSOR_GROUP_S:-300}"

saved=0

# Is a bit-identical copy of this ring file already in KEEP_DIR?
already_kept() {
    local source_file="$1" fingerprint existing

    fingerprint="$(stat -c '%s %Y' "$source_file" 2>/dev/null)" || return 1
    [[ -z "$fingerprint" ]] && return 1

    for existing in "${KEEP_DIR}"/*-"$(basename "$source_file")"; do
        [[ -f "$existing" ]] || continue
        [[ "$(stat -c '%s %Y' "$existing" 2>/dev/null)" == "$fingerprint" ]] && return 0
    done
    return 1
}

# Copy out the two most recent ring files under the given prefix. Counts into
# the global `saved`; see the header on why two files and why by mtime.
keep_ring() {
    local stamp="$1" prefix="$2" f target
    local -a files

    # shellcheck disable=SC2012
    # ls -t sorts by modification time, which is what "most recent ring file"
    # means here. The filenames carry tcpdump's rotation index, not a time.
    mapfile -t files < <(ls -t "${RING_DIR}"/broadcast.pcap* 2>/dev/null)
    (( ${#files[@]} == 0 )) && return 0

    for f in "${files[0]}" "${files[1]:-}"; do
        [[ -n "$f" && -f "$f" ]] || continue
        target="${KEEP_DIR}/${prefix}-${stamp}-$(basename "$f")"
        [[ -f "$target" ]] && continue
        already_kept "$f" && continue
        cp -p "$f" "$target" && saved=$((saved + 1))
    done
    return 0
}

# Timestamp of the most recent precursor frame, or empty.
#
# Selected by modification time, not by filename: a daily capture's name
# carries the date of its ROTATION, which starts counting from service start,
# not the date of its contents. Two files are read so a rotation does not open
# a gap.
last_precursor_frame() {
    local d
    local -a files

    command -v tcpdump >/dev/null 2>&1 || return 0
    # shellcheck disable=SC2012
    mapfile -t files < <(ls -t "${PRECURSOR_DIR}"/*.pcap 2>/dev/null | head -2)
    (( ${#files[@]} == 0 )) && return 0

    for d in "${files[@]}"; do
        tcpdump -r "$d" -nn -tt "$PRECURSOR_FILTER" 2>/dev/null
    done | awk '{ printf "%d\n", $1 }' | sort -n | tail -1
}

# Most recent interval in the lookback window over the threshold, or empty.
rate_trigger() {
    local now="$1" today file

    today=$(date +%F)
    file="${LT_PKTRATE_DIR}/${LT_NODE_NAME}-${today}.log"
    [[ -f "$file" ]] || return 0

    # The bracketed timestamp evaluates to 0 in awk arithmetic unless the
    # brackets are stripped first - pitfall C1. NUL bytes are filtered because
    # the logs contain them at breakpoints after an unclean stop.
    tr -d '\000' < "$file" | awk -v now="$now" -v back="$LOOKBACK_S" -v thr="$THRESHOLD" '
        !/^#/ {
            split($1, a, "[][]")
            if (a[2] >= now - back && ($3 > thr || $4 > thr)) print a[2]
        }' | tail -1
}

note() {
    printf '%s %s\n' "$(date -Is)" "$1" >> "$EVENT_LOG"
    log_info "$LOG_PREFIX $1"
}

main() {
    local now flood stamp precursor seen

    lt_ensure_dirs || exit 1
    mkdir -p "$KEEP_DIR" || { log_error "$LOG_PREFIX could not create $KEEP_DIR"; exit 1; }

    # Overlap protection. The timer uses OnCalendar rather than
    # OnUnitActiveSec, which loses its anchor with Type=oneshot; this lock is
    # the other half of that pattern. A second pass is redundant, so it exits
    # quietly rather than failing.
    exec 200>"$LOCK_FILE"
    flock -n 200 || exit 0

    now=$(date +%s)

    flood=$(rate_trigger "$now")
    if [[ -n "$flood" ]]; then
        stamp=$(date -d "@${flood}" +%Y%m%d-%H%M)
        saved=0
        keep_ring "$stamp" "flood"
        # Logged even when nothing was copied, and that is deliberate: the
        # deduplication would otherwise swallow the event notice whenever the
        # precursor trigger had already saved the same file. The TIME of an
        # event is a finding regardless of whether a copy resulted.
        if (( saved > 0 )); then
            note "rate event at $(date -d "@${flood}" +%H:%M:%S), ${saved} file(s) kept"
        else
            note "rate event at $(date -d "@${flood}" +%H:%M:%S), capture already kept"
        fi
    fi

    precursor=$(last_precursor_frame)
    seen=$(cat "$PRECURSOR_STATE" 2>/dev/null || echo 0)
    [[ "$seen" =~ ^[0-9]+$ ]] || seen=0

    if [[ -n "$precursor" ]] && (( precursor - seen >= GROUP_S )) \
       && (( precursor >= now - LOOKBACK_S )); then
        stamp=$(date -d "@${precursor}" +%Y%m%d-%H%M)
        saved=0
        keep_ring "$stamp" "precursor"
        if (( saved > 0 )); then
            note "precursor at $(date -d "@${precursor}" +%H:%M:%S), ${saved} file(s) kept"
        else
            note "precursor at $(date -d "@${precursor}" +%H:%M:%S), capture already kept"
        fi
        printf '%s' "$precursor" > "$PRECURSOR_STATE"
    fi

    # Bound the kept set. Unlike the daily captures, this directory has a
    # retention default: its contents are copies of a ring that is itself
    # bounded, and an unbounded copy of a bounded thing fills a disk on its own.
    find "$KEEP_DIR" \( -name 'flood-*.pcap*' -o -name 'precursor-*.pcap*' \) \
        -type f -mtime "+${RETENTION_DAYS}" -delete 2>/dev/null
}

main "$@"
