#!/usr/bin/env bash

set -euo pipefail

PROJECT_NETWORK="${COMPOSE_PROJECT_NAME:-archive_manager}_default"
ANYTHINGLLM_CONTAINER="${ANYTHINGLLM_CONTAINER:-archive-anythingllm}"

docker compose down --remove-orphans

if docker container inspect "$ANYTHINGLLM_CONTAINER" >/dev/null 2>&1; then
	if [[ "$(docker inspect -f '{{.State.Running}}' "$ANYTHINGLLM_CONTAINER")" == "true" ]]; then
		docker stop "$ANYTHINGLLM_CONTAINER"
	fi
	docker rm "$ANYTHINGLLM_CONTAINER"
fi

if docker network inspect "$PROJECT_NETWORK" >/dev/null 2>&1; then
	docker network rm "$PROJECT_NETWORK" >/dev/null
fi

printf 'Docker services and project network are down.\n'