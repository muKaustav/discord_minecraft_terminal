#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/rlcraft-discord
IDLE_DIR=/opt/rlcraft-idle

sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip nodejs npm

sudo mkdir -p "${APP_DIR}" "${IDLE_DIR}"
sudo chown ubuntu:ubuntu "${APP_DIR}" "${IDLE_DIR}"

if [[ ! -x "${APP_DIR}/venv/bin/python" ]]; then
  python3 -m venv "${APP_DIR}/venv"
fi
"${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

chmod 755 "${APP_DIR}/wait-rcon.sh"
chmod 600 "${APP_DIR}/.env" || true

cd "${IDLE_DIR}"
if [[ ! -f package.json ]]; then
  echo "idle package.json missing"
  exit 1
fi
npm install --omit=dev

sudo cp /tmp/rlcraft-units/rlcraft-terminal.service /etc/systemd/system/rlcraft-terminal.service
sudo cp /tmp/rlcraft-units/rlcraft-discord.service /etc/systemd/system/rlcraft-discord.service
sudo cp /tmp/rlcraft-units/rlcraft-idle-stop.service /etc/systemd/system/rlcraft-idle-stop.service
sudo systemctl daemon-reload
sudo systemctl enable --now rlcraft.service
sudo systemctl enable --now rlcraft-terminal.service
sudo systemctl enable --now rlcraft-discord.service
sudo systemctl enable --now rlcraft-idle-stop.service
sudo systemctl restart rlcraft-terminal.service
sudo systemctl restart rlcraft-discord.service
sudo systemctl restart rlcraft-idle-stop.service

systemctl is-active rlcraft.service
systemctl is-active rlcraft-terminal.service
systemctl is-active rlcraft-discord.service
systemctl is-active rlcraft-idle-stop.service
