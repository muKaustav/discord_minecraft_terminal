#!/usr/bin/env bash
# Run this ON the Oracle VM to pull the latest panel and restart it.
#   bash /home/ubuntu/discord_minecraft_terminal/power_panel/deploy.sh
set -euo pipefail

REPO="${REPO:-/home/ubuntu/discord_minecraft_terminal}"
TARGET="${TARGET:-/opt/rlcraft-power}"

git -C "$REPO" fetch origin
git -C "$REPO" reset --hard origin/main
sudo rsync -a --delete --exclude venv --exclude .env "$REPO/power_panel/" "$TARGET/"
sudo chown -R ubuntu:ubuntu "$TARGET"
"$TARGET/venv/bin/pip" install -q -r "$TARGET/requirements.txt"
sudo cp "$TARGET/rlcraft-power.service" /etc/systemd/system/rlcraft-power.service
sudo systemctl daemon-reload
sudo systemctl restart rlcraft-power
sudo systemctl is-active rlcraft-power
