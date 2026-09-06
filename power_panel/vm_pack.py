"""The unprivileged server-pack launcher and safe archive installer run on EC2."""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path("/opt/minecraft-booter")
PACKS = ROOT / "packs"
CURRENT = ROOT / "current"
SERVICE = "minecraft-server.service"
IDLE_SERVICE = "rlcraft-idle-stop.service"
RCON_ENV = Path("/opt/rlcraft-discord/.env")
CURSEFORGE_ENV = ROOT / "curseforge.env"
MAX_UNPACKED_BYTES = 80 * 1024 * 1024 * 1024


def detect_launcher(pack_dir: Path) -> tuple[str, str]:
    for name in ("start.sh", "run.sh", "startserver.sh"):
        if (pack_dir / name).is_file():
            return "shell", name
    for name in ("fabric-server-launch.jar", "server.jar"):
        if (pack_dir / name).is_file():
            return "java", name
    for pattern in ("neoforge-*.jar", "forge-*.jar"):
        matches = sorted(pack_dir.glob(pattern))
        if matches:
            return "java", matches[-1].name
    raise ValueError("Unsupported server-pack layout: no known Forge, NeoForge, or Fabric launcher was found.")


def required_java_major(minecraft_version: str) -> int:
    parts = minecraft_version.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return 21
    if major >= 22:
        return major - 1
    if major != 1:
        return 21
    if minor <= 16:
        return 8
    if minor == 17:
        return 16
    if minor < 20 or (minor == 20 and patch <= 4):
        return 17
    return 21


def _java_binary(required_major: int) -> Path:
    candidates = list(Path("/usr/lib/jvm").glob("*/bin/java"))
    candidates.append(Path("/usr/bin/java"))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        completed = subprocess.run(
            [str(candidate), "-version"], capture_output=True, text=True, check=False,
        )
        version_text = completed.stderr or completed.stdout
        if f'"{required_major}.' in version_text or f'"1.{required_major}.' in version_text:
            return candidate.resolve()
    raise ValueError(f"Java {required_major} is required for this Minecraft version but is not installed.")


def _safe_member(info: zipfile.ZipInfo) -> bool:
    path = PurePosixPath(info.filename.replace("\\", "/"))
    unix_mode = info.external_attr >> 16
    return not path.is_absolute() and ".." not in path.parts and not stat.S_ISLNK(unix_mode)


def extract_zip_safely(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as zip_file:
        infos = zip_file.infolist()
        if not infos or any(not _safe_member(info) for info in infos):
            raise ValueError("The server-pack archive has an unsafe path or symlink.")
        unpacked_size = sum(info.file_size for info in infos)
        free_space = shutil.disk_usage(destination.parent).free
        if unpacked_size > MAX_UNPACKED_BYTES or unpacked_size + 5 * 1024 * 1024 * 1024 > free_space:
            raise ValueError("There is not enough free disk space for this server pack.")
        zip_file.extractall(destination)


def _load_rcon_password() -> str | None:
    if not RCON_ENV.is_file():
        return None
    for line in RCON_ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("MINECRAFT_RCON_PASSWORD="):
            return line.partition("=")[2].strip()
    return None


def _load_curseforge_key() -> str:
    if not CURSEFORGE_ENV.is_file():
        raise ValueError("CurseForge API access is not configured on the Minecraft VM.")
    for line in CURSEFORGE_ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("CURSEFORGE_API_KEY="):
            key = line.partition("=")[2].strip()
            if key:
                return key
    raise ValueError("CurseForge API access is not configured on the Minecraft VM.")


def _configure_rcon(pack_dir: Path) -> None:
    password = _load_rcon_password()
    if not password:
        raise ValueError("The existing RCON password is unavailable; server-pack installation cannot continue.")
    properties = pack_dir / "server.properties"
    existing = {}
    if properties.exists():
        for line in properties.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                existing[key] = value
    existing.update({"enable-rcon": "true", "rcon.port": "25575", "rcon.password": password})
    properties.write_text("\n".join(f"{key}={value}" for key, value in existing.items()) + "\n", encoding="utf-8")


def _write_launcher(pack_dir: Path, manifest: dict) -> None:
    kind, target = detect_launcher(pack_dir)
    java = _java_binary(required_java_major(manifest.get("minecraft_version", "")))
    launcher = pack_dir / "minecraft-booter-start.sh"
    if kind == "shell":
        command = f'exec /usr/bin/env bash "{pack_dir / target}"'
    else:
        command = f'exec "{java}" -jar "{pack_dir / target}" nogui'
    launcher.write_text(
        f"#!/usr/bin/env bash\nset -euo pipefail\nexport PATH=\"{java.parent}:$PATH\"\n{command}\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    (pack_dir / "minecraft-booter.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _give_pack_to_server_user(pack_dir: Path) -> None:
    import pwd

    user = pwd.getpwnam("ubuntu")
    for path in (pack_dir, *pack_dir.rglob("*")):
        os.chown(path, user.pw_uid, user.pw_gid)


def install(manifest: dict) -> Path:
    url = manifest.get("server_download_url", "")
    if not url.startswith("https://"):
        raise ValueError("CurseForge did not return a secure server-pack download URL.")
    server_file_id = int(manifest["server_file_id"])
    final_dir = PACKS / str(server_file_id)
    if final_dir.exists():
        return final_dir
    ROOT.mkdir(mode=0o755, parents=True, exist_ok=True)
    PACKS.mkdir(mode=0o755, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=PACKS, prefix="staging-") as temp_dir:
        temp_path = Path(temp_dir)
        archive = temp_path / "server-pack.zip"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Minecraft-Booter/1.0", "x-api-key": _load_curseforge_key()},
        )
        with urllib.request.urlopen(request, timeout=90) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        staging = temp_path / "pack"
        staging.mkdir()
        extract_zip_safely(archive, staging)
        _configure_rcon(staging)
        _write_launcher(staging, manifest)
        _give_pack_to_server_user(staging)
        shutil.move(str(staging), str(final_dir))
    return final_dir


def _activate_path(pack_id: int) -> Path:
    pack_dir = PACKS / str(pack_id)
    if not pack_dir.exists():
        raise ValueError("The selected server pack is not installed on the Minecraft VM.")
    return pack_dir.resolve()


def _set_current(pack_id: int) -> None:
    pack_dir = _activate_path(pack_id)
    replacement = ROOT / ".current-next"
    replacement.unlink(missing_ok=True)
    replacement.symlink_to(pack_dir)
    replacement.replace(CURRENT)


def _systemctl(*args: str) -> None:
    subprocess.run(["systemctl", *args], check=True, text=True)


def _rcon(command: str) -> None:
    """Send an RCON command through the existing local RCON configuration."""
    password = _load_rcon_password()
    if not password:
        return
    import socket
    import struct

    def packet(request_id: int, kind: int, body: str) -> bytes:
        payload = struct.pack("<ii", request_id, kind) + body.encode() + b"\x00\x00"
        return struct.pack("<i", len(payload)) + payload

    with socket.create_connection(("127.0.0.1", 25575), timeout=3) as sock:
        sock.sendall(packet(1, 3, password))
        sock.recv(4096)
        sock.sendall(packet(2, 2, command))


def switch(pack_id: int) -> None:
    _systemctl("stop", IDLE_SERVICE)
    try:
        for seconds in (60, 30, 10):
            try:
                _rcon(f"say Minecraft Booter: switching modpacks in {seconds} seconds.")
            except OSError:
                break
            time.sleep(30 if seconds == 60 else 20 if seconds == 30 else 10)
        try:
            _rcon("save-all")
        except OSError:
            pass
        _systemctl("stop", SERVICE)
        _set_current(pack_id)
        _systemctl("start", SERVICE)
    finally:
        _systemctl("start", IDLE_SERVICE)


def activate(pack_id: int) -> None:
    _set_current(pack_id)
    _systemctl("start", SERVICE)


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: pack_manager.py install|activate|switch")
    action = sys.argv[1]
    if action == "install":
        manifest = json.load(sys.stdin)
        print(f"installed={install(manifest)}")
    elif action == "activate" and len(sys.argv) == 3:
        activate(int(sys.argv[2]))
        print("activated")
    elif action == "switch" and len(sys.argv) == 3:
        switch(int(sys.argv[2]))
        print("switched")
    else:
        raise SystemExit("usage: pack_manager.py install|activate|switch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
