import asyncio
import logging
import os
import sys

import discord
import requests
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# Setup logging
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

# Get configuration values
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
WEBHOOK_SERVER_URL = os.getenv("WEBHOOK_SERVER_URL")
SECRET_TOKEN = os.getenv("SECRET_TOKEN")
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID")) if os.getenv("ADMIN_ROLE_ID") else None

# Log configuration (without revealing sensitive information)
logger.info("Configuration loaded:")
logger.info("DISCORD_TOKEN: %s", "Set" if DISCORD_TOKEN else "Not set")
logger.info("DISCORD_GUILD_ID: %s", DISCORD_GUILD_ID)
logger.info("WEBHOOK_SERVER_URL: %s", WEBHOOK_SERVER_URL)
logger.info("SECRET_TOKEN: %s", "Set" if SECRET_TOKEN else "Not set")
logger.info("ADMIN_ROLE_ID: %s", ADMIN_ROLE_ID)

# Initialize Discord client
logger.debug("Initializing Discord client")
intents = discord.Intents.default()
client = commands.Bot(command_prefix="!", intents=intents)


# Format code blocks for Discord
def format_code_blocks(text):
    """Format text into code blocks, splitting if necessary."""
    logger.debug("Formatting text as code blocks")
    if not text:
        logger.debug("Empty text provided for formatting")
        return ["No output"]

    # If text is too long, split it
    if len(text) > 1900:
        logger.debug("Text exceeds 1900 characters, splitting into chunks")
        chunks = []
        for i in range(0, len(text), 1900):
            chunks.append(f"```\n{text[i:i+1900]}\n```")
        logger.debug("Split text into %d chunks", len(chunks))
        return chunks

    logger.debug("Returning single code block")
    return [f"```\n{text}\n```"]


# Check if user has admin role
def has_admin_role(member):
    """Check if a member has the admin role."""
    logger.debug("Checking if user %s has admin role", member.name)

    if not ADMIN_ROLE_ID:
        logger.warning("ADMIN_ROLE_ID not set, denying access by default")
        return False

    has_role = discord.utils.get(member.roles, id=ADMIN_ROLE_ID) is not None
    logger.debug("User %s admin role check result: %s", member.name, has_role)
    return has_role


@client.event
async def on_ready():
    """Called when the client is connected to Discord."""
    logger.info("Bot is now logged in as %s (%s)", client.user.name, client.user.id)
    logger.info("Connected to %d guilds", len(client.guilds))

    for guild in client.guilds:
        logger.debug("Connected to guild: %s (%s)", guild.name, guild.id)

    await register_commands()


async def register_commands():
    """Register slash commands with Discord."""
    logger.info("Registering slash commands")

    try:
        # Sync commands with Discord
        if DISCORD_GUILD_ID:
            guild = discord.Object(id=int(DISCORD_GUILD_ID))
            logger.debug("Copying global commands to guild %s", DISCORD_GUILD_ID)
            client.tree.copy_global_to(guild=guild)

            logger.debug("Syncing commands with guild")
            await client.tree.sync(guild=guild)
            logger.info(
                "Successfully registered slash commands to guild %s", DISCORD_GUILD_ID
            )
        else:
            logger.debug("No guild ID specified, syncing commands globally")
            await client.tree.sync()
            logger.info("Successfully registered slash commands globally")
    except Exception as e:
        logger.error("Error registering slash commands: %s", str(e))
        logger.exception("Detailed traceback:")


@client.tree.command(name="mc", description="Execute a Minecraft server command")
@app_commands.describe(command="The command to execute")
async def mc_command(interaction: discord.Interaction, command: str):
    """Execute a Minecraft server command."""
    logger.info("User %s executed /mc command: %s", interaction.user.name, command)

    # Check for admin role
    if not has_admin_role(interaction.user):
        logger.warning(
            "User %s attempted to use /mc command without admin role",
            interaction.user.name,
        )
        await interaction.response.send_message(
            "❌ You need the Admin role to use this command", ephemeral=True
        )
        return

    # Defer response since the command may take time to execute
    logger.debug("Deferring response for /mc command")
    await interaction.response.defer()

    try:
        logger.debug("Sending command to webhook server: %s", command)
        headers = {"X-Secret-Token": SECRET_TOKEN}

        logger.debug("Making POST request to %s/command", WEBHOOK_SERVER_URL)
        response = requests.post(
            f"{WEBHOOK_SERVER_URL}/command",
            json={"command": command},
            headers=headers,
            timeout=10,
        )

        logger.debug("Received response with status code: %d", response.status_code)

        if response.status_code == 200:
            result = response.json().get("result", "No response")
            logger.debug(
                "Command result: %s",
                result[:100] + "..." if len(result) > 100 else result,
            )

            formatted_results = format_code_blocks(result)
            logger.debug("Formatted result into %d chunks", len(formatted_results))

            # Send first chunk
            logger.debug("Sending first chunk of response")
            await interaction.followup.send(formatted_results[0])

            # Send additional chunks if needed
            for i, chunk in enumerate(formatted_results[1:], 1):
                logger.debug("Sending additional chunk %d of response", i)
                await interaction.followup.send(chunk)

            logger.info(
                "Successfully executed Minecraft command for %s", interaction.user.name
            )
        else:
            error_data = response.json()
            error_message = error_data.get("error", "Unknown error")
            logger.error("Error response from webhook server: %s", error_message)
            await interaction.followup.send(f"❌ Error: {error_message}")
    except requests.ConnectionError as e:
        logger.error("Connection error to webhook server: %s", str(e))
        await interaction.followup.send(
            f"❌ Error: Could not connect to Minecraft Terminal server. Please check if it's running."
        )
    except requests.Timeout as e:
        logger.error("Timeout error when connecting to webhook server: %s", str(e))
        await interaction.followup.send(
            f"❌ Error: Connection to Minecraft Terminal server timed out."
        )
    except Exception as e:
        logger.error("Error executing Minecraft command: %s", str(e))
        logger.exception("Detailed traceback:")
        await interaction.followup.send(f"❌ Error: {str(e)}")


@client.tree.command(name="logs", description="Get recent Minecraft server logs")
@app_commands.describe(lines="Number of log lines to retrieve (1-100)")
async def logs_command(interaction: discord.Interaction, lines: int = 10):
    """Get recent Minecraft server logs."""
    logger.info(
        "User %s executed /logs command for %d lines", interaction.user.name, lines
    )

    # Check for admin role
    if not has_admin_role(interaction.user):
        logger.warning(
            "User %s attempted to use /logs command without admin role",
            interaction.user.name,
        )
        await interaction.response.send_message(
            "❌ You need the Admin role to use this command", ephemeral=True
        )
        return

    # Validate input
    if lines < 1 or lines > 100:
        logger.warning(
            "User %s provided invalid lines parameter: %d", interaction.user.name, lines
        )
        await interaction.response.send_message(
            "❌ Please request between 1 and 100 lines", ephemeral=True
        )
        return

    # Defer response since the logs may be large
    logger.debug("Deferring response for /logs command")
    await interaction.response.defer()

    try:
        logger.debug("Requesting %d log lines from webhook server", lines)
        headers = {"X-Secret-Token": SECRET_TOKEN}

        logger.debug("Making GET request to %s/logs", WEBHOOK_SERVER_URL)
        response = requests.get(
            f"{WEBHOOK_SERVER_URL}/logs",
            params={"lines": lines},
            headers=headers,
            timeout=10,
        )

        logger.debug("Received response with status code: %d", response.status_code)

        if response.status_code == 200:
            logs = response.json().get("logs", "No logs available")
            logger.debug("Retrieved log data of length: %d", len(logs))

            formatted_logs = format_code_blocks(logs)
            logger.debug("Formatted logs into %d chunks", len(formatted_logs))

            # Send first chunk
            logger.debug("Sending first chunk of logs")
            await interaction.followup.send(formatted_logs[0])

            # Send additional chunks if needed
            for i, chunk in enumerate(formatted_logs[1:], 1):
                logger.debug("Sending additional chunk %d of logs", i)
                await interaction.followup.send(chunk)

            logger.info("Successfully retrieved logs for %s", interaction.user.name)
        else:
            error_data = response.json()
            error_message = error_data.get("error", "Unknown error")
            logger.error("Error response from webhook server: %s", error_message)
            await interaction.followup.send(f"❌ Error: {error_message}")
    except requests.ConnectionError as e:
        logger.error("Connection error to webhook server: %s", str(e))
        await interaction.followup.send(
            f"❌ Error: Could not connect to Minecraft Terminal server. Please check if it's running."
        )
    except requests.Timeout as e:
        logger.error("Timeout error when connecting to webhook server: %s", str(e))
        await interaction.followup.send(
            f"❌ Error: Connection to Minecraft Terminal server timed out."
        )
    except Exception as e:
        logger.error("Error retrieving logs: %s", str(e))
        logger.exception("Detailed traceback:")
        await interaction.followup.send(f"❌ Error: {str(e)}")


@client.tree.command(name="status", description="Check Minecraft server status")
async def status_command(interaction: discord.Interaction):
    """Check Minecraft server status."""
    logger.info("User %s executed /status command", interaction.user.name)

    # Defer response
    logger.debug("Deferring response for /status command")
    await interaction.response.defer()

    try:
        logger.debug("Requesting status from webhook server")
        headers = {"X-Secret-Token": SECRET_TOKEN}

        logger.debug("Making GET request to %s/status", WEBHOOK_SERVER_URL)
        response = requests.get(
            f"{WEBHOOK_SERVER_URL}/status", headers=headers, timeout=10
        )

        logger.debug("Received response with status code: %d", response.status_code)

        if response.status_code == 200:
            status_data = response.json().get("status", {})
            rcon_connected = status_data.get("rconConnected", False)
            log_watcher_active = status_data.get("logWatcherActive", False)

            logger.debug(
                "Status received - RCON: %s, Log Watcher: %s",
                rcon_connected,
                log_watcher_active,
            )

            # Create embed
            logger.debug("Creating Discord embed for status response")
            embed = discord.Embed(
                title="Minecraft Server Status",
                color=discord.Color.green() if rcon_connected else discord.Color.red(),
                timestamp=interaction.created_at,
            )

            embed.add_field(
                name="RCON Connection",
                value="✅ Connected" if rcon_connected else "❌ Disconnected",
                inline=True,
            )

            embed.add_field(
                name="Log Watcher",
                value="✅ Active" if log_watcher_active else "❌ Inactive",
                inline=True,
            )

            logger.debug("Sending status embed to Discord")
            await interaction.followup.send(embed=embed)
            logger.info(
                "Successfully sent status information to %s", interaction.user.name
            )
        else:
            error_data = response.json()
            error_message = error_data.get("error", "Unknown error")
            logger.error("Error response from webhook server: %s", error_message)
            await interaction.followup.send(f"❌ Error: {error_message}")
    except requests.ConnectionError as e:
        logger.error("Connection error to webhook server: %s", str(e))
        await interaction.followup.send(
            f"❌ Error: Could not connect to Minecraft Terminal server. Please check if it's running."
        )
    except requests.Timeout as e:
        logger.error("Timeout error when connecting to webhook server: %s", str(e))
        await interaction.followup.send(
            f"❌ Error: Connection to Minecraft Terminal server timed out."
        )
    except Exception as e:
        logger.error("Error checking status: %s", str(e))
        logger.exception("Detailed traceback:")
        await interaction.followup.send(f"❌ Error: {str(e)}")


if __name__ == "__main__":
    logger.info("Starting Discord Integration for Minecraft Terminal")

    # Check if required environment variables are set
    missing_vars = []

    if not DISCORD_TOKEN:
        missing_vars.append("DISCORD_TOKEN")
        logger.error("DISCORD_TOKEN is not set")

    if not DISCORD_GUILD_ID:
        missing_vars.append("DISCORD_GUILD_ID")
        logger.error("DISCORD_GUILD_ID is not set")

    if not WEBHOOK_SERVER_URL:
        missing_vars.append("WEBHOOK_SERVER_URL")
        logger.error("WEBHOOK_SERVER_URL is not set")

    if not SECRET_TOKEN:
        missing_vars.append("SECRET_TOKEN")
        logger.error("SECRET_TOKEN is not set")

    if not ADMIN_ROLE_ID:
        missing_vars.append("ADMIN_ROLE_ID")
        logger.error("ADMIN_ROLE_ID is not set")

    if missing_vars:
        logger.error(
            "Required environment variables not set: %s", ", ".join(missing_vars)
        )
        logger.error("Please check your .env file")
        sys.exit(1)

    # Verify webhook server URL format
    if WEBHOOK_SERVER_URL and not (
        WEBHOOK_SERVER_URL.startswith("http://")
        or WEBHOOK_SERVER_URL.startswith("https://")
    ):
        logger.warning(
            "WEBHOOK_SERVER_URL does not start with http:// or https://, this may cause issues"
        )
        logger.warning("Current value: %s", WEBHOOK_SERVER_URL)

    try:
        # Start the bot
        logger.info("Connecting to Discord...")
        client.run(DISCORD_TOKEN)
    except discord.LoginFailure:
        logger.error("Failed to login to Discord - invalid token")
    except Exception as e:
        logger.error("Error starting Discord bot: %s", str(e))
        logger.exception("Detailed traceback:")
