#!/bin/bash
# ---
# deployment: systemd-service
# service: lt-frame-capture@.service
# status: active
# type: daemon
# requires_root: false
# ---
# frame-capture.sh - targeted passive capture of one frame class
#
# PURPOSE
#   Captures ONE narrowly defined class of frames, continuously, so that the
#   question "did this happen before the symptom" has an answer afterwards.
#   pktrate.sh counts frames and cannot name a sender; l2-sniffer.sh sees only
#   STP and ARP. This fills the gap between them.
#
#   Completely passive: not a single packet is sent.
#
# INVOCATION
#   frame-capture.sh --profile broadcast
#   frame-capture.sh --profile loop-detect --iface eth1
#   frame-capture.sh --profile custom --filter 'ether proto 0x88cc' --name lldp
#   frame-capture.sh --help
#
# PROFILES
#   broadcast     Everything sent to a broadcast or multicast address. This is
#                 the profile that names a flood's source: a switch flooding
#                 all ports reports no discards and its MAC table only says
#                 "behind port N", not which of the ten devices there is
#                 sending. Ring buffer, 128-byte snaplen.
#
#   loop-detect   Ethertype 0x8899 - the loop-detection frames several vendors
#                 (Realtek-based switches among them) emit every few seconds.
#                 Each frame carries an identifier that changes per frame, so
#                 the SAME identifier arriving twice is a copy, and a copy is a
#                 closed loop. That test needs no flood and no traffic of your
#                 own - see docs/explanation/proving-a-loop.md.
#                 Daily files, full frames: the identifier is in the payload.
#
#   roaming       Ethertype 0x890d - 802.11r Fast BSS Transition "over the DS",
#                 access points coordinating a client handover across the wire.
#                 Cheap to keep (a few kB a day) and worth keeping: in the
#                 campaign this came from, these frames preceded broadcast
#                 surges by 0.1 to 4 seconds, and 491 minutes of quiet windows
#                 contained none at all.
#                 Daily files, full frames.
#
#   custom        Your own BPF filter via --filter. --name sets the directory
#                 and filename prefix.
#
# CONFIGURATION (environment or config/lan-tomography.conf)
#   LT_IFACE                interface to capture on
#   LT_BASE_DIR             where measurement data goes
#   LT_TZ                   timezone for timestamps           (default: UTC)
#   LT_PCAP_SIZE_MB         ring buffer file size             (default: 50)
#   LT_PCAP_FILES           ring buffer file count            (default: 10)
#   LT_PCAP_RETENTION_DAYS  delete daily pcaps older than this (default: keep)
#
# ON DISK USE - READ THIS BEFORE STARTING A DAILY PROFILE
#   Ring profiles cannot fill a disk: LT_PCAP_SIZE_MB * LT_PCAP_FILES is a hard
#   cap and tcpdump overwrites its own oldest file.
#
#   Daily profiles have NO cap unless you set LT_PCAP_RETENTION_DAYS, and that
#   is deliberate. These captures are evidence, and a tool that silently
#   deletes evidence to save disk space is worse than a full disk, which at
#   least announces itself. The volume differs by three orders of magnitude
#   between profiles - roaming ran at about a kilobyte a day in the campaign
#   this came from, loop-detect at about ten megabytes - so measure yours
#   before deciding.
#
# WHAT A NARROW FILTER COSTS
#   Everything outside the filter is invisible, and the gap does not announce
#   itself. In the campaign this came from the decisive event was a broadcast
#   flood that no capture recorded, because the only one running filtered on
#   'stp'. Run a counter (pktrate.sh) alongside, so at least the volume of what
#   you are not capturing is on record.
#
# SNAPLEN AND PAYLOAD
#   The ring profile truncates at 128 bytes: enough for headers plus the name
#   in an mDNS or NBNS query, which is what identifies a sender, and short
#   enough that session content is not collected. The daily profiles keep whole
#   frames because their identifying content sits in the payload - they are
#   also the profiles whose filters match control traffic only.
#
#   Whatever the snaplen, this writes packets from somebody's network to disk.
#   Treat the output as evidence: restrict it, and delete it when the
#   investigation ends. docs/how-to/tear-down.md covers that.

set -uo pipefail  # NO -e: tcpdump exits non-zero when the interface changes

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
readonly SCRIPT_DIR

# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh" || exit 1

# Help BEFORE main: the text comes from the header comment above.
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { lt_usage "$0"; exit 0; }

readonly LOG_PREFIX="[capture]"

# `tcpdump -tttt` and the %F in rotating filenames both take the local zone.
# Fix it explicitly rather than inheriting it: systemd units get no TZ and would
# fall back to /etc/localtime, so the day boundary of a daily file would differ
# between probes.
export TZ="${LT_TZ:-UTC}"

PROFILE=""
FILTER=""
NAME=""
IFACE="${LT_IFACE:-eth0}"
SNAPLEN=""
ROTATION=""

usage_error() {
    log_error "$LOG_PREFIX $1"
    printf 'Try --help.\n' >&2
    exit 2
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --profile) PROFILE="${2:-}"; shift 2 || usage_error "--profile needs a value" ;;
            --filter)  FILTER="${2:-}";  shift 2 || usage_error "--filter needs a value" ;;
            --name)    NAME="${2:-}";    shift 2 || usage_error "--name needs a value" ;;
            --iface)   IFACE="${2:-}";   shift 2 || usage_error "--iface needs a value" ;;
            --snaplen) SNAPLEN="${2:-}"; shift 2 || usage_error "--snaplen needs a value" ;;
            *)         usage_error "unknown argument: $1" ;;
        esac
    done

    [[ -n "$PROFILE" ]] || usage_error "no --profile given"

    # Each profile fixes a filter, a snaplen and a rotation mode. They are
    # defaults in the sense that --snaplen overrides them, but they are not
    # arbitrary - the reasoning for each is in the header.
    case "$PROFILE" in
        broadcast)
            FILTER="${FILTER:-ether broadcast or ether multicast}"
            NAME="${NAME:-broadcast}"
            SNAPLEN="${SNAPLEN:-128}"
            ROTATION="ring"
            ;;
        loop-detect)
            FILTER="${FILTER:-ether proto 0x8899}"
            NAME="${NAME:-loop-detect}"
            SNAPLEN="${SNAPLEN:-0}"
            ROTATION="daily"
            ;;
        roaming)
            FILTER="${FILTER:-ether proto 0x890d}"
            NAME="${NAME:-roaming}"
            SNAPLEN="${SNAPLEN:-0}"
            ROTATION="daily"
            ;;
        custom)
            [[ -n "$FILTER" ]] || usage_error "--profile custom needs --filter"
            NAME="${NAME:-custom}"
            SNAPLEN="${SNAPLEN:-128}"
            ROTATION="daily"
            ;;
        *)
            usage_error "unknown profile: $PROFILE (broadcast, loop-detect, roaming, custom)"
            ;;
    esac

    # The name becomes a directory and a filename prefix. Anything else would
    # let a stray argument write outside LT_BASE_DIR.
    [[ "$NAME" =~ ^[A-Za-z0-9_-]+$ ]] || usage_error "--name must be [A-Za-z0-9_-]+: $NAME"
    [[ "$SNAPLEN" =~ ^[0-9]+$ ]] || usage_error "--snaplen must be a number: $SNAPLEN"
}

start_ring() {
    local dir="$1"
    # -C/-W cap the total at LT_PCAP_SIZE_MB * LT_PCAP_FILES and rotate in
    # place. The rotation is also what forces periodic flushes, which is why
    # this mode does not need -U.
    tcpdump -i "$IFACE" -n -s "$SNAPLEN" \
            -C "${LT_PCAP_SIZE_MB:-50}" -W "${LT_PCAP_FILES:-10}" \
            -w "${dir}/${NAME}.pcap" \
            "$FILTER" >/dev/null 2>&1 &
    printf '%s' "$!"
}

start_daily() {
    local dir="$1"
    # -U is not optional here, and the reason is measured rather than
    # theoretical: without it tcpdump holds frames until 8 KB of buffer has
    # filled. On a profile producing a kilobyte a day that means the file first
    # becomes readable after about four days, and until then a 0-byte file is
    # indistinguishable from "no frames matched" - which is exactly the
    # observation such a capture exists to make.
    tcpdump -i "$IFACE" -n -s "$SNAPLEN" -U \
            -G 86400 \
            -w "${dir}/${NAME}-%F.pcap" \
            "$FILTER" >/dev/null 2>&1 &
    printf '%s' "$!"
}

prune_daily() {
    local dir="$1" days="${LT_PCAP_RETENTION_DAYS:-}"
    [[ -n "$days" ]] || return 0
    [[ "$days" =~ ^[0-9]+$ ]] || { log_warning "$LOG_PREFIX ignoring non-numeric LT_PCAP_RETENTION_DAYS: $days"; return 0; }
    find "$dir" -maxdepth 1 -name "${NAME}-*.pcap" -mtime "+${days}" -delete 2>/dev/null
}

main() {
    parse_args "$@"

    local dir="${LT_CAPTURE_DIR}/${NAME}"
    local pid

    lt_ensure_dirs || exit 1
    mkdir -p "$dir" || { log_error "$LOG_PREFIX could not create $dir"; exit 1; }

    if ! command -v tcpdump >/dev/null 2>&1; then
        log_error "$LOG_PREFIX tcpdump is not installed"
        exit 1
    fi

    log_info "$LOG_PREFIX profile $PROFILE on $IFACE, filter: $FILTER"
    log_info "$LOG_PREFIX snaplen $SNAPLEN, $ROTATION rotation, writing to $dir"

    if [[ "$ROTATION" == "ring" ]]; then
        pid=$(start_ring "$dir")
        log_info "$LOG_PREFIX ring buffer running (PID $pid, max $(( ${LT_PCAP_SIZE_MB:-50} * ${LT_PCAP_FILES:-10} )) MB)"
    else
        pid=$(start_daily "$dir")
        if [[ -n "${LT_PCAP_RETENTION_DAYS:-}" ]]; then
            log_info "$LOG_PREFIX daily files (PID $pid, deleting after ${LT_PCAP_RETENTION_DAYS} days)"
        else
            log_warning "$LOG_PREFIX daily files (PID $pid, NO retention limit - watch the disk)"
        fi
    fi
    add_exit_trap "kill $pid 2>/dev/null"

    # Waiting in a loop rather than `wait`: the pruning has to happen
    # periodically, and a dead tcpdump has to end the unit so systemd restarts
    # it instead of leaving a service that is up and capturing nothing.
    while kill -0 "$pid" 2>/dev/null; do
        [[ "$ROTATION" == "daily" ]] && prune_daily "$dir"
        sleep 300
    done

    log_error "$LOG_PREFIX tcpdump exited - capture is no longer running"
    exit 1
}

main "$@"
