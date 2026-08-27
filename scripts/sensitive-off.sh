#!/usr/bin/env bash
# Source this script to disable sensitive security protections and strict authorization:
#   source scripts/sensitive-off.sh

export ARCHIVE_SECURITY_MODE=compat
export ARCHIVE_AUTH_MODE=compat

printf '🔓 Sensitive mode and strict authorization DISABLED (compat mode active).\n'
