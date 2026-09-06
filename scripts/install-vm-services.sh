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

sudo cp /tmp/rlcraft-units/bmc6.service /etc/systemd/system/bmc6.service
sudo cp /tmp/rlcraft-units/minecraft-server.service /etc/systemd/system/minecraft-server.service
sudo install -D -m 755 /tmp/rlcraft-units/pack_manager.py /opt/minecraft-booter/pack_manager.py
sudo mkdir -p /opt/minecraft-booter/packs
sudo touch /opt/minecraft-booter/curseforge.env
sudo chmod 600 /opt/minecraft-booter/curseforge.env
if [[ -d /opt/bmc6 && ! -e /opt/minecraft-booter/packs/8728685 ]]; then
  sudo ln -s /opt/bmc6 /opt/minecraft-booter/packs/8728685
fi
if [[ -f /opt/bmc6/start.sh && ! -e /opt/bmc6/minecraft-booter-start.sh ]]; then
  sudo ln -s start.sh /opt/bmc6/minecraft-booter-start.sh
fi
sudo cp /tmp/rlcraft-units/rlcraft-terminal.service /etc/systemd/system/rlcraft-terminal.service
sudo cp /tmp/rlcraft-units/rlcraft-discord.service /etc/systemd/system/rlcraft-discord.service
sudo cp /tmp/rlcraft-units/rlcraft-idle-stop.service /etc/systemd/system/rlcraft-idle-stop.service
sudo systemctl daemon-reload
sudo systemctl disable --now bmc6.service || true
sudo systemctl disable minecraft-server.service || true
sudo systemctl enable --now rlcraft-terminal.service
sudo systemctl enable --now rlcraft-discord.service
sudo systemctl enable --now rlcraft-idle-stop.service
sudo systemctl restart rlcraft-terminal.service
sudo systemctl restart rlcraft-discord.service
sudo systemctl restart rlcraft-idle-stop.service

systemctl is-enabled minecraft-server.service
systemctl is-active rlcraft-terminal.service
systemctl is-active rlcraft-discord.service
systemctl is-active rlcraft-idle-stop.service
