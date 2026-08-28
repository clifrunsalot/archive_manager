#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

fail() {
	printf 'Deployment validation failed: %s\n' "$1" >&2
	exit 1
}

command -v node >/dev/null 2>&1 || fail "Node.js is required"
command -v npm >/dev/null 2>&1 || fail "npm is required"
command -v docker >/dev/null 2>&1 || fail "Docker is required"

[[ -f web/dist/index.html ]] || fail "build web/dist first with: cd web && npm run build"
[[ -f deploy/Caddyfile.example ]] || fail "missing deploy/Caddyfile.example"
[[ -f deploy/oauth2-proxy.env.example ]] || fail "missing deploy/oauth2-proxy.env.example"
[[ -f deploy/oauth2-proxy.env ]] || fail "copy deploy/oauth2-proxy.env.example to deploy/oauth2-proxy.env and configure OIDC"
grep -q 'replace-me\|replace-with-' deploy/oauth2-proxy.env && fail "replace placeholder OIDC values in deploy/oauth2-proxy.env"
[[ "${ARCHIVE_AUTH_MODE:-strict}" == "strict" ]] || fail "ARCHIVE_AUTH_MODE must be strict"
[[ -n "${ARCHIVE_ENCRYPTION_KEY:-}" ]] || fail "ARCHIVE_ENCRYPTION_KEY is not set"
[[ -n "${QDRANT_API_KEY:-}" ]] || fail "QDRANT_API_KEY is not set"

docker compose config --quiet

printf 'Deployment configuration is valid.\n'
printf 'UI bundle: present\n'
printf 'Authorization: strict\n'
printf 'Compose: valid\n'