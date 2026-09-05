#!/usr/bin/env bash
set -euo pipefail

# Run from a machine that can SSH to the always-on Oracle VM.
# Usage: KEY=/path/to/ssh-key-2026-09-05.key ./deploy.sh

KEY="${KEY:-$HOME/.ssh/ssh-key-2026-09-05.key}"
HOST="${HOST:-ubuntu@144.24.114.0}"
ENV_FILE="${ENV_FILE:-$HOME/.ssh/rlcraft-power.env}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "${KEY}" ]]; then
  echo "SSH key not found: ${KEY}"
  exit 1
fi
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Env file not found: ${ENV_FILE}"
  exit 1
fi

ssh -i "${KEY}" -o IdentitiesOnly=yes "${HOST}" "sudo mkdir -p /opt/rlcraft-power && sudo chown ubuntu:ubuntu /opt/rlcraft-power"
rsync -az -e "ssh -i ${KEY} -o IdentitiesOnly=yes" \
  --exclude venv --exclude __pycache__ --exclude .env \
  "${ROOT}/app.py" "${ROOT}/requirements.txt" "${ROOT}/templates" "${ROOT}/static" \
  "${HOST}:/opt/rlcraft-power/"
scp -i "${KEY}" -o IdentitiesOnly=yes "${ENV_FILE}" "${HOST}:/opt/rlcraft-power/.env"
scp -i "${KEY}" -o IdentitiesOnly=yes "${ROOT}/rlcraft-power.service" "${HOST}:/tmp/rlcraft-power.service"
scp -i "${KEY}" -o IdentitiesOnly=yes "${ROOT}/nginx-rlcraft-power.conf" "${HOST}:/tmp/nginx-rlcraft-power.conf"

ssh -i "${KEY}" -o IdentitiesOnly=yes "${HOST}" bash -s <<'REMOTE'
set -euo pipefail
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip nginx
python3 -m venv /opt/rlcraft-power/venv
/opt/rlcraft-power/venv/bin/pip install -r /opt/rlcraft-power/requirements.txt
chmod 600 /opt/rlcraft-power/.env
sudo mv /tmp/rlcraft-power.service /etc/systemd/system/rlcraft-power.service
sudo mv /tmp/nginx-rlcraft-power.conf /etc/nginx/sites-available/rlcraft-powe
sudo ln -sfn /etc/nginx/sites-available/rlcraft-power /etc/nginx/sites-enabled/rlcraft-powe
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl daemon-reload
sudo systemctl enable --now rlcraft-powe
sudo systemctl restart nginx
sudo systemctl restart rlcraft-powe
sudo systemctl is-active rlcraft-power nginx
REMOTE

echo "Deployed. Open http://144.24.114.0/"
