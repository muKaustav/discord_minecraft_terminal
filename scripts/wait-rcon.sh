#!/bin/bash
set -eu
for i in $(seq 1 150); do
  if ss -ltn | grep -q ':25585'; then
    # The port opens before the server finishes post-load work, so the first
    # RCON command times out unless we let it settle.
    sleep 20
    exit 0
  fi
  sleep 2
done
exit 0
