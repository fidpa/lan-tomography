#!/bin/bash
# ---
# deployment: library
# status: active
# type: library
# requires_root: false
# ---
# common.sh - shared configuration, logging and cleanup for lan-tomography
#
# Purpose: keep every tool in this repository self-contained. The tools this
#          project grew out of sourced a 2790-line general-purpose shell
#          library from their parent repository and used exactly nine of its
#          seventy-five functions. Those nine live here, so a probe host needs
#          nothing but this repository.
#
# Sourced only - this file has no entry point of its own.
#
# CONFIGURATION
#   Values are read in this order, last one wins:
#     1. the defaults below
#     2. $LT_CONFIG, or config/lan-tomography.conf next to the repository root
#     3. the environment
#   That order lets a systemd unit override a config file without editing it.
#
# LOGGING
#   log_info / log_warning / log_error / log_success write to stdout, to
#   $LT_LOG_FILE and to the journal. They carry OPERATIONAL messages only.
#   Measurement data is never written through them - probes append to their
#   own files directly, so the evidence chain does not depend on this code.
#
# ALERTING
#   lt_alert and lt_alert_once hand subject and body to $LT_ALERT_CMD. This
#   repository ships no mailer: what happens with an alert is the operator's
#   decision (msmtp, ntfy, Telegram, logger). Unset means alerts are logged
#   and nothing else.

# Include guard: sourcing twice must not repeat the readonly assignments.
[[ -n "${LT_COMMON_LOADED:-}" ]] && return 0
readonly LT_COMMON_LOADED=1

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

LT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LT_REPO_ROOT="$(cd "${LT_LIB_DIR}/../.." && pwd)"
readonly LT_LIB_DIR LT_REPO_ROOT

# The project version lives in exactly one place: VERSION at the repository
# root. Every tool used to carry its own "Version: 1.0" header, which is how a
# help text ends up announcing a release that never existed.
#
# "unknown" rather than fatal: a single script copied onto a probe should still
# run, and saying "unknown" is honest where a stale number is not.
if [[ -r "${LT_REPO_ROOT}/VERSION" ]]; then
    LT_VERSION="$(<"${LT_REPO_ROOT}/VERSION")"
    LT_VERSION="${LT_VERSION//[$'\t\r\n ']/}"
else
    LT_VERSION="unknown"
fi
readonly LT_VERSION

# A config file may set any LT_* value. Environment wins over it, so the
# values are captured before sourcing and restored afterwards.
_lt_load_config() {
    local candidate="${LT_CONFIG:-${LT_REPO_ROOT}/config/lan-tomography.conf}"
    [[ -r "$candidate" ]] || return 0

    local env_base="${LT_BASE_DIR:-}" env_iface="${LT_IFACE:-}"
    local env_node="${LT_NODE_NAME:-}" env_switch="${LT_SWITCH_IP:-}"
    local env_comm="${LT_SNMP_COMMUNITY:-}" env_secrets="${LT_SECRETS:-}"
    local env_alert="${LT_ALERT_CMD:-}" env_log="${LT_LOG_FILE:-}"

    # shellcheck source=/dev/null
    source "$candidate" || return 1

    [[ -n "$env_base" ]]    && LT_BASE_DIR="$env_base"
    [[ -n "$env_iface" ]]   && LT_IFACE="$env_iface"
    [[ -n "$env_node" ]]    && LT_NODE_NAME="$env_node"
    [[ -n "$env_switch" ]]  && LT_SWITCH_IP="$env_switch"
    [[ -n "$env_comm" ]]    && LT_SNMP_COMMUNITY="$env_comm"
    [[ -n "$env_secrets" ]] && LT_SECRETS="$env_secrets"
    [[ -n "$env_alert" ]]   && LT_ALERT_CMD="$env_alert"
    [[ -n "$env_log" ]]     && LT_LOG_FILE="$env_log"
    return 0
}
_lt_load_config

# Where all measurement data goes. Treat it as evidence, not as logs: a
# capture contains whatever crossed the wire.
LT_BASE_DIR="${LT_BASE_DIR:-/var/log/lan-tomography}"
LT_PING_DIR="${LT_BASE_DIR}/ping"
LT_L2_DIR="${LT_BASE_DIR}/l2"
LT_PKTRATE_DIR="${LT_BASE_DIR}/pktrate"
LT_SWITCH_DIR="${LT_BASE_DIR}/switch"
LT_CAPTURE_DIR="${LT_BASE_DIR}/capture"

# Capture interface and the name this probe reports itself under. Both differ
# per probe host, which is the whole point of a distributed measurement.
LT_IFACE="${LT_IFACE:-eth0}"
LT_NODE_NAME="${LT_NODE_NAME:-$(hostname -s 2>/dev/null || echo probe)}"

# Switch to poll over SNMP, and the read-only community.
LT_SWITCH_IP="${LT_SWITCH_IP:-}"
LT_SNMP_COMMUNITY="${LT_SNMP_COMMUNITY:-}"

# File holding credentials, outside the repository. Never commit it.
LT_SECRETS="${LT_SECRETS:-}"

# Operational log. Set BEFORE sourcing this file if the unit runs under
# ProtectSystem=strict and cannot write the default location - otherwise the
# write fails silently and the tool looks healthy while logging nothing.
LT_LOG_FILE="${LT_LOG_FILE:-${LT_BASE_DIR}/lan-tomography.log}"

# Command receiving alerts. Subject as $1, body on stdin.
LT_ALERT_CMD="${LT_ALERT_CMD:-}"

# Where lt_alert_once keeps its cooldown state.
LT_ALERT_STATE_DIR="${LT_ALERT_STATE_DIR:-${LT_BASE_DIR}/.alert-state}"

LT_LOG_TO_STDOUT="${LT_LOG_TO_STDOUT:-true}"
LT_LOG_TO_JOURNAL="${LT_LOG_TO_JOURNAL:-true}"

readonly LT_PING_DIR LT_L2_DIR LT_PKTRATE_DIR LT_SWITCH_DIR LT_CAPTURE_DIR

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

_lt_log() {
    local level="${1:-INFO}"
    # Defensive: log_info $var with an empty unquoted variable passes zero
    # arguments, and a bare $1 would kill a caller running under `set -u`.
    local message="${2-}"
    shift $(( $# >= 2 ? 2 : $# ))
    [[ $# -gt 0 ]] && message="$message $*"

    if [[ "$LT_LOG_TO_JOURNAL" == "true" ]] && command -v logger >/dev/null 2>&1; then
        local prio
        case "$level" in
            ERROR)   prio="err" ;;
            WARNING) prio="warning" ;;
            *)       prio="info" ;;
        esac
        logger -t "lan-tomography" -p "user.${prio}" -- "$message" 2>/dev/null || true
    fi

    local dir
    dir="$(dirname "$LT_LOG_FILE")"
    [[ -d "$dir" ]] || mkdir -p "$dir" 2>/dev/null || true
    printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$level" "$message" \
        >> "$LT_LOG_FILE" 2>/dev/null || true

    if [[ "$LT_LOG_TO_STDOUT" == "true" ]]; then
        printf '[%s] [%s] %s\n' "$(date '+%H:%M:%S')" "$level" "$message"
    fi
    return 0
}

log_info()    { _lt_log INFO    "$@"; }
log_warning() { _lt_log WARNING "$@"; }
log_error()   { _lt_log ERROR   "$@" >&2; }
log_success() { _lt_log SUCCESS "$@"; }

# Accepts either `log LEVEL "message"` or `log "message"`.
log() {
    [[ $# -eq 0 ]] && return 0
    if [[ "$1" =~ ^(DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|SUCCESS|CRITICAL)$ ]]; then
        _lt_log "$@"
    else
        _lt_log INFO "$@"
    fi
}

# --------------------------------------------------------------------------
# Exit traps
#
# Ported unchanged in behaviour from the parent library. The subshell refusal
# matters: an EXIT trap only fires in the process that set it, so registering
# one from a subshell either loses the handler silently or runs an absorbed
# parent trap twice. Refuse loudly rather than fail quietly.
# --------------------------------------------------------------------------

_LT_EXIT_TRAP_HANDLERS=()
_LT_EXIT_TRAP_INSTALLED=0

_lt_run_exit_traps() {
    local ret=$?
    local handler
    for handler in "${_LT_EXIT_TRAP_HANDLERS[@]}"; do
        eval "$handler" || true
    done
    exit "$ret"
}

add_exit_trap() {
    local handler="${1:-}"
    [[ -z "$handler" ]] && return 0

    if [[ "${BASHPID:-$$}" != "$$" ]]; then
        printf 'add_exit_trap: refused in subshell (BASHPID %s != %s), handler not registered: %s\n' \
            "${BASHPID:-?}" "$$" "$handler" >&2
        return 1
    fi

    if (( _LT_EXIT_TRAP_INSTALLED == 0 )); then
        # Absorb an EXIT trap set before us so its cleanup is not lost.
        local existing body exit_re
        existing="$(trap -p EXIT 2>/dev/null || true)"
        if [[ -n "$existing" ]]; then
            # `trap -p` prints shell-quoted output meant for re-eval. Let bash
            # unquote it; regex parsing breaks on quotes and newlines in the body.
            eval "set -- $existing"    # -> $1=trap $2=-- $3=BODY $4=EXIT
            body="${3:-}"
            # Strip the absorbed trap's own exit-code plumbing: the exit code
            # belongs to the dispatcher.
            body="$(sed -E '1s/^[[:space:]]*ret=\$\?[[:space:]]*;?[[:space:]]*//' <<<"$body")"
            body="$(sed -E '$s/[[:space:]]*;?[[:space:]]*exit[[:space:]]+"?\$\{?ret\}?"?[[:space:]]*$//' <<<"$body")"
            # Any surviving `exit` would break the dispatcher loop and overwrite
            # the exit code - wrap it in a subshell.
            exit_re='(^|[^[:alnum:]_])exit([^[:alnum:]_]|$)'
            if [[ "$body" =~ $exit_re ]]; then
                body="( ${body} )"
            fi
            [[ -n "$body" ]] && _LT_EXIT_TRAP_HANDLERS+=("$body")
        fi
        trap '_lt_run_exit_traps' EXIT
        _LT_EXIT_TRAP_INSTALLED=1
    fi

    _LT_EXIT_TRAP_HANDLERS+=("$handler")
}

# --------------------------------------------------------------------------
# Redaction
#
# Ported unchanged. Alert bodies quote command output, and command output
# quotes credentials often enough that this is not optional.
# --------------------------------------------------------------------------

redact_sensitive_data() {
    local input="${1:-}"
    [[ -z "$input" ]] && input=$(cat)
    [[ -z "$input" ]] && return 0

    echo "$input" | sed -E \
        -e 's/(password|passwd|pwd)[=:]["'"'"']?[^"'"'"' \t\n]+/\1=***REDACTED***/gi' \
        -e 's/(PGPASSWORD|DB_PASSWORD|MYSQL_PASSWORD|POSTGRES_PASSWORD)=[^ \t\n]+/\1=***REDACTED***/g' \
        -e 's/(api_key|apikey|api-key|token|secret|auth_token|access_token)[=:]["'"'"']?[^"'"'"' \t\n]+/\1=***REDACTED***/gi' \
        -e 's/(Authorization:[[:space:]]*Bearer[[:space:]])[^ \t\n]+/\1***REDACTED***/gi' \
        -e 's/(Basic[[:space:]])[A-Za-z0-9+/=]+/\1***REDACTED***/g' \
        -e 's/(AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY)=[^ \t\n]+/\1=***REDACTED***/g' \
        -e 's#(postgres|mysql|mongodb|redis)://[^@]+@#\1://***REDACTED***@#gi' \
        -e 's/(community[[:space:]]*[=:][[:space:]]*)[^ \t\n]+/\1***REDACTED***/gi' \
        -e 's/eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*/***JWT_REDACTED***/g'
}

# --------------------------------------------------------------------------
# Alerting
# --------------------------------------------------------------------------

# lt_alert <subject> <body>
lt_alert() {
    local subject="${1:-lan-tomography}"
    local body="${2:-}"

    body="$(redact_sensitive_data "$body")"
    log_warning "ALERT: $subject"

    if [[ -z "$LT_ALERT_CMD" ]]; then
        # No handler configured. The alert is in the log and the journal; say
        # so once rather than losing it silently.
        log_info "LT_ALERT_CMD is unset - alert logged only"
        return 0
    fi

    printf '%s\n' "$body" | $LT_ALERT_CMD "$subject" || {
        log_error "LT_ALERT_CMD failed: $LT_ALERT_CMD"
        return 1
    }
    return 0
}

# lt_alert_once <key> <cooldown-seconds> <subject> <body>
# Suppresses repeats of the same key within the cooldown. A flapping link
# would otherwise alert every poll cycle, and an operator who mutes the
# channel loses the next real alert with it.
lt_alert_once() {
    local key="${1:?key required}"
    local cooldown="${2:-3600}"
    local subject="${3:-lan-tomography}"
    local body="${4:-}"

    [[ -d "$LT_ALERT_STATE_DIR" ]] || mkdir -p "$LT_ALERT_STATE_DIR" 2>/dev/null || true
    local state="${LT_ALERT_STATE_DIR}/${key//[^A-Za-z0-9_-]/_}"

    local now last
    now=$(date +%s)
    if [[ -r "$state" ]]; then
        last=$(cat "$state" 2>/dev/null || echo 0)
        if (( now - last < cooldown )); then
            log_info "alert '$key' suppressed ($(( cooldown - (now - last) ))s of cooldown left)"
            return 0
        fi
    fi

    printf '%s' "$now" > "$state" 2>/dev/null || true
    lt_alert "$subject" "$body"
}

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

# Print the calling script's header comment as its help text.
#
# Deliberately NOT a second, hand-written usage string: a second text drifts
# from the header and nobody notices. The tool this was extracted from proved
# the point - its single hand-rolled help used fixed line numbers (`sed -n
# '9,46p'`) and had silently started cutting twenty lines, including the whole
# "invocation" section, because the changelog above it had grown.
lt_usage() {
    local file="${1:-$0}"

    if [[ ! -r "$file" ]]; then
        printf 'help text not readable: %s\n' "$file" >&2
        return 1
    fi

    awk '
        NR == 1 && /^#!/ { next }          # shebang is not help
        /^# ---$/ { fm = !fm; next }       # skip the frontmatter block
        fm        { next }
        /^#/      { found = 1; sub(/^# ?/, ""); print; next }
        found     { exit }                 # first non-comment line ends it
    ' "$file"
    printf '\nlan-tomography %s\n' "$LT_VERSION"
    return 0
}

lt_ensure_dirs() {
    local dir
    for dir in "$LT_BASE_DIR" "$LT_PING_DIR" "$LT_L2_DIR" "$LT_PKTRATE_DIR" "$LT_SWITCH_DIR" \
               "$LT_CAPTURE_DIR"; do
        if [[ ! -d "$dir" ]]; then
            mkdir -p "$dir" || {
                log_error "could not create directory: $dir"
                return 1
            }
        fi
    done
    return 0
}

# Read one value from the secrets file without sourcing it. Sourcing an
# operator-supplied file executes whatever is in it.
lt_read_secret() {
    local key="${1:?key required}"
    [[ -r "$LT_SECRETS" ]] || {
        log_error "secrets file not readable: ${LT_SECRETS:-<unset>}"
        return 1
    }
    local value
    value="$(grep -m1 "^[[:space:]]*${key}=" "$LT_SECRETS" 2>/dev/null | cut -d= -f2-)"
    value="${value%\"}"; value="${value#\"}"
    value="${value%\'}"; value="${value#\'}"
    [[ -n "$value" ]] || { log_error "secret not found: $key"; return 1; }
    printf '%s' "$value"
}
