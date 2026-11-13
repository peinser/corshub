#!/bin/bash

set -e

exec python -m corshub.bin.standalone \
     --host=0.0.0.0 \
     --access-logs \
     --reverse-proxy-count=1 \
     --header-real-ip=X-Real-IP \
     "$@"
