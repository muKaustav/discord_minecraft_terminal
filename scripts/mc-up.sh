#!/usr/bin/env bash
set -euo pipefail

export PATH="${HOME}/.local/bin:/usr/local/bin:/usr/bin:${PATH:-}"

INSTANCE_ID="${RLCRAFT_INSTANCE_ID:-i-050606336b43db88f}"
REGION="${AWS_REGION:-ap-south-1}"
PROFILE="${AWS_PROFILE:-default}"
MC_PORT="${MC_PORT:-25565}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WSL_SSH_CONFIG="${HOME}/.ssh/config"
STATUS_JS="${SCRIPT_DIR}/check-status.js"

windows_ssh_config() {
  local candidate winuser
  if [[ -n "${WIN_SSH_CONFIG:-}" ]]; then
    printf '%s\n' "${WIN_SSH_CONFIG}"
    return 0
  fi
  if [[ -n "${USERPROFILE:-}" ]]; then
    candidate="$(wslpath "${USERPROFILE}\\.ssh\\config" 2>/dev/null || true)"
    if [[ -n "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  fi
  winuser="$(cmd.exe /c "echo %USERNAME%" 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
  if [[ -n "${winuser}" && -d "/mnt/c/Users/${winuser}" ]]; then
    printf '%s\n' "/mnt/c/Users/${winuser}/.ssh/config"
    return 0
  fi
  if [[ -f /mnt/c/Users/Admin/.ssh/config ]]; then
    printf '%s\n' "/mnt/c/Users/Admin/.ssh/config"
  fi
}

aws_cmd() {
  aws --profile "${PROFILE}" --region "${REGION}" "$@"
}

update_ssh_host() {
  local config_file="$1"
  local ip="$2"
  if [[ -z "${config_file}" ]]; then
    return 0
  fi
  local dir
  dir="$(dirname "${config_file}")"
  if [[ ! -f "${config_file}" ]]; then
    if [[ ! -d "${dir}" ]] && ! mkdir -p "${dir}" 2>/dev/null; then
      echo "Skipping SSH config ${config_file}"
      return 0
    fi
    cat > "${config_file}" <<EOF
Host mc
    HostName ${ip}
    User ubuntu
    IdentityFile ~/.ssh/rlcraft-server.pem
    IdentitiesOnly yes
EOF
    return 0
  fi
  python3 - "${config_file}" "${ip}" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
ip = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
out = []
in_mc = False
replaced = False
for line in lines:
    stripped = line.strip()
    lower = stripped.lower()
    if lower.startswith("host ") and not lower.startswith("hostname"):
        names = stripped.split()[1:]
        in_mc = "mc" in names
    if in_mc and lower.startswith("hostname"):
        indent = line[: len(line) - len(line.lstrip())]
        if line.endswith("\r\n"):
            nl = "\r\n"
        elif line.endswith("\n"):
            nl = "\n"
        else:
            nl = ""
        out.append(f"{indent}HostName {ip}{nl}")
        replaced = True
        continue
    out.append(line)
if not replaced:
    out.append(
        f"\nHost mc\n    HostName {ip}\n    User ubuntu\n    IdentityFile ~/.ssh/rlcraft-server.pem\n    IdentitiesOnly yes\n"
    )
path.write_text("".join(out), encoding="utf-8")
PY
}

WIN_SSH_CONFIG_RESOLVED="$(windows_ssh_config || true)"

echo "Checking EC2 ${INSTANCE_ID} in ${REGION}..."
STATE="$(aws_cmd ec2 describe-instances --instance-ids "${INSTANCE_ID}" --query 'Reservations[0].Instances[0].State.Name' --output text)"
echo "Current state: ${STATE}"

if [[ "${STATE}" == "stopping" ]]; then
  echo "Waiting for instance to finish stopping..."
  aws_cmd ec2 wait instance-stopped --instance-ids "${INSTANCE_ID}"
  STATE="stopped"
fi

if [[ "${STATE}" == "stopped" ]]; then
  echo "Starting instance..."
  aws_cmd ec2 start-instances --instance-ids "${INSTANCE_ID}" >/dev/null
fi

if [[ "${STATE}" != "running" ]]; then
  echo "Waiting until instance is running..."
  aws_cmd ec2 wait instance-running --instance-ids "${INSTANCE_ID}"
fi

IP="$(aws_cmd ec2 describe-instances --instance-ids "${INSTANCE_ID}" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)"
if [[ -z "${IP}" || "${IP}" == "None" ]]; then
  echo "No public IP yet, waiting 10s..."
  sleep 10
  IP="$(aws_cmd ec2 describe-instances --instance-ids "${INSTANCE_ID}" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)"
fi
echo "Public IP: ${IP}"

update_ssh_host "${WIN_SSH_CONFIG_RESOLVED}" "${IP}"
update_ssh_host "${WSL_SSH_CONFIG}" "${IP}"
echo "Updated SSH host 'mc' to ${IP}"

echo "Waiting for Minecraft via mcstatus..."
if [[ ! -f "${SCRIPT_DIR}/node_modules/node-mcstatus/package.json" ]]; then
  (cd "${SCRIPT_DIR}" && npm install --omit=dev >/dev/null)
fi

for i in $(seq 1 60); do
  if node "${STATUS_JS}" "${IP}" "${MC_PORT}" >/tmp/mc-up-status.json 2>/dev/null; then
    cat /tmp/mc-up-status.json
    echo
    echo "RLCraft is up. Connect at ${IP}:${MC_PORT}"
    echo "SSH: ssh mc"
    echo "Discord bot starts on the VM with the server."
    exit 0
  fi
  echo "wait-${i}: Minecraft not ready yet"
  sleep 10
done

echo "Instance is running at ${IP}, but Minecraft did not answer mcstatus in time."
echo "SSH with: ssh mc"
exit 2
