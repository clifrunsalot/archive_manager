#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FORCE=0
if [[ "${1:-}" == "--force" ]]; then
	FORCE=1
fi

if [[ "$FORCE" != "1" ]]; then
	printf 'This permanently deletes generated logs and report artifacts under %s. Continue? [y/N]: ' "$PROJECT_ROOT"
	read -r answer
	case "$answer" in
		y|Y|yes|YES) ;;
		*) printf 'Cleanup cancelled.\n'; exit 1 ;;
	esac
fi

for directory in logs artifact_output; do
	path="$PROJECT_ROOT/$directory"
	if [[ -d "$path" ]]; then
		find "$path" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
	fi
done

printf 'Generated logs and report artifacts were removed.\n'
