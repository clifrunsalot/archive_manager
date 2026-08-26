#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

for directory in ARCHIVE data logs artifact_output; do
	path="$PROJECT_ROOT/$directory"
	if [[ -d "$path" ]]; then
		find "$path" -type d -exec chmod 700 {} +
		find "$path" -type f -exec chmod 600 {} +
	fi
done

printf 'Sensitive archive directories and files are owner-only.\n'
