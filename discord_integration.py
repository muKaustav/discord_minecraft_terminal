import logging
import os
import sys
from urllib.parse import urlparse

import discord
import requests
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
WEBHOOK_SERVER_URL = (os.getenv("WEBHOOK_SERVER_URL") or "").rstrip("/")
SECRET_TOKEN = os.getenv("SECRET_TOKEN")
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID")) if os.getenv("ADMIN_ROLE_ID") else None
MAX_COMMAND_LENGTH = 256
HTTP_TIMEOUT = 10

logger.info("Configuration loaded:")
logger.info("DISCORD_TOKEN: %s", "Set" if DISCORD_TOKEN else "Not set")
logger.info("DISCORD_GUILD_ID: %s", DISCORD_GUILD_ID)
logger.info("WEBHOOK_SERVER_URL: %s", WEBHOOK_SERVER_URL)
logger.info("SECRET_TOKEN: %s", "Set" if SECRET_TOKEN else "Not set")
logger.info("ADMIN_ROLE_ID: %s", ADMIN_ROLE_ID)

intents = discord.Intents.default()
client = commands.Bot(command_prefix="!", intents=intents, help_command=None)
http = requests.Session()
http.headers.update({"X-Secret-Token": SECRET_TOKEN or ""})


def format_code_blocks(text):
    if not text:
        return ["No output"]
    if len(text) > 1900:
        return [f"```\n{text[i:i + 1900]}\n```" for i in range(0, len(text), 1900)]
    return [f"```\n{text}\n```"]


def is_allowed_guild(interaction):
    if not DISCORD_GUILD_ID or interaction.guild_id is None:
        return False
    try:
        return int(interaction.guild_id) == int(DISCORD_GUILD_ID)
    except (TypeError, ValueError):
        return False


def has_admin_role(user):
    if not ADMIN_ROLE_ID:
        return False
    roles = getattr(user, "roles", None)
    if not roles:
        return False
    return any(getattr(role, "id", None) == ADMIN_ROLE_ID for role in roles)


def is_safe_terminal_url(url):
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    return True


def sanitize_command(raw):
    command = (raw or "").strip()
    if command.startswith("/"):
        command = command[1:]
    if not command or len(command) > MAX_COMMAND_LENGTH:
        return None
    if any(ord(char) < 32 for char in command):
        return None
    return command


def terminal_url(path):
    return f"{WEBHOOK_SERVER_URL}{path}"


async def deny(interaction, message):
    await interaction.response.send_message(message, ephemeral=True)


@client.event
async def on_ready():
    logger.info("Bot is now logged in as %s (%s)", client.user.name, client.user.id)
    await register_commands()


async def register_commands():
    try:
        if DISCORD_GUILD_ID:
            guild = discord.Object(id=int(DISCORD_GUILD_ID))
            client.tree.copy_global_to(guild=guild)
            await client.tree.sync(guild=guild)
            logger.info("Registered slash commands to guild %s", DISCORD_GUILD_ID)
        else:
            await client.tree.sync()
            logger.info("Registered slash commands globally")
    except Exception:
        logger.exception("Error registering slash commands")


@client.tree.command(name="mc", description="Execute a Minecraft server command")
@app_commands.describe(command="The command to execute")
@app_commands.guild_only()
async def mc_command(interaction: discord.Interaction, command: str):
    if not is_allowed_guild(interaction) or not has_admin_role(interaction.user):
        logger.warning("Denied /mc for %s", interaction.user)
        await deny(interaction, "❌ You need the Admin role to use this command")
        return

    command = sanitize_command(command)
    if not command:
        await deny(interaction, "❌ Invalid command")
        return

    logger.info("User %s executed /mc command: %s", interaction.user.name, command)
    await interaction.response.defer(ephemeral=True)
    try:
        response = http.post(
            terminal_url("/command"),
            json={"command": command},
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code == 200:
            result = response.json().get("result", "No response")
            chunks = format_code_blocks(result)
            await interaction.followup.send(chunks[0], ephemeral=True)
            for chunk in chunks[1:]:
                await interaction.followup.send(chunk, ephemeral=True)
            return
        logger.error("Terminal /command status %s", response.status_code)
        await interaction.followup.send("❌ Command failed", ephemeral=True)
    except requests.ConnectionError:
        await interaction.followup.send(
            "❌ Could not connect to Minecraft Terminal server.", ephemeral=True
        )
    except requests.Timeout:
        await interaction.followup.send(
            "❌ Minecraft Terminal server timed out.", ephemeral=True
        )
    except Exception:
        logger.exception("Error executing Minecraft command")
        await interaction.followup.send("❌ Command failed", ephemeral=True)


@client.tree.command(name="logs", description="Get recent Minecraft server logs")
@app_commands.describe(lines="Number of log lines to retrieve (1-100)")
@app_commands.guild_only()
async def logs_command(interaction: discord.Interaction, lines: int = 10):
    if not is_allowed_guild(interaction) or not has_admin_role(interaction.user):
        logger.warning("Denied /logs for %s", interaction.user)
        await deny(interaction, "❌ You need the Admin role to use this command")
        return
    if lines < 1 or lines > 100:
        await deny(interaction, "❌ Please request between 1 and 100 lines")
        return

    logger.info("User %s executed /logs for %d lines", interaction.user.name, lines)
    await interaction.response.defer(ephemeral=True)
    try:
        response = http.get(
            terminal_url("/logs"),
            params={"lines": lines},
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code == 200:
            logs = response.json().get("logs", "No logs available")
            chunks = format_code_blocks(logs)
            await interaction.followup.send(chunks[0], ephemeral=True)
            for chunk in chunks[1:]:
                await interaction.followup.send(chunk, ephemeral=True)
            return
        logger.error("Terminal /logs status %s", response.status_code)
        await interaction.followup.send("❌ Could not retrieve logs", ephemeral=True)
    except requests.ConnectionError:
        await interaction.followup.send(
            "❌ Could not connect to Minecraft Terminal server.", ephemeral=True
        )
    except requests.Timeout:
        await interaction.followup.send(
            "❌ Minecraft Terminal server timed out.", ephemeral=True
        )
    except Exception:
        logger.exception("Error retrieving logs")
        await interaction.followup.send("❌ Could not retrieve logs", ephemeral=True)


@client.tree.command(name="status", description="Check Minecraft server status")
@app_commands.guild_only()
async def status_command(interaction: discord.Interaction):
    if not is_allowed_guild(interaction) or not has_admin_role(interaction.user):
        logger.warning("Denied /status for %s", interaction.user)
        await deny(interaction, "❌ You need the Admin role to use this command")
        return

    logger.info("User %s executed /status", interaction.user.name)
    await interaction.response.defer(ephemeral=True)
    try:
        response = http.get(terminal_url("/status"), timeout=HTTP_TIMEOUT)
        if response.status_code == 200:
            status_data = response.json().get("status", {})
            rcon_ok = status_data.get("rconConnected", False)
            logs_ok = status_data.get("logWatcherActive", False)
            embed = discord.Embed(
                title="Minecraft Server Status",
                color=discord.Color.green() if rcon_ok else discord.Color.red(),
                timestamp=interaction.created_at,
            )
            embed.add_field(
                name="RCON Connection",
                value="✅ Connected" if rcon_ok else "❌ Disconnected",
                inline=True,
            )
            embed.add_field(
                name="Log Watcher",
                value="✅ Active" if logs_ok else "❌ Inactive",
                inline=True,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        await interaction.followup.send("❌ Status check failed", ephemeral=True)
    except requests.ConnectionError:
        await interaction.followup.send(
            "❌ Could not connect to Minecraft Terminal server.", ephemeral=True
        )
    except requests.Timeout:
        await interaction.followup.send(
            "❌ Minecraft Terminal server timed out.", ephemeral=True
        )
    except Exception:
        logger.exception("Error checking status")
        await interaction.followup.send("❌ Status check failed", ephemeral=True)


if __name__ == "__main__":
    logger.info("Starting Discord Integration for Minecraft Terminal")
    missing_vars = [
        name
        for name, value in (
            ("DISCORD_TOKEN", DISCORD_TOKEN),
            ("DISCORD_GUILD_ID", DISCORD_GUILD_ID),
            ("WEBHOOK_SERVER_URL", WEBHOOK_SERVER_URL),
            ("SECRET_TOKEN", SECRET_TOKEN),
            ("ADMIN_ROLE_ID", ADMIN_ROLE_ID),
        )
        if not value
    ]
    if missing_vars:
        logger.error(
            "Required environment variables not set: %s", ", ".join(missing_vars)
        )
        sys.exit(1)
    if len(SECRET_TOKEN) < 16:
        logger.error("SECRET_TOKEN is too short")
        sys.exit(1)
    try:
        int(DISCORD_GUILD_ID)
    except ValueError:
        logger.error("DISCORD_GUILD_ID must be numeric")
        sys.exit(1)
    if not is_safe_terminal_url(WEBHOOK_SERVER_URL):
        logger.error("WEBHOOK_SERVER_URL must be an http(s) URL without credentials")
        sys.exit(1)

    logger.info("Connecting to Discord...")
    try:
        client.run(DISCORD_TOKEN)
    except discord.LoginFailure:
        logger.error("Failed to login to Discord - invalid token")
    except Exception:
        logger.exception("Error starting Discord bot")
