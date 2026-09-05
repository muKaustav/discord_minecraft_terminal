#!/bin/bash
set -eu
for i in $(seq 1 90); do
  if ss -ltn | grep -q ':25585'; then
    exit 0
  fi
  sleep 2
done
exit 0
