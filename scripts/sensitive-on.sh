#!/usr/bin/env bash
# Source this script to re-enable sensitive security protections and strict authorization:
#   source scripts/sensitive-on.sh

export ARCHIVE_SECURITY_MODE=sensitive
export ARCHIVE_AUTH_MODE=strict

printf '🔒 Sensitive mode and strict authorization ENABLED (default sensitive mode active).\n'
