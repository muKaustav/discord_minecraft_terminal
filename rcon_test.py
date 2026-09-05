import os
from ipaddress import ip_address

from dotenv import load_dotenv
from mcrcon import MCRcon

load_dotenv()

host = os.getenv("MINECRAFT_SERVER_IP", "127.0.0.1")
port = int(os.getenv("MINECRAFT_RCON_PORT", 25585))
password = os.getenv("MINECRAFT_RCON_PASSWORD")

if not password:
    raise SystemExit("MINECRAFT_RCON_PASSWORD is not set")

try:
    parsed = ip_address(host)
    if not parsed.is_loopback:
        raise SystemExit("rcon_test.py only connects to loopback addresses")
except ValueError:
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit("rcon_test.py only connects to loopback addresses")

try:
    print(f"Connecting to RCON at {host}:{port}...")
    with MCRcon(host, password, port) as mcr:
        print("Connected! Sending 'list' command...")
        resp = mcr.command("list")
        print(f"Response: {resp}")
    print("Disconnected successfully")
except Exception:
    raise SystemExit("RCON test failed")
