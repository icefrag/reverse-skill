#!/usr/bin/env bash
# Advisory scope status reporter (personal lab edition). Never blocks ACT:
# always exits 0 for an existing case; exit 1 only for bad usage.
# --force / --quiet are accepted for backward compatibility.
# Usage:
#   bash skills/scripts/case-guard.sh --case-root work/my-case
set -euo pipefail

CASE_ROOT=""
QUIET=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -CaseRoot|--case-root) CASE_ROOT="${2:-}"; shift 2 ;;
    -Force|--force) shift ;;
    -Quiet|--quiet) QUIET=1; shift ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "Unknown arg: \"$1\"" >&2; exit 1 ;;
  esac
done

info() { [[ $QUIET -eq 1 ]] || echo "$*"; }

if [[ -z "$CASE_ROOT" ]]; then
  echo "ERROR: --case-root required" >&2
  exit 1
fi
if [[ ! -d "$CASE_ROOT" ]]; then
  echo "ERROR: CaseRoot missing: $CASE_ROOT" >&2
  exit 1
fi

SCOPE_PATH="$CASE_ROOT/scope.md"
if [[ ! -f "$SCOPE_PATH" ]]; then
  info "CASE-GUARD: no scope.md under $CASE_ROOT (advisory only; scaffold one with case-init)"
  exit 0
fi

section_field() {
  local section="$1"
  local field="$2"
  awk -v wanted_section="$section" -v wanted_field="$field" '
    /^##[[:space:]]+/ {
      heading=$0
      sub(/^##[[:space:]]+/, "", heading)
      active=(tolower(heading)==tolower(wanted_section))
      next
    }
    active {
      line=$0
      pattern="^[[:space:]]*-[[:space:]]*" wanted_field ":[[:space:]]*"
      if (line ~ pattern) {
        sub(pattern, "", line)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
        print line
        exit
      }
    }
  ' "$SCOPE_PATH"
}

auth_status="$(section_field auth status | tr -d '\r')"
net_mode="$(section_field network_profile mode | tr -d '\r')"
ready="$(section_field signoff ready_for_act | tr -d '\r')"

info "CASE-GUARD OK: $CASE_ROOT (advisory; never blocks)"
info "  auth.status=${auth_status:-n/a} network_profile=${net_mode:-n/a} ready_for_act=${ready:-n/a}"
exit 0
