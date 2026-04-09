#!/bin/bash

set -euo pipefail  # Exit on error, undefined vars, pipe failures

export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8

echo "Starting devcontainer setup..."
uv sync --locked

echo "Setting up the devcontainer..."
make setup

echo "Setting up SSH/GPG commit signing..."
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id.github.pub
git config --global commit.gpgsign true
git config --global tag.gpgsign true

echo "Startup complete."