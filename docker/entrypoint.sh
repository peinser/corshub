#!/bin/bash

set -e

exec python -m corshub.bin.standalone \
     --host=0.0.0.0 \
     --access-log \
     --proxies-count=1 \
     --real-ip-header=X-Real-IP \
     --workers=1 $@
