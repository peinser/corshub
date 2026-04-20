#!/bin/bash

set -euo pipefail  # Exit on error, undefined vars, pipe failures

export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8

# Copy host gitconfig into a writable file. Mounting .gitconfig directly causes
# "Device or resource busy" errors because git writes atomically via rename(),
# which fails across a bind-mounted file.
cp /root/.gitconfig.host /root/.gitconfig

echo "Starting devcontainer setup..."
uv sync --locked

echo "Setting up the devcontainer..."
make setup

echo "Startup complete."