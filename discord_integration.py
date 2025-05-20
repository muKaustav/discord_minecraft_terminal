import asyncio
import logging
import os

import discord
import requests
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
WEBHOOK_SERVER_URL = os.getenv("WEBHOOK_SERVER_URL")
SECRET_TOKEN = os.getenv("SECRET_TOKEN")
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID"))

# Initialize Discord client
intents = discord.Intents.default()
client = commands.Bot(command_prefix="!", intents=intents)


# Format code blocks for Discord
def format_code_blocks(text):
    """Format text into code blocks, splitting if necessary."""
    if not text:
        return ["No output"]

    # If text is too long, split it
    if len(text) > 1900:
        chunks = []
        for i in range(0, len(text), 1900):
            chunks.append(f"```\n{text[i:i+1900]}\n```")
        return chunks

    return [f"```\n{text}\n```"]


# Check if user has admin role
def has_admin_role(member):
    """Check if a member has the admin role."""
    return discord.utils.get(member.roles, id=ADMIN_ROLE_ID) is not None


@client.event
async def on_ready():
    """Called when the client is connected to Discord."""
    logger.info(f"Logged in as {client.user.name} ({client.user.id})")
    await register_commands()


async def register_commands():
    """Register slash commands with Discord."""
    try:
        # Sync commands with Discord
        guild = discord.Object(id=DISCORD_GUILD_ID)
        client.tree.copy_global_to(guild=guild)
        await client.tree.sync(guild=guild)
        logger.info("Successfully registered slash commands")
    except Exception as e:
        logger.error(f"Error registering slash commands: {e}")


@client.tree.command(name="mc", description="Execute a Minecraft server command")
@app_commands.describe(command="The command to execute")
async def mc_command(interaction: discord.Interaction, command: str):
    """Execute a Minecraft server command."""
    # Check for admin role
    if not has_admin_role(interaction.user):
        await interaction.response.send_message(
            "❌ You need the Admin role to use this command", ephemeral=True
        )
        return

    # Defer response since the command may take time to execute
    await interaction.response.defer()

    try:
        headers = {"X-Secret-Token": SECRET_TOKEN}
        response = requests.post(
            f"{WEBHOOK_SERVER_URL}/command",
            json={"command": command},
            headers=headers,
            timeout=10,
        )

        if response.status_code == 200:
            result = response.json().get("result", "No response")
            formatted_results = format_code_blocks(result)

            # Send first chunk
            await interaction.followup.send(formatted_results[0])

            # Send additional chunks if needed
            for chunk in formatted_results[1:]:
                await interaction.followup.send(chunk)
        else:
            error_data = response.json()
            await interaction.followup.send(
                f"❌ Error: {error_data.get('error', 'Unknown error')}"
            )
    except Exception as e:
        logger.error(f"Error executing Minecraft command: {e}")
        await interaction.followup.send(f"❌ Error: {str(e)}")


@client.tree.command(name="logs", description="Get recent Minecraft server logs")
@app_commands.describe(lines="Number of log lines to retrieve (1-100)")
async def logs_command(interaction: discord.Interaction, lines: int = 10):
    """Get recent Minecraft server logs."""
    # Check for admin role
    if not has_admin_role(interaction.user):
        await interaction.response.send_message(
            "❌ You need the Admin role to use this command", ephemeral=True
        )
        return

    # Validate input
    if lines < 1 or lines > 100:
        await interaction.response.send_message(
            "❌ Please request between 1 and 100 lines", ephemeral=True
        )
        return

    # Defer response since the logs may be large
    await interaction.response.defer()

    try:
        headers = {"X-Secret-Token": SECRET_TOKEN}
        response = requests.get(
            f"{WEBHOOK_SERVER_URL}/logs",
            params={"lines": lines},
            headers=headers,
            timeout=10,
        )

        if response.status_code == 200:
            logs = response.json().get("logs", "No logs available")
            formatted_logs = format_code_blocks(logs)

            # Send first chunk
            await interaction.followup.send(formatted_logs[0])

            # Send additional chunks if needed
            for chunk in formatted_logs[1:]:
                await interaction.followup.send(chunk)
        else:
            error_data = response.json()
            await interaction.followup.send(
                f"❌ Error: {error_data.get('error', 'Unknown error')}"
            )
    except Exception as e:
        logger.error(f"Error retrieving logs: {e}")
        await interaction.followup.send(f"❌ Error: {str(e)}")


@client.tree.command(name="status", description="Check Minecraft server status")
async def status_command(interaction: discord.Interaction):
    """Check Minecraft server status."""
    # Defer response
    await interaction.response.defer()

    try:
        headers = {"X-Secret-Token": SECRET_TOKEN}
        response = requests.get(
            f"{WEBHOOK_SERVER_URL}/status", headers=headers, timeout=10
        )

        if response.status_code == 200:
            status_data = response.json().get("status", {})
            rcon_connected = status_data.get("rconConnected", False)
            log_watcher_active = status_data.get("logWatcherActive", False)

            # Create embed
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

            await interaction.followup.send(embed=embed)
        else:
            error_data = response.json()
            await interaction.followup.send(
                f"❌ Error: {error_data.get('error', 'Unknown error')}"
            )
    except Exception as e:
        logger.error(f"Error checking status: {e}")
        await interaction.followup.send(f"❌ Error: {str(e)}")


if __name__ == "__main__":
    # Check if required environment variables are set
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN is not set. Please check your .env file.")
        exit(1)

    if not DISCORD_GUILD_ID:
        logger.error("DISCORD_GUILD_ID is not set. Please check your .env file.")
        exit(1)

    if not WEBHOOK_SERVER_URL:
        logger.error("WEBHOOK_SERVER_URL is not set. Please check your .env file.")
        exit(1)

    if not SECRET_TOKEN:
        logger.error("SECRET_TOKEN is not set. Please check your .env file.")
        exit(1)

    # Start the bot
    client.run(DISCORD_TOKEN)
