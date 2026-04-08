#!/bin/bash

set -e

delay=5 # Wait for K8S stdout flush :)

exec python -m sanic corshub.bin.standalone:app \
    --host 0.0.0.0 \
    --port "${CORSHUB_PORT:-8000}" \
    --single-process
