import logging
import os
import re
import sys

from discord_webhook import DiscordEmbed, DiscordWebhook
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from mcrcon import MCRcon
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# Setup logging with more detailed configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        # Only log to console, not to file
        logging.StreamHandler(sys.stdout)
    ],
)
logger = logging.getLogger(__name__)

# Load environment variables
logger.debug("Loading environment variables from .env file")
load_dotenv()

# Get environment variables with appropriate logging
MINECRAFT_SERVER_IP = os.getenv("MINECRAFT_SERVER_IP", "localhost")
MINECRAFT_RCON_PORT = int(os.getenv("MINECRAFT_RCON_PORT", 25585))
MINECRAFT_RCON_PASSWORD = os.getenv("MINECRAFT_RCON_PASSWORD")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
LOG_FILE_PATH = os.getenv("LOG_FILE_PATH")
SECRET_TOKEN = os.getenv("SECRET_TOKEN")
SERVER_PORT = int(os.getenv("SERVER_PORT", 25575))

# Log configuration details (omitting sensitive information)
logger.info("Configuration loaded:")
logger.info("MINECRAFT_SERVER_IP: %s", MINECRAFT_SERVER_IP)
logger.info("MINECRAFT_RCON_PORT: %s", MINECRAFT_RCON_PORT)
logger.info("RCON Password: %s", "Set" if MINECRAFT_RCON_PASSWORD else "Not set")
logger.info("Discord Webhook: %s", "Set" if DISCORD_WEBHOOK_URL else "Not set")
logger.info("Log File Path: %s", LOG_FILE_PATH)
logger.info("Secret Token: %s", "Set" if SECRET_TOKEN else "Not set")
logger.info("Server Port: %s", SERVER_PORT)

# Initialize the Flask app
logger.debug("Initializing Flask application")
app = Flask(__name__)

# Global variables
rcon_connected = False
last_log_position = 0

# Important log patterns
logger.debug("Compiling log pattern regular expressions")
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

# Compile regex patterns for efficiency
IMPORTANT_REGEX = [re.compile(pattern, re.IGNORECASE) for pattern in IMPORTANT_PATTERNS]


def is_important_log(line):
    """Check if a log line matches any important patterns."""
    logger.debug(
        "Checking if log line is important: %s",
        line[:50] + "..." if len(line) > 50 else line,
    )
    return any(pattern.search(line) for pattern in IMPORTANT_REGEX)


class MinecraftRCON:
    """Class to handle RCON connections to the Minecraft server."""

    def __init__(self, host, port, password):
        logger.debug("Initializing MinecraftRCON with host: %s, port: %s", host, port)
        self.host = host
        self.port = port
        self.password = password
        self.rcon = None

    def connect(self):
        """Connect to the Minecraft server via RCON."""
        global rcon_connected
        logger.info("Attempting to connect to RCON at %s:%s", self.host, self.port)

        try:
            logger.debug("Creating MCRcon instance")
            self.rcon = MCRcon(self.host, self.password, self.port)

            logger.debug("Establishing RCON connection")
            self.rcon.connect()

            # Test the connection with a simple command
            logger.debug("Testing RCON connection with 'list' command")
            test_response = self.rcon.command("list")
            logger.info("RCON test response: %s", test_response)

            rcon_connected = True
            logger.info("Successfully connected to Minecraft RCON")
            send_webhook_message("✅ Connected to Minecraft server RCON")
            return True
        except ConnectionRefusedError as e:
            rcon_connected = False
            logger.error("RCON connection refused: %s", str(e))
            logger.error(
                "Check if the Minecraft server is running and RCON is properly configured"
            )
            send_webhook_message(
                "❌ RCON connection refused. Is the Minecraft server running?"
            )
            return False
        except Exception as e:
            rcon_connected = False
            logger.error("RCON connection error: %s", str(e))

            # More detailed error logging for debugging
            error_type = type(e).__name__
            logger.error("Error type: %s", error_type)

            if "unpack requires a buffer of 8 bytes" in str(e):
                logger.error(
                    "This error often occurs due to connection issues or wrong credentials"
                )
                logger.error(
                    "Connection details - Host: %s, Port: %s, Password length: %d",
                    self.host,
                    self.port,
                    len(self.password),
                )

            send_webhook_message(f"❌ RCON connection error: {str(e)}")
            return False

    def disconnect(self):
        """Disconnect from the Minecraft server."""
        global rcon_connected
        logger.info("Disconnecting from Minecraft RCON")
        try:
            if self.rcon:
                self.rcon.disconnect()
                rcon_connected = False
                logger.info("Successfully disconnected from Minecraft RCON")
            else:
                logger.warning("Disconnect called but RCON was not connected")
        except Exception as e:
            logger.error("Error disconnecting from RCON: %s", str(e))

    def command(self, cmd):
        """Execute a command on the Minecraft server."""
        logger.info("Executing Minecraft command: %s", cmd)
        try:
            if not rcon_connected:
                logger.warning(
                    "Attempted to execute command while disconnected from RCON"
                )
                return "Not connected to Minecraft server"

            logger.debug("Sending command via RCON")
            response = self.rcon.command(cmd)
            logger.debug("Command response: %s", response)
            return response
        except ConnectionResetError as e:
            logger.error("Connection reset while executing command: %s", str(e))
            logger.info("Attempting to reconnect to RCON")
            self.disconnect()
            if self.connect():
                try:
                    logger.info("Reconnected, retrying command: %s", cmd)
                    response = self.rcon.command(cmd)
                    logger.debug("Command response after reconnect: %s", response)
                    return response
                except Exception as e2:
                    logger.error("Error executing command after reconnect: %s", str(e2))
                    return f"Error: {str(e2)}"
            return f"Error: Connection lost and reconnection failed"
        except Exception as e:
            logger.error("Error executing command: %s", str(e))
            return f"Error: {str(e)}"


class LogWatcher(FileSystemEventHandler):
    """Watch the Minecraft log file for changes."""

    def __init__(self, file_path):
        logger.debug("Initializing LogWatcher for file: %s", file_path)
        self.file_path = file_path
        global last_log_position

        # Initialize the log position
        try:
            if os.path.exists(file_path):
                last_log_position = os.path.getsize(file_path)
                logger.info(
                    "Initialized log watcher at position %d bytes", last_log_position
                )
            else:
                logger.error("Log file not found: %s", file_path)
                send_webhook_message(f"❌ Log file not found: {file_path}")
        except Exception as e:
            logger.error("Error initializing log watcher: %s", str(e))

    def on_modified(self, event):
        """Handle log file modification events."""
        if event.src_path == self.file_path:
            logger.debug("Log file modified, processing new entries")
            self.process_new_log_entries()
        else:
            logger.debug(
                "File modified but not the target log file: %s", event.src_path
            )

    def process_new_log_entries(self):
        """Process new entries in the log file."""
        global last_log_position

        try:
            current_size = os.path.getsize(self.file_path)
            logger.debug(
                "Log file current size: %d bytes, last position: %d bytes",
                current_size,
                last_log_position,
            )

            # Check if the log has been rotated
            if current_size < last_log_position:
                logger.info("Log file appears to have been rotated, resetting position")
                last_log_position = 0

            # Only process if there's new content
            if current_size > last_log_position:
                logger.debug(
                    "Reading %d new bytes from log file",
                    current_size - last_log_position,
                )
                with open(self.file_path, "r", encoding="utf-8") as file:
                    file.seek(last_log_position)
                    new_content = file.read()

                # Update the position
                last_log_position = current_size
                logger.debug("Updated last_log_position to %d", last_log_position)

                # Process new log lines
                lines = new_content.splitlines()
                logger.debug("Processing %d new log lines", len(lines))

                important_count = 0
                for line in lines:
                    if line and is_important_log(line):
                        logger.debug(
                            "Found important log entry: %s",
                            line[:50] + "..." if len(line) > 50 else line,
                        )
                        send_webhook_message(f"```{line}```")
                        important_count += 1

                if important_count > 0:
                    logger.info(
                        "Sent %d important log entries to Discord", important_count
                    )
                else:
                    logger.debug("No important log entries found in new content")

        except Exception as e:
            logger.error("Error processing log file: %s", str(e))
            logger.exception("Detailed traceback:")


def send_webhook_message(content, embed=None):
    """Send a message to the Discord webhook."""
    if not DISCORD_WEBHOOK_URL:
        logger.warning(
            "Attempted to send webhook message but DISCORD_WEBHOOK_URL is not set"
        )
        return

    logger.debug(
        "Sending Discord webhook message: %s",
        content[:50] + "..." if len(content) > 50 else content,
    )
    try:
        webhook = DiscordWebhook(
            url=DISCORD_WEBHOOK_URL,
            username="Minecraft Server Terminal",
            avatar_url="https://www.minecraft.net/etc.clientlibs/minecraft/clientlibs/main/resources/img/minecraft-logo.png",
            content=content,
        )

        if embed:
            logger.debug("Adding embed to webhook message")
            webhook.add_embed(embed)

        response = webhook.execute()
        if response.status_code == 204:
            logger.debug("Webhook message sent successfully")
        else:
            logger.warning(
                "Webhook returned unexpected status code: %d", response.status_code
            )
    except Exception as e:
        logger.error("Error sending webhook message: %s", str(e))


def get_recent_logs(lines=10):
    """Get the most recent lines from the log file."""
    logger.info("Retrieving %d recent log lines", lines)
    try:
        if not os.path.exists(LOG_FILE_PATH):
            logger.error("Log file not found: %s", LOG_FILE_PATH)
            return f"Log file not found: {LOG_FILE_PATH}"

        logger.debug("Reading log file: %s", LOG_FILE_PATH)
        with open(LOG_FILE_PATH, "r", encoding="utf-8") as file:
            log_lines = file.readlines()

        # Get the last N lines
        result = "".join(log_lines[-lines:])
        logger.debug("Retrieved %d lines from log file", min(lines, len(log_lines)))
        return result
    except Exception as e:
        logger.error("Error getting recent logs: %s", str(e))
        return f"Error reading logs: {str(e)}"


# API authentication middleware
def verify_token():
    """Verify the secret token in the request header."""
    token = request.headers.get("X-Secret-Token")
    result = token == SECRET_TOKEN
    if not result:
        logger.warning("API request with invalid token received")
    return result


# Flask routes
@app.route("/command", methods=["POST"])
def handle_command():
    """Handle command execution requests."""
    logger.info("Received command execution request")

    if not verify_token():
        logger.warning("Unauthorized command request rejected")
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    if not data or "command" not in data:
        logger.warning("Received command request without 'command' field")
        return jsonify({"error": "Command is required"}), 400

    command = data["command"]
    logger.info("Executing command: %s", command)
    result = minecraft_rcon.command(command)

    logger.debug("Command result: %s", result)
    return jsonify(
        {"success": True, "result": result or "Command executed (no response)"}
    )


@app.route("/logs", methods=["GET"])
def handle_logs():
    """Handle log retrieval requests."""
    logger.info("Received logs request")

    if not verify_token():
        logger.warning("Unauthorized logs request rejected")
        return jsonify({"error": "Unauthorized"}), 401

    try:
        lines = int(request.args.get("lines", 10))
        logger.info("Retrieving %d log lines", lines)

        if lines < 1 or lines > 100:
            logger.warning("Invalid log lines parameter: %d", lines)
            return jsonify({"error": "Please request between 1 and 100 lines"}), 400

        logs = get_recent_logs(lines)
        return jsonify({"success": True, "logs": logs})
    except ValueError as e:
        logger.error("Invalid lines parameter: %s", str(e))
        return jsonify({"error": "Invalid lines parameter"}), 400


@app.route("/status", methods=["GET"])
def handle_status():
    """Handle status check requests."""
    logger.info("Received status request")

    if not verify_token():
        logger.warning("Unauthorized status request rejected")
        return jsonify({"error": "Unauthorized"}), 401

    status = {
        "success": True,
        "status": {
            "rconConnected": rcon_connected,
            "logWatcherActive": (
                os.path.exists(LOG_FILE_PATH) if LOG_FILE_PATH else False
            ),
        },
    }

    logger.debug("Status response: %s", status)
    return jsonify(status)


def start_log_watcher():
    """Start watching the log file for changes."""
    if not LOG_FILE_PATH:
        logger.error("LOG_FILE_PATH not set, cannot start log watcher")
        return None

    logger.info("Starting log watcher for: %s", LOG_FILE_PATH)

    if not os.path.exists(LOG_FILE_PATH):
        logger.error("Log file not found: %s", LOG_FILE_PATH)
        send_webhook_message(f"❌ Log file not found: {LOG_FILE_PATH}")
        return None

    try:
        log_dir = os.path.dirname(LOG_FILE_PATH)
        logger.debug("Setting up log watcher in directory: %s", log_dir)

        event_handler = LogWatcher(LOG_FILE_PATH)
        observer = Observer()
        observer.schedule(event_handler, log_dir, recursive=False)
        observer.start()

        logger.info("Log watcher started successfully")
        return observer
    except Exception as e:
        logger.error("Failed to start log watcher: %s", str(e))
        logger.exception("Detailed traceback:")
        return None


if __name__ == "__main__":
    logger.info("Starting Minecraft Server Terminal")

    # Check if required environment variables are set
    missing_vars = []

    if not MINECRAFT_RCON_PASSWORD:
        missing_vars.append("MINECRAFT_RCON_PASSWORD")
        logger.error("MINECRAFT_RCON_PASSWORD is not set")

    if not DISCORD_WEBHOOK_URL:
        missing_vars.append("DISCORD_WEBHOOK_URL")
        logger.error("DISCORD_WEBHOOK_URL is not set")

    if not SECRET_TOKEN:
        missing_vars.append("SECRET_TOKEN")
        logger.error("SECRET_TOKEN is not set")

    if missing_vars:
        logger.error(
            "Required environment variables not set: %s", ", ".join(missing_vars)
        )
        logger.error("Please check your .env file")
        sys.exit(1)

    # Initialize RCON connection
    logger.info("Initializing RCON connection to Minecraft server")
    minecraft_rcon = MinecraftRCON("141.148.217.100", 25585, "iamyourfather")

    # Connect to Minecraft server
    connection_result = minecraft_rcon.connect()
    if not connection_result:
        logger.warning("Failed to connect to Minecraft server on startup")
        logger.info("Continuing anyway - will retry on command execution")

    # Start log watcher
    observer = None
    if LOG_FILE_PATH:
        observer = start_log_watcher()
        if not observer:
            logger.warning("Log watcher failed to start")
    else:
        logger.warning("LOG_FILE_PATH not set, log watching is disabled")

    try:
        # Send startup notification
        logger.info("Sending startup notification to Discord")
        send_webhook_message("🚀 Minecraft Server Terminal is now online")

        # Start the Flask server
        logger.info("Starting Flask server on port %d", SERVER_PORT)
        app.run(host="0.0.0.0", port=SERVER_PORT)

    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except Exception as e:
        logger.error("Error during server operation: %s", str(e))
        logger.exception("Detailed traceback:")
    finally:
        logger.info("Performing cleanup tasks")

        # Disconnect RCON
        logger.info("Disconnecting from RCON")
        minecraft_rcon.disconnect()

        # Stop log watcher
        if observer:
            logger.info("Stopping log watcher")
            observer.stop()
            observer.join()

        logger.info("Sending shutdown notification to Discord")
        send_webhook_message("⚠️ Minecraft Server Terminal is shutting down")

        logger.info("Shutdown complete")
