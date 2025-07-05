import discord
from discord.ext import commands
import asyncio
import os
import sqlite3
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# Get environment variables
TOKEN = os.getenv('DISCORD_TOKEN')
SUSPENDED_ROLE_ID = int(os.getenv('SUSPENDED_ROLE_ID'))
LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID'))
ALLOWED_ROLES = [int(role_id) for role_id in os.getenv('ALLOWED_ROLES', '').split(',') if role_id]

# Validation checks
if not TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable is required")
if not SUSPENDED_ROLE_ID:
    raise ValueError("SUSPENDED_ROLE_ID environment variable is required")
if not LOG_CHANNEL_ID:
    raise ValueError("LOG_CHANNEL_ID environment variable is required")
if not ALLOWED_ROLES:
    raise ValueError("ALLOWED_ROLES environment variable is required")

SUSPENSION_TIME_OPTIONS = [
    discord.app_commands.Choice(name="1h", value="1 hour"),
    discord.app_commands.Choice(name="12h", value="12 hours"),
    discord.app_commands.Choice(name="1d", value="1 day"),
    discord.app_commands.Choice(name="3d", value="3 days"),
    discord.app_commands.Choice(name="7d", value="7 days"),
    discord.app_commands.Choice(name="30d", value="30 days"),
]

class DatabaseManager:
    def __init__(self, db_path="jailbot.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize the database with required tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create suspensions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS suspensions (
                user_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                suspended_by INTEGER NOT NULL,
                start_time TIMESTAMP NOT NULL,
                end_time TIMESTAMP NOT NULL,
                duration_text TEXT NOT NULL,
                previous_roles TEXT NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                reason TEXT DEFAULT ''
            )
        ''')
        
        # Create suspension_logs table for audit trail
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS suspension_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                performed_by INTEGER NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                details TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def add_suspension(self, user_id, guild_id, suspended_by, duration_seconds, duration_text, previous_roles, reason=""):
        """Add a new suspension record"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        start_time = datetime.now()
        end_time = start_time + timedelta(seconds=duration_seconds)
        roles_json = json.dumps([role.id for role in previous_roles])
        
        cursor.execute('''
            INSERT OR REPLACE INTO suspensions 
            (user_id, guild_id, suspended_by, start_time, end_time, duration_text, previous_roles, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, guild_id, suspended_by, start_time, end_time, duration_text, roles_json, reason))
        
        # Add to logs
        cursor.execute('''
            INSERT INTO suspension_logs 
            (user_id, guild_id, action, performed_by, details)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, guild_id, "SUSPENDED", suspended_by, f"Duration: {duration_text}"))
        
        conn.commit()
        conn.close()
    
    def get_active_suspension(self, user_id):
        """Get active suspension for a user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM suspensions 
            WHERE user_id = ? AND is_active = 1
        ''', (user_id,))
        
        result = cursor.fetchone()
        conn.close()
        return result
    
    def get_all_active_suspensions(self):
        """Get all active suspensions"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM suspensions 
            WHERE is_active = 1 AND end_time > datetime('now')
        ''')
        
        results = cursor.fetchall()
        conn.close()
        return results
    
    def get_expired_suspensions(self):
        """Get all expired suspensions that are still marked as active"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM suspensions 
            WHERE is_active = 1 AND end_time <= datetime('now')
        ''')
        
        results = cursor.fetchall()
        conn.close()
        return results
    
    def end_suspension(self, user_id, ended_by=None):
        """End a suspension"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get the suspension first
        suspension = self.get_active_suspension(user_id)
        if not suspension:
            conn.close()
            return None
        
        # Update suspension to inactive
        cursor.execute('''
            UPDATE suspensions 
            SET is_active = 0 
            WHERE user_id = ? AND is_active = 1
        ''', (user_id,))
        
        # Add to logs
        action = "RELEASED" if ended_by else "EXPIRED"
        cursor.execute('''
            INSERT INTO suspension_logs 
            (user_id, guild_id, action, performed_by, details)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, suspension[1], action, ended_by or 0, ""))
        
        conn.commit()
        conn.close()
        return suspension
    
    def get_previous_roles(self, user_id):
        """Get previous roles for a user"""
        suspension = self.get_active_suspension(user_id)
        if suspension:
            return json.loads(suspension[6])  # previous_roles column
        return []

def convert_duration_to_seconds(duration):
    duration_map = {
        "1 hour": 1 * 3600,
        "12 hours": 12 * 3600,
        "1 day": 24 * 3600,
        "3 days": 3 * 24 * 3600,
        "7 days": 7 * 24 * 3600,
        "30 days": 30 * 24 * 3600
    }
    return duration_map.get(duration, -1)

# Initialize database
db = DatabaseManager()

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix='/', intents=intents)

def is_allowed_role():
    async def predicate(interaction: discord.Interaction):
        return any(role.id in ALLOWED_ROLES for role in interaction.user.roles)
    return commands.check(predicate)

async def check_expired_suspensions():
    """Background task to check for expired suspensions"""
    while not bot.is_closed():
        try:
            expired_suspensions = db.get_expired_suspensions()
            
            for suspension in expired_suspensions:
                user_id = suspension[0]
                guild_id = suspension[1]
                
                guild = bot.get_guild(guild_id)
                if not guild:
                    continue
                
                member = guild.get_member(user_id)
                if not member:
                    # User left the server, just mark as ended
                    db.end_suspension(user_id)
                    continue
                
                # Restore roles
                suspend_role = guild.get_role(SUSPENDED_ROLE_ID)
                previous_role_ids = json.loads(suspension[6])
                previous_roles = [guild.get_role(role_id) for role_id in previous_role_ids]
                previous_roles = [role for role in previous_roles if role]  # Filter out None roles
                
                try:
                    if suspend_role in member.roles:
                        await member.remove_roles(suspend_role, reason="Sentence ended")
                    
                    if previous_roles:
                        await member.add_roles(*previous_roles, reason="Roles restored after release")
                    
                    # Log the restoration
                    log_channel = bot.get_channel(LOG_CHANNEL_ID)
                    if log_channel:
                        restore_log_embed = discord.Embed(
                            title="Automatic Release",
                            description=f"<@{user_id}>'s sentence has ended and roles have been restored.",
                            color=discord.Color.green()
                        )
                        restore_log_embed.add_field(name="Inmate ID", value=f"||{user_id}||")
                        await log_channel.send(embed=restore_log_embed)
                
                except discord.Forbidden:
                    print(f"Could not restore roles for user {user_id}")
                except discord.HTTPException as e:
                    print(f"Error restoring roles for user {user_id}: {e}")
                
                # Mark suspension as ended
                db.end_suspension(user_id)
        
        except Exception as e:
            print(f"Error in expired suspension check: {e}")
        
        # Check every 60 seconds
        await asyncio.sleep(60)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('Database initialized and ready!')
    
    # Start background task for checking expired suspensions
    bot.loop.create_task(check_expired_suspensions())
    print('Background task started for checking expired suspensions')

@bot.tree.command(name="jail", description="Jail a user for a specific time")
@is_allowed_role()
@discord.app_commands.choices(duration=SUSPENSION_TIME_OPTIONS)
async def suspend(interaction: discord.Interaction, member: discord.Member, duration: str):
    await interaction.response.defer(thinking=True)

    # Check if user is already suspended
    if db.get_active_suspension(member.id):
        await interaction.followup.send(f"{member.mention} is already locked up.")
        return

    current_roles = member.roles[1:]  # Exclude @everyone
    suspend_role = interaction.guild.get_role(SUSPENDED_ROLE_ID)

    if suspend_role is None:
        await interaction.followup.send("Incarcerated role not found. Please check the role ID.")
        return

    duration_seconds = convert_duration_to_seconds(duration)
    if duration_seconds == -1:
        await interaction.followup.send("Invalid duration specified.")
        return

    try:
        # Remove current roles and add suspension role
        await member.remove_roles(*current_roles, reason="User incarcerated")
        await member.add_roles(suspend_role, reason="User incarcerated")

        # Store in database
        db.add_suspension(
            member.id, 
            interaction.guild.id, 
            interaction.user.id, 
            duration_seconds, 
            duration, 
            current_roles
        )

        # Send confirmation embed
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

    except discord.Forbidden:
        await interaction.followup.send(f"I don't have the authority to sentence {member.mention}.")
    except discord.HTTPException as e:
        await interaction.followup.send(f"An error occurred while jailing {member.mention}: {e}")

@bot.tree.command(name="unjail", description="Unjail an incarcerated user")
@is_allowed_role()
async def unsuspend(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(thinking=True)

    suspension = db.get_active_suspension(member.id)
    if not suspension:
        await interaction.followup.send(f"{member.mention} is not currently incarcerated.")
        return

    try:
        suspend_role = interaction.guild.get_role(SUSPENDED_ROLE_ID)
        if suspend_role is None:
            await interaction.followup.send("Incarcerated role not found. Please check the role ID.")
            return

        # Get previous roles
        previous_role_ids = json.loads(suspension[6])
        previous_roles = [interaction.guild.get_role(role_id) for role_id in previous_role_ids]
        previous_roles = [role for role in previous_roles if role]  # Filter out None roles

        # Remove suspension role and restore previous roles
        await member.remove_roles(suspend_role, reason="User released")
        if previous_roles:
            await member.add_roles(*previous_roles, reason="Roles restored after release")

        # End suspension in database
        db.end_suspension(member.id, interaction.user.id)

        # Send confirmation embed
        embed = discord.Embed(
            title="User Released",
            description=f"{member.mention} has been released and their roles have been restored.",
            color=discord.Color.green()
        )
        embed.add_field(name="Released By", value=interaction.user.mention)
        embed.set_thumbnail(url=member.avatar.url if member.avatar else "")
        await interaction.followup.send(embed=embed)

        # Log the release
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

@bot.tree.command(name="jailstatus", description="Check the status of a jailed user")
@is_allowed_role()
async def jail_status(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(thinking=True)
    
    suspension = db.get_active_suspension(member.id)
    if not suspension:
        await interaction.followup.send(f"{member.mention} is not currently incarcerated.")
        return
    
    # Parse suspension data
    end_time = datetime.fromisoformat(suspension[4])
    remaining_time = end_time - datetime.now()
    
    if remaining_time.total_seconds() <= 0:
        await interaction.followup.send(f"{member.mention}'s sentence has expired but hasn't been processed yet.")
        return
    
    # Format remaining time
    days = remaining_time.days
    hours, remainder = divmod(remaining_time.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    
    time_str = []
    if days > 0:
        time_str.append(f"{days} day{'s' if days != 1 else ''}")
    if hours > 0:
        time_str.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes > 0:
        time_str.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    
    remaining_str = ", ".join(time_str) if time_str else "Less than a minute"
    
    embed = discord.Embed(
        title="Jail Status",
        description=f"{member.mention} is currently incarcerated.",
        color=discord.Color.orange()
    )
    embed.add_field(name="Original Duration", value=suspension[5])
    embed.add_field(name="Time Remaining", value=remaining_str)
    embed.add_field(name="Release Time", value=f"<t:{int(end_time.timestamp())}:F>")
    embed.set_thumbnail(url=member.avatar.url if member.avatar else "")
    
    await interaction.followup.send(embed=embed)

if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"Error starting bot: {e}")