import discord
from discord.ext import commands
import asyncio

# Set your bot token and other constants
TOKEN = 'MTI5MTM2NzYxMjI5MDMwNjA1OA.GfBbVI.zyuPyNXS3jIpuS8zGYXZotQ8bBW22k7YPcdh_Y'
SUSPENDED_ROLE_ID = 1291368158149480471  # Replace with your suspended role ID
LOG_CHANNEL_ID = 1291369068695257119  # Replace with your log channel ID
ALLOWED_ROLES = [1110043784055640127]  # Replace with allowed role IDs


# Define suspension time options
SUSPENSION_TIME_OPTIONS = [
    discord.app_commands.Choice(name="5m", value="5 minutes"),
    discord.app_commands.Choice(name="12h", value="12 hours"),
    discord.app_commands.Choice(name="1d", value="1 day"),
    discord.app_commands.Choice(name="3d", value="3 days"),
    discord.app_commands.Choice(name="7d", value="7 days"),
    discord.app_commands.Choice(name="30d", value="30 days"),
]

# Create a dictionary to track suspended users
suspended_users = {}

# Helper function to convert duration to seconds
def convert_duration_to_seconds(duration):
    if duration == "5 minutes":
        return 5 * 60
    if duration == "12 hours":
        return 12 * 3600
    elif duration == "1 day":
        return 24 * 3600
    elif duration == "3 days":
        return 3 * 24 * 3600
    elif duration == "7 days":
        return 7 * 24 * 3600
    elif duration == "30 days":
        return 30 * 24 * 3600
    return -1  # Return -1 for invalid duration

# Create a bot instance
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix='/', intents=intents)

def is_allowed_role():
    async def predicate(interaction: discord.Interaction):
        return any(role.id in ALLOWED_ROLES for role in interaction.user.roles)
    return commands.check(predicate)

@bot.event
async def on_ready():
    await bot.tree.sync()  # Sync commands on startup
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('-----')

@bot.tree.command(name="jail", description="Jail a user for a specific time")
@is_allowed_role()
@discord.app_commands.choices(duration=SUSPENSION_TIME_OPTIONS)
async def suspend(interaction: discord.Interaction, member: discord.Member, duration: str):
    await interaction.response.defer(thinking=True)

    # Check if the user is already suspended
    if member.id in suspended_users:
        await interaction.followup.send(f"{member.mention} is already locked up.")
        return

    current_roles = member.roles[1:]  # Exclude @everyone role
    suspend_role = interaction.guild.get_role(SUSPENDED_ROLE_ID)

    if suspend_role is None:
        await interaction.followup.send("Incarcerated role not found. Please check the role ID.")
        return

    # Convert duration to seconds before proceeding
    duration_seconds = convert_duration_to_seconds(duration)
    if duration_seconds == -1:
        await interaction.followup.send("Invalid duration specified.")
        return

    try:
        await member.remove_roles(*current_roles, reason="User incarcerated")
        await member.add_roles(suspend_role, reason="User incarcerated")

        # Notify the user via an embed
        embed = discord.Embed(
            title="User Incarcerated",
            description=f"{member.mention} has been incarcerated for {duration}.",
            color=discord.Color.red()
        )
        embed.add_field(name="Sentenced By", value=interaction.user.mention)
        embed.add_field(name="Duration", value=duration)
        embed.set_thumbnail(url=member.avatar.url if member.avatar else "")
        await interaction.followup.send(embed=embed)

        # Log the suspension
        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            log_embed = discord.Embed(
                title="Sentencing Log",
                description=f"{member.mention} was incarcerated.",
                color=discord.Color.red()
            )
            log_embed.add_field(name="Arresting Officer", value=interaction.user.mention)
            log_embed.add_field(name="Duration", value=duration)
            log_embed.add_field(name="Inmate ID", value=f"||{member.id}||")

            log_embed.set_thumbnail(url=member.avatar.url if member.avatar else "")
            await log_channel.send(embed=log_embed)

        suspended_users[member.id] = current_roles  # Store current roles

        # Sleep for the specified duration
        await asyncio.sleep(duration_seconds)

        await member.remove_roles(suspend_role, reason="Sentence ended")
        await member.add_roles(*current_roles, reason="Roles restored after release")

        restore_embed = discord.Embed(
            title="User Sentence Ended",
            description=f"{member.mention}'s sentence has ended and roles have been restored.",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=restore_embed)

        if log_channel:
            restore_log_embed = discord.Embed(
                title="User Roles Restored",
                description=f"{member.mention}'s roles have been restored after sentence.",
                color=discord.Color.green()
            )
            restore_log_embed.add_field(name="Inmate ID", value=f"||{member.id}||")
            await log_channel.send(embed=restore_log_embed)

        suspended_users.pop(member.id, None)  # Remove from suspended users

    except discord.Forbidden:
        await interaction.followup.send(f"I don't have the authority to sentence {member.mention}.")
    except discord.HTTPException as e:
        await interaction.followup.send(f"An error occurred while jailing {member.mention}: {e}")

@bot.tree.command(name="unjail", description="Unjail an incarcerated user")
@is_allowed_role()
async def unsuspend(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(thinking=True)

    # Check if the user is suspended
    if member.id not in suspended_users:
        await interaction.followup.send(f"{member.mention} is not currently incarcerated.")
        return

    try:
        suspend_role = interaction.guild.get_role(SUSPENDED_ROLE_ID)
        if suspend_role is None:
            await interaction.followup.send("Incarcerated role not found. Please check the role ID.")
            return

        # Remove suspension role and restore previous roles
        await member.remove_roles(suspend_role, reason="User released")
        previous_roles = suspended_users.pop(member.id)  # Get stored roles
        await member.add_roles(*previous_roles, reason="Roles restored after sentence")

        # Notify the user via an embed
        embed = discord.Embed(
            title="User Released",
            description=f"{member.mention} has been released and their roles have been restored.",
            color=discord.Color.green()
        )
        embed.add_field(name="Released By", value=interaction.user.mention)
        embed.set_thumbnail(url=member.avatar.url if member.avatar else "")
        await interaction.followup.send(embed=embed)

        # Log the unsuspension
        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            log_embed = discord.Embed(
                title="User Released",
                description=f"{member.mention} was released.",
                color=discord.Color.green()
            )
            log_embed.add_field(name="Released By", value=interaction.user.mention)
            log_embed.add_field(name="Inmate ID", value=f"||{member.id}||")
            log_embed.set_thumbnail(url=member.avatar.url if member.avatar else "")
            await log_channel.send(embed=log_embed)

    except discord.Forbidden:
        await interaction.followup.send(f"I don't have permission to manage roles for {member.mention}.")
    except discord.HTTPException as e:
        await interaction.followup.send(f"An error occurred while releasing {member.mention}: {e}")

# Run the bot
bot.run(TOKEN)

#--    ____  _________                   __  ____ ____    
#--   / __ \/ __/ __(_)_______  _____   /  |/  (_) / /____ ™
#--  / / / / /_/ /_/ / ___/ _ \/ ___/  / /|_/ / / / / ___/
#-- / /_/ / __/ __/ / /__/  __/ /     / /  / / / / (__  ) 
#-- \____/_/ /_/ /_/\___/\___/_/     /_/  /_/_/_/_/____/  