import json
import logging
import os
import re
import time

from discord_webhook import DiscordEmbed, DiscordWebhook
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from mcrcon import MCRcon
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
MINECRAFT_SERVER_IP = os.getenv('MINECRAFT_SERVER_IP', 'localhost')
MINECRAFT_RCON_PORT = int(os.getenv('MINECRAFT_RCON_PORT', 25575))
MINECRAFT_RCON_PASSWORD = os.getenv('MINECRAFT_RCON_PASSWORD')
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')
LOG_FILE_PATH = os.getenv('LOG_FILE_PATH')
SECRET_TOKEN = os.getenv('SECRET_TOKEN')
SERVER_PORT = int(os.getenv('SERVER_PORT', 3000))

# Initialize the Flask app
app = Flask(__name__)

# Global variables
rcon_connected = False
last_log_position = 0

# Important log patterns
IMPORTANT_PATTERNS = [
    r'joined the game',
    r'left the game',
    r'Starting minecraft server',
    r'Stopping server',
    r'\[ERROR\]',
    r'SEVERE',
    r'was slain by',
    r'was killed by',
    r"Can't keep up!",
    r'issued server command'
]

# Compile regex patterns for efficiency
IMPORTANT_REGEX = [re.compile(pattern, re.IGNORECASE) for pattern in IMPORTANT_PATTERNS]

def is_important_log(line):
    """Check if a log line matches any important patterns."""
    return any(pattern.search(line) for pattern in IMPORTANT_REGEX)

class MinecraftRCON:
    """Class to handle RCON connections to the Minecraft server."""
    def __init__(self, host, port, password):
        self.host = host
        self.port = port
        self.password = password
        self.rcon = None
    
    def connect(self):
        """Connect to the Minecraft server via RCON."""
        global rcon_connected
        try:
            self.rcon = MCRcon(self.host, self.password, self.port)
            self.rcon.connect()
            rcon_connected = True
            logger.info("Connected to Minecraft RCON")
            send_webhook_message("✅ Connected to Minecraft server RCON")
            return True
        except Exception as e:
            rcon_connected = False
            logger.error(f"RCON connection error: {e}")
            send_webhook_message(f"❌ RCON connection error: {str(e)}")
            return False
    
    def disconnect(self):
        """Disconnect from the Minecraft server."""
        global rcon_connected
        try:
            if self.rcon:
                self.rcon.disconnect()
                rcon_connected = False
                logger.info("Disconnected from Minecraft RCON")
        except Exception as e:
            logger.error(f"Error disconnecting from RCON: {e}")
    
    def command(self, cmd):
        """Execute a command on the Minecraft server."""
        try:
            if not rcon_connected:
                return "Not connected to Minecraft server"
            
            response = self.rcon.command(cmd)
            return response
        except Exception as e:
            logger.error(f"Error executing command: {e}")
            # Try to reconnect
            self.disconnect()
            if self.connect():
                try:
                    # Retry the command after reconnecting
                    response = self.rcon.command(cmd)
                    return response
                except Exception as e2:
                    logger.error(f"Error executing command after reconnect: {e2}")
                    return f"Error: {str(e2)}"
            return f"Error: {str(e)}"

class LogWatcher(FileSystemEventHandler):
    """Watch the Minecraft log file for changes."""
    def __init__(self, file_path):
        self.file_path = file_path
        global last_log_position
        
        # Initialize the log position
        try:
            if os.path.exists(file_path):
                last_log_position = os.path.getsize(file_path)
                logger.info(f"Initialized log watcher at position {last_log_position}")
            else:
                logger.error(f"Log file not found: {file_path}")
                send_webhook_message(f"❌ Log file not found: {file_path}")
        except Exception as e:
            logger.error(f"Error initializing log watcher: {e}")
    
    def on_modified(self, event):
        """Handle log file modification events."""
        if event.src_path == self.file_path:
            self.process_new_log_entries()
    
    def process_new_log_entries(self):
        """Process new entries in the log file."""
        global last_log_position
        
        try:
            current_size = os.path.getsize(self.file_path)
            
            # Check if the log has been rotated
            if current_size < last_log_position:
                logger.info("Log file rotated, resetting position")
                last_log_position = 0
            
            # Only process if there's new content
            if current_size > last_log_position:
                with open(self.file_path, 'r', encoding='utf-8') as file:
                    file.seek(last_log_position)
                    new_content = file.read()
                
                # Update the position
                last_log_position = current_size
                
                # Process new log lines
                for line in new_content.splitlines():
                    if line and is_important_log(line):
                        send_webhook_message(f"```{line}```")
        
        except Exception as e:
            logger.error(f"Error processing log file: {e}")

def send_webhook_message(content, embed=None):
    """Send a message to the Discord webhook."""
    try:
        webhook = DiscordWebhook(
            url=DISCORD_WEBHOOK_URL,
            username="Minecraft Server Terminal",
            avatar_url="https://www.minecraft.net/etc.clientlibs/minecraft/clientlibs/main/resources/img/minecraft-logo.png",
            content=content
        )
        
        if embed:
            webhook.add_embed(embed)
        
        webhook.execute()
    except Exception as e:
        logger.error(f"Error sending webhook message: {e}")

def get_recent_logs(lines=10):
    """Get the most recent lines from the log file."""
    try:
        if not os.path.exists(LOG_FILE_PATH):
            return f"Log file not found: {LOG_FILE_PATH}"
        
        with open(LOG_FILE_PATH, 'r', encoding='utf-8') as file:
            log_lines = file.readlines()
        
        # Get the last N lines
        return ''.join(log_lines[-lines:])
    except Exception as e:
        logger.error(f"Error getting recent logs: {e}")
        return f"Error reading logs: {str(e)}"

# API authentication middleware
def verify_token():
    """Verify the secret token in the request header."""
    token = request.headers.get('X-Secret-Token')
    return token == SECRET_TOKEN

# Flask routes
@app.route('/command', methods=['POST'])
def handle_command():
    """Handle command execution requests."""
    if not verify_token():
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    if not data or 'command' not in data:
        return jsonify({'error': 'Command is required'}), 400
    
    command = data['command']
    result = minecraft_rcon.command(command)
    
    return jsonify({
        'success': True,
        'result': result or "Command executed (no response)"
    })

@app.route('/logs', methods=['GET'])
def handle_logs():
    """Handle log retrieval requests."""
    if not verify_token():
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        lines = int(request.args.get('lines', 10))
        if lines < 1 or lines > 100:
            return jsonify({'error': 'Please request between 1 and 100 lines'}), 400
        
        logs = get_recent_logs(lines)
        return jsonify({'success': True, 'logs': logs})
    except ValueError:
        return jsonify({'error': 'Invalid lines parameter'}), 400

@app.route('/status', methods=['GET'])
def handle_status():
    """Handle status check requests."""
    if not verify_token():
        return jsonify({'error': 'Unauthorized'}), 401
    
    return jsonify({
        'success': True,
        'status': {
            'rconConnected': rcon_connected,
            'logWatcherActive': os.path.exists(LOG_FILE_PATH)
        }
    })

def start_log_watcher():
    """Start watching the log file for changes."""
    if not os.path.exists(LOG_FILE_PATH):
        logger.error(f"Log file not found: {LOG_FILE_PATH}")
        send_webhook_message(f"❌ Log file not found: {LOG_FILE_PATH}")
        return
    
    event_handler = LogWatcher(LOG_FILE_PATH)
    observer = Observer()
    observer.schedule(event_handler, os.path.dirname(LOG_FILE_PATH), recursive=False)
    observer.start()
    logger.info(f"Started log watcher for {LOG_FILE_PATH}")
    
    return observer

if __name__ == "__main__":
    # Check if required environment variables are set
    if not MINECRAFT_RCON_PASSWORD:
        logger.error("MINECRAFT_RCON_PASSWORD is not set. Please check your .env file.")
        exit(1)
    
    if not DISCORD_WEBHOOK_URL:
        logger.error("DISCORD_WEBHOOK_URL is not set. Please check your .env file.")
        exit(1)
    
    if not SECRET_TOKEN:
        logger.error("SECRET_TOKEN is not set. Please check your .env file.")
        exit(1)
    
    # Initialize RCON connection
    minecraft_rcon = MinecraftRCON(
        MINECRAFT_SERVER_IP,
        MINECRAFT_RCON_PORT,
        MINECRAFT_RCON_PASSWORD
    )
    
    # Connect to Minecraft server
    minecraft_rcon.connect()
    
    # Start log watcher
    observer = start_log_watcher()
    
    try:
        # Send startup notification
        send_webhook_message("🚀 Minecraft Server Terminal is now online")
        
        # Start the Flask server
        logger.info(f"Starting server on port {SERVER_PORT}")
        app.run(host='0.0.0.0', port=SERVER_PORT)
    
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        
        # Disconnect RCON
        minecraft_rcon.disconnect()
        
        # Stop log watcher
        if observer:
            observer.stop()
            observer.join()
        
        send_webhook_message("⚠️ Minecraft Server Terminal is shutting down")