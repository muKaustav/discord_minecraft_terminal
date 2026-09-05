import hmac
import logging
import os
import re
import sys
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse

from discord_webhook import DiscordWebhook
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from mcrcon import MCRcon
from waitress import serve
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

load_dotenv()

MINECRAFT_SERVER_IP = os.getenv("MINECRAFT_SERVER_IP", "127.0.0.1")
MINECRAFT_RCON_PORT = int(os.getenv("MINECRAFT_RCON_PORT", 25585))
MINECRAFT_RCON_PASSWORD = os.getenv("MINECRAFT_RCON_PASSWORD")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
LOG_FILE_PATH = os.getenv("LOG_FILE_PATH")
SECRET_TOKEN = os.getenv("SECRET_TOKEN")
SERVER_PORT = int(os.getenv("SERVER_PORT", 25575))
SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
MAX_COMMAND_LENGTH = 256
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 30

logger.info("Configuration loaded:")
logger.info("MINECRAFT_SERVER_IP: %s", MINECRAFT_SERVER_IP)
logger.info("MINECRAFT_RCON_PORT: %s", MINECRAFT_RCON_PORT)
logger.info("RCON Password: %s", "Set" if MINECRAFT_RCON_PASSWORD else "Not set")
logger.info("Discord Webhook: %s", "Set" if DISCORD_WEBHOOK_URL else "Not set")
logger.info("Log File Path: %s", LOG_FILE_PATH)
logger.info("Secret Token: %s", "Set" if SECRET_TOKEN else "Not set")
logger.info("Server bind: %s:%s", SERVER_HOST, SERVER_PORT)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
app.config["JSON_SORT_KEYS"] = False

rcon_connected = False
last_log_position = 0
_rate_hits = defaultdict(deque)

IMPORTANT_PATTERNS = [
    r"joined the game",
    r"left the game",
    r"Starting minecraft server",
    r"Stopping server",
    r"\[ERROR\]",
    r"SEVERE",
    r"was slain by",
    r"was killed by",
    r"Can't keep up!",
    r"issued server command",
]
IMPORTANT_REGEX = [re.compile(pattern, re.IGNORECASE) for pattern in IMPORTANT_PATTERNS]


def is_important_log(line):
    return any(pattern.search(line) for pattern in IMPORTANT_REGEX)


def is_safe_webhook_url(url):
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and host in {"discord.com", "discordapp.com"}
        and parsed.path.startswith("/api/webhooks/")
    )


def resolve_log_path(path):
    if not path:
        return None
    resolved = Path(path).expanduser().resolve()
    if resolved.suffix != ".log" or not resolved.is_file():
        return None
    if resolved.name.startswith("."):
        return None
    return resolved


def validate_command(raw):
    if not isinstance(raw, str):
        return None, "Command must be a string"
    command = raw.strip()
    if command.startswith("/"):
        command = command[1:]
    if not command or len(command) > MAX_COMMAND_LENGTH:
        return None, "Invalid command"
    if any(ord(char) < 32 for char in command):
        return None, "Invalid command"
    return command, None


def rate_limited(ip):
    now = time.monotonic()
    hits = _rate_hits[ip]
    while hits and now - hits[0] > RATE_LIMIT_WINDOW:
        hits.popleft()
    if len(hits) >= RATE_LIMIT_MAX:
        return True
    hits.append(now)
    return False


def tail_lines(path, count):
    """Read the last N lines without loading the whole log into memory."""
    newline = b"\n"
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        block = 4096
        data = b""
        while size > 0 and data.count(newline) <= count:
            step = min(block, size)
            size -= step
            handle.seek(size)
            data = handle.read(step) + data
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()[-count:]
    return "\n".join(lines)


class MinecraftRCON:
    def __init__(self, host, port, password):
        self.host = host
        self.port = port
        self.password = password
        self.rcon = None

    def connect(self):
        global rcon_connected
        logger.info("Attempting to connect to RCON at %s:%s", self.host, self.port)
        try:
            self.rcon = MCRcon(self.host, self.password, self.port)
            self.rcon.connect()
            test_response = self.rcon.command("list")
            logger.info("RCON test succeeded")
            logger.debug("RCON test response: %s", test_response)
            rcon_connected = True
            send_webhook_message("✅ Connected to Minecraft server RCON")
            return True
        except ConnectionRefusedError:
            rcon_connected = False
            logger.error("RCON connection refused")
            send_webhook_message(
                "❌ RCON connection refused. Is the Minecraft server running?"
            )
            return False
        except Exception:
            rcon_connected = False
            logger.exception("RCON connection error")
            send_webhook_message("❌ RCON connection error")
            return False

    def disconnect(self):
        global rcon_connected
        try:
            if self.rcon:
                self.rcon.disconnect()
            rcon_connected = False
            logger.info("Disconnected from Minecraft RCON")
        except Exception:
            logger.exception("Error disconnecting from RCON")
            rcon_connected = False

    def command(self, cmd):
        logger.info("Executing Minecraft command: %s", cmd)
        try:
            if not rcon_connected or not self.rcon:
                return "Not connected to Minecraft server"
            return self.rcon.command(cmd)
        except ConnectionResetError:
            logger.warning("RCON connection reset, reconnecting")
            self.disconnect()
            if self.connect():
                try:
                    return self.rcon.command(cmd)
                except Exception:
                    logger.exception("Command failed after reconnect")
                    return "Error: command failed after reconnect"
            return "Error: Connection lost and reconnection failed"
        except Exception:
            logger.exception("Error executing command")
            return "Error: command failed"


class LogWatcher(FileSystemEventHandler):
    def __init__(self, file_path):
        global last_log_position
        self.file_path = str(file_path)
        self.resolved_path = Path(file_path).resolve()
        try:
            last_log_position = self.resolved_path.stat().st_size
            logger.info(
                "Initialized log watcher at position %d bytes", last_log_position
            )
        except OSError:
            logger.exception("Error initializing log watcher")
            send_webhook_message("❌ Log file could not be opened")

    def _is_target(self, src_path):
        try:
            return Path(src_path).resolve() == self.resolved_path
        except OSError:
            return os.path.abspath(src_path) == os.path.abspath(self.file_path)

    def on_modified(self, event):
        if not event.is_directory and self._is_target(event.src_path):
            self.process_new_log_entries()

    def process_new_log_entries(self):
        global last_log_position
        try:
            current_size = os.path.getsize(self.file_path)
            if current_size < last_log_position:
                last_log_position = 0
            if current_size <= last_log_position:
                return
            with open(self.file_path, "r", encoding="utf-8", errors="replace") as file:
                file.seek(last_log_position)
                new_content = file.read()
            last_log_position = current_size
            important_count = 0
            for line in new_content.splitlines():
                if line and is_important_log(line):
                    safe_line = line.replace("`", "'")[:1800]
                    send_webhook_message("```" + safe_line + "```")
                    important_count += 1
            if important_count:
                logger.info(
                    "Sent %d important log entries to Discord", important_count
                )
        except Exception:
            logger.exception("Error processing log file")


def send_webhook_message(content, embed=None):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        webhook = DiscordWebhook(
            url=DISCORD_WEBHOOK_URL,
            username="Minecraft Server Terminal",
            content=content[:1900],
            timeout=10,
        )
        if embed:
            webhook.add_embed(embed)
        response = webhook.execute()
        if response.status_code not in (200, 204):
            logger.warning(
                "Webhook returned unexpected status code: %d", response.status_code
            )
    except Exception:
        logger.exception("Error sending webhook message")


def get_recent_logs(lines=10):
    log_path = resolve_log_path(LOG_FILE_PATH)
    if log_path is None:
        return "Log file is not available"
    try:
        return tail_lines(log_path, lines)
    except OSError:
        logger.exception("Error getting recent logs")
        return "Error reading logs"


def verify_token():
    provided = request.headers.get("X-Secret-Token", "")
    expected = SECRET_TOKEN or ""
    if not expected:
        return False
    if hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        return True
    logger.warning("API request with invalid token received")
    return False


@app.before_request
def protect_api():
    if rate_limited(request.remote_addr or "unknown"):
        return jsonify({"error": "Too many requests"}), 429
    if not verify_token():
        return jsonify({"error": "Unauthorized"}), 401
    return None


@app.route("/command", methods=["POST"])
def handle_command():
    data = request.get_json(silent=True) or {}
    command, error = validate_command(data.get("command"))
    if error:
        return jsonify({"error": error}), 400
    result = minecraft_rcon.command(command)
    return jsonify(
        {"success": True, "result": result or "Command executed (no response)"}
    )


@app.route("/logs", methods=["GET"])
def handle_logs():
    try:
        lines = int(request.args.get("lines", 10))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid lines parameter"}), 400
    if lines < 1 or lines > 100:
        return jsonify({"error": "Please request between 1 and 100 lines"}), 400
    return jsonify({"success": True, "logs": get_recent_logs(lines)})


@app.route("/status", methods=["GET"])
def handle_status():
    log_path = resolve_log_path(LOG_FILE_PATH)
    return jsonify(
        {
            "success": True,
            "status": {
                "rconConnected": rcon_connected,
                "logWatcherActive": log_path is not None,
            },
        }
    )


def start_log_watcher():
    log_path = resolve_log_path(LOG_FILE_PATH)
    if log_path is None:
        logger.error("LOG_FILE_PATH is missing or not a safe .log file")
        send_webhook_message("❌ Log file not found")
        return None
    try:
        observer = Observer()
        observer.schedule(LogWatcher(log_path), str(log_path.parent), recursive=False)
        observer.start()
        logger.info("Log watcher started for %s", log_path)
        return observer
    except Exception:
        logger.exception("Failed to start log watcher")
        return None


if __name__ == "__main__":
    logger.info("Starting Minecraft Server Terminal")
    missing_vars = []
    if not MINECRAFT_RCON_PASSWORD:
        missing_vars.append("MINECRAFT_RCON_PASSWORD")
    if not DISCORD_WEBHOOK_URL:
        missing_vars.append("DISCORD_WEBHOOK_URL")
    if not SECRET_TOKEN:
        missing_vars.append("SECRET_TOKEN")
    if missing_vars:
        logger.error(
            "Required environment variables not set: %s", ", ".join(missing_vars)
        )
        sys.exit(1)
    if len(SECRET_TOKEN) < 16:
        logger.error("SECRET_TOKEN is too short")
        sys.exit(1)
    if not is_safe_webhook_url(DISCORD_WEBHOOK_URL):
        logger.error("DISCORD_WEBHOOK_URL must be an https Discord webhook URL")
        sys.exit(1)
    if SERVER_HOST not in {"127.0.0.1", "localhost", "::1"}:
        logger.warning(
            "SERVER_HOST=%s binds beyond loopback. Restrict security group access.",
            SERVER_HOST,
        )

    minecraft_rcon = MinecraftRCON(
        MINECRAFT_SERVER_IP, MINECRAFT_RCON_PORT, MINECRAFT_RCON_PASSWORD
    )
    if not minecraft_rcon.connect():
        logger.warning("Failed to connect to Minecraft server on startup")

    observer = start_log_watcher() if LOG_FILE_PATH else None
    try:
        send_webhook_message("🚀 Minecraft Server Terminal is now online")
        logger.info("Starting HTTP server on %s:%d", SERVER_HOST, SERVER_PORT)
        serve(app, host=SERVER_HOST, port=SERVER_PORT, ident="rlcraft-terminal")
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except Exception:
        logger.exception("Error during server operation")
    finally:
        minecraft_rcon.disconnect()
        if observer:
            observer.stop()
            observer.join()
        send_webhook_message("⚠️ Minecraft Server Terminal is shutting down")
        logger.info("Shutdown complete")
