#!/bin/bash

set -e

delay=5 # Wait for K8S stdout flush :)

exec python -m corshub.bin.standalone \
     --host=0.0.0.0 \
     --access-log \
     --proxies-count=1 \
     --real-ip-header=X-Real-IP $@
