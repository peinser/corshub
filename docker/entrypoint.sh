#!/bin/bash

set -e

delay=5 # Wait for K8S stdout flush :)

# Build --run arguments from CORSHUB_SERVICES (comma-separated, e.g. "ntrip=v1,ntrip=v2").
run_args=()
IFS=',' read -ra _services <<< "${CORSHUB_SERVICES:-ntrip=v1}"
for svc in "${_services[@]}"; do
    run_args+=(--run "$svc")
done

[[ "${CORSHUB_ACCESS_LOGS:-false}" == "true" ]] && run_args+=(--access-logs)

exec python -m corshub.bin.services \
    "${run_args[@]}" \
    --host 0.0.0.0 \
    --port "${CORSHUB_PORT:-8000}" \
    --workers "${CORSHUB_WORKERS:-1}" \
    --reverse-proxy-count "${CORSHUB_REVERSE_PROXY_COUNT:-0}"
