#!/bin/bash
# ---
# deployment: manual
# status: active
# type: installer
# requires_root: true
# ---
# install.sh - install lan-tomography on a server or a probe node
#
# PURPOSE
#   Copies the tools to an install directory, resolves the placeholders in the
#   systemd unit templates and puts them in place. It ENABLES nothing and
#   STARTS nothing: which units belong on this machine is a decision about the
#   measurement, not about the installation.
#
# INVOCATION
#   sudo ./install.sh --role server [options]
#   sudo ./install.sh --role node   [options]
#   ./install.sh --dry-run --role node        show what would happen
#   ./install.sh --help
#
# OPTIONS
#   --role server|node   which set of units to install (required)
#   --prefix DIR         install directory       (default: /opt/lan-tomography)
#   --config DIR         configuration directory (default: /etc/lan-tomography)
#   --data DIR           measurement directory   (default: /var/log/lan-tomography)
#   --user NAME          user the units run as   (default: lan-tomography)
#   --python PATH        interpreter for the Python tools (default: /usr/bin/python3)
#   --dry-run            print actions, change nothing
#
# AFTER INSTALLING
#   1. Edit <config>/lan-tomography.conf and <config>/targets.conf.
#      Nothing works until the target matrix describes YOUR network - see
#      docs/explanation/target-matrix.md, and do not copy the thresholds.
#   2. Enable only the units you actually want:
#        systemctl enable --now lt-probe-node.service
#        systemctl enable --now lt-ping@192.0.2.1.service
#   3. Confirm the chain is alive before trusting any quiet period:
#        <prefix>/src/events/liveness-check.sh --report
#
# WHAT THIS DOES NOT DO
#   It does not grant packet-capture privileges beyond the capabilities in the
#   unit files, it does not open firewall rules, and it does not create the
#   SNMP community. All three are deliberate: they are decisions about somebody
#   else's network.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
readonly SCRIPT_DIR

ROLE=""
PREFIX="/opt/lan-tomography"
CONFIG_DIR="/etc/lan-tomography"
DATA_DIR="/var/log/lan-tomography"
RUN_USER="lan-tomography"
PYTHON="/usr/bin/python3"
DRY_RUN=false

usage() {
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
}

say()  { printf '[install] %s\n' "$*"; }
die()  { printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }
run()  {
    if [[ "$DRY_RUN" == "true" ]]; then
        printf '[dry-run] %s\n' "$*"
    else
        "$@" || die "failed: $*"
    fi
}

# Units that belong to each role. Deliberately explicit rather than "everything
# in systemd/": installing a switch poller on a probe node produces a unit that
# fails every minute, and a permanently failing unit trains people to ignore
# `systemctl --failed`.
readonly -a SERVER_UNITS=(
    lt-ping@.service
    lt-l2-sniffer.service
    lt-pktrate.service
    lt-tcp-probe.service
    lt-switch-probe.service lt-switch-probe.timer
    lt-fdb-probe.service    lt-fdb-probe.timer
    lt-compress-logs.service lt-compress-logs.timer
    lt-liveness-check.service lt-liveness-check.timer
    lt-correlate.service lt-correlate.timer
    lt-event-watch.service
    lt-sync-node@.service lt-sync-node@.timer
)
readonly -a NODE_UNITS=(
    lt-probe-node.service
    lt-pktrate.service
    lt-compress-logs.service lt-compress-logs.timer
)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --role)    ROLE="${2:-}"; shift 2 ;;
        --prefix)  PREFIX="${2:-}"; shift 2 ;;
        --config)  CONFIG_DIR="${2:-}"; shift 2 ;;
        --data)    DATA_DIR="${2:-}"; shift 2 ;;
        --user)    RUN_USER="${2:-}"; shift 2 ;;
        --python)  PYTHON="${2:-}"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *)         die "unknown option: $1 (try --help)" ;;
    esac
done

[[ -n "$ROLE" ]] || die "--role server|node is required"
[[ "$ROLE" == "server" || "$ROLE" == "node" ]] || die "--role must be server or node"

if [[ "$DRY_RUN" != "true" && "$(id -u)" -ne 0 ]]; then
    die "must run as root (or use --dry-run)"
fi

[[ -x "$PYTHON" ]] || say "WARNING: $PYTHON is not executable - pass --python"

say "role       : $ROLE"
say "prefix     : $PREFIX"
say "config     : $CONFIG_DIR"
say "data       : $DATA_DIR"
say "user       : $RUN_USER"
say "python     : $PYTHON"
[[ "$DRY_RUN" == "true" ]] && say "DRY-RUN: nothing will be changed"

# --- user ----------------------------------------------------------------
if ! id -u "$RUN_USER" >/dev/null 2>&1; then
    say "creating system user $RUN_USER"
    run useradd --system --no-create-home --shell /usr/sbin/nologin "$RUN_USER"
else
    say "user $RUN_USER already exists"
fi

# --- directories ---------------------------------------------------------
run install -d -m 0755 "$PREFIX"
run install -d -m 0750 -o "$RUN_USER" -g "$RUN_USER" "$CONFIG_DIR"
# The measurement directory holds captures. It is evidence, not logs.
run install -d -m 0750 -o "$RUN_USER" -g "$RUN_USER" "$DATA_DIR"

# --- code ----------------------------------------------------------------
say "copying tools"
run cp -a "${SCRIPT_DIR}/src" "$PREFIX/"
run cp -a "${SCRIPT_DIR}/README.md" "$PREFIX/"
[[ -d "${SCRIPT_DIR}/docs" ]] && run cp -a "${SCRIPT_DIR}/docs" "$PREFIX/"

# --- configuration -------------------------------------------------------
# Never overwrite an existing config: a reinstall must not silently reset a
# target matrix somebody spent an afternoon getting right.
for example in "${SCRIPT_DIR}"/config/*.example; do
    [[ -f "$example" ]] || continue
    target="${CONFIG_DIR}/$(basename "${example%.example}")"
    if [[ -e "$target" ]]; then
        say "keeping existing $(basename "$target")"
    else
        say "installing $(basename "$target")"
        run install -m 0640 -o "$RUN_USER" -g "$RUN_USER" "$example" "$target"
    fi
done

# --- units ---------------------------------------------------------------
if [[ "$ROLE" == "server" ]]; then
    units=("${SERVER_UNITS[@]}")
else
    units=("${NODE_UNITS[@]}")
fi

say "installing ${#units[@]} unit template(s)"
for unit in "${units[@]}"; do
    src="${SCRIPT_DIR}/systemd/${unit}"
    [[ -f "$src" ]] || { say "WARNING: missing template $unit, skipped"; continue; }
    if [[ "$DRY_RUN" == "true" ]]; then
        printf '[dry-run] would install /etc/systemd/system/%s\n' "$unit"
        continue
    fi
    sed -e "s|__INSTALL_DIR__|${PREFIX}|g" \
        -e "s|__CONFIG_DIR__|${CONFIG_DIR}|g" \
        -e "s|__BASE_DIR__|${DATA_DIR}|g" \
        -e "s|__USER__|${RUN_USER}|g" \
        -e "s|__PYTHON__|${PYTHON}|g" \
        "$src" > "/etc/systemd/system/${unit}" \
        || die "could not write /etc/systemd/system/${unit}"
    chmod 0644 "/etc/systemd/system/${unit}"
done

# Leaving a placeholder behind produces a unit that fails in a way nobody
# associates with the installer. Check rather than hope.
if [[ "$DRY_RUN" != "true" ]]; then
    if grep -l '__[A-Z_]*__' /etc/systemd/system/lt-*.service \
                             /etc/systemd/system/lt-*.timer 2>/dev/null; then
        die "unresolved placeholders in the units listed above"
    fi
fi

run systemctl daemon-reload

say ""
say "installed. Nothing has been enabled or started - that is deliberate."
say ""
say "next:"
say "  1. edit ${CONFIG_DIR}/lan-tomography.conf and ${CONFIG_DIR}/targets.conf"
say "     the defaults describe nobody's network, least of all yours"
say "  2. enable what this machine should run, for example:"
if [[ "$ROLE" == "server" ]]; then
    say "       systemctl enable --now lt-ping@192.0.2.1.service"
    say "       systemctl enable --now lt-switch-probe.timer"
else
    say "       systemctl enable --now lt-probe-node.service"
fi
say "  3. confirm the chain is alive BEFORE trusting any quiet period:"
say "       ${PREFIX}/src/events/liveness-check.sh --report"
