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
COURT_RECORD_CHANNEL_ID = int(os.getenv('COURT_RECORD_CHANNEL_ID'))
ALLOWED_ROLES = [int(role_id) for role_id in os.getenv('ALLOWED_ROLES', '').split(',') if role_id]

# Validation checks
if not TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable is required")
if not SUSPENDED_ROLE_ID:
    raise ValueError("SUSPENDED_ROLE_ID environment variable is required")
if not LOG_CHANNEL_ID:
    raise ValueError("LOG_CHANNEL_ID environment variable is required")
if not COURT_RECORD_CHANNEL_ID:
    raise ValueError("COURT_RECORD_CHANNEL_ID environment variable is required")
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
        
        # Create sticky_messages table to persist sticky message IDs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sticky_messages (
                channel_id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    
    def get_sticky_message_id(self, channel_id):
        """Get the stored sticky message ID for a channel"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT message_id FROM sticky_messages 
            WHERE channel_id = ?
        ''', (channel_id,))
        
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def update_sticky_message_id(self, channel_id, message_id):
        """Store or update the sticky message ID for a channel"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO sticky_messages 
            (channel_id, message_id, last_updated)
            VALUES (?, ?, ?)
        ''', (channel_id, message_id, datetime.now()))
        
        conn.commit()
        conn.close()

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
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

class TimeRemainingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # Persistent view
    
    @discord.ui.button(label="Time Remaining", style=discord.ButtonStyle.green, emoji="⏰", custom_id="time_remaining_button")
    async def time_remaining_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if user is incarcerated
        suspension = db.get_active_suspension(interaction.user.id)
        
        if not suspension:
            await interaction.response.send_message(
                "You are currently not incarcerated.\n"
                "The judge here can be a real prick so I highly suggest staying out of trouble to keep it that way.",
                ephemeral=True
            )
            return
        
        # Parse suspension data
        end_time = datetime.fromisoformat(suspension[4])
        remaining_time = end_time - datetime.now()
        
        if remaining_time.total_seconds() <= 0:
            await interaction.response.send_message(
                "Your sentence has expired but hasn't been processed yet. You should be released shortly.",
                ephemeral=True
            )
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
            title="⏰ Your Remaining Time",
            description=f"**Original Sentence:** {suspension[5]}\n"
                       f"**Time Remaining:** {remaining_str}\n"
                       f"**Release Time:** <t:{int(end_time.timestamp())}:F>",
            color=discord.Color.orange()
        )
        embed.set_thumbnail(url=interaction.user.avatar.url if interaction.user.avatar else "")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

def is_allowed_role():
    async def predicate(interaction: discord.Interaction):
        return any(role.id in ALLOWED_ROLES for role in interaction.user.roles)
    return commands.check(predicate)

async def create_sticky_embed():
    """Create the court record sticky embed"""
    # Get current inmate count
    total_inmates = 0
    for guild in bot.guilds:
        suspend_role = guild.get_role(SUSPENDED_ROLE_ID)
        if suspend_role:
            total_inmates += len(suspend_role.members)
    
    embed = discord.Embed(
        title="🏛️ Court Records & Inmate Status",
        description="Welcome to the Court Records. Click the button below to check your remaining sentence time.",
        color=discord.Color.dark_red()
    )
    
    if total_inmates == 0:
        embed.add_field(
            name="📊 Current Status", 
            value="🟢 **Jail is Empty**\nAll inmates have been released!", 
            inline=False
        )
    elif total_inmates == 1:
        embed.add_field(
            name="📊 Current Status", 
            value="🟠 **1 Inmate** currently serving time", 
            inline=False
        )
    else:
        embed.add_field(
            name="📊 Current Status", 
            value=f"🔴 **{total_inmates} Inmates** currently serving time", 
            inline=False
        )
    
    embed.add_field(
        name="ℹ️ How to Use", 
        value="• Click **Time Remaining** to check your sentence\n"
              "• Only you can see your personal information\n"
              "• Information updates automatically", 
        inline=False
    )
    
    embed.set_footer(text="Stay out of trouble! • Updated automatically")
    embed.timestamp = datetime.now()
    
    return embed

async def send_sticky_message():
    """Send or update the sticky message in court records channel"""
    try:
        channel = bot.get_channel(COURT_RECORD_CHANNEL_ID)
        if not channel:
            print(f"Court record channel {COURT_RECORD_CHANNEL_ID} not found")
            return
        
        embed = await create_sticky_embed()
        view = TimeRemainingView()
        
        # Get the stored sticky message ID from database
        stored_message_id = db.get_sticky_message_id(COURT_RECORD_CHANNEL_ID)
        
        # Always delete old and create new to keep it at bottom
        if stored_message_id:
            try:
                old_message = await channel.fetch_message(stored_message_id)
                await old_message.delete()
                print(f"Deleted old sticky message: {stored_message_id}")
            except discord.NotFound:
                print(f"Old sticky message {stored_message_id} not found")
            except Exception as e:
                print(f"Error deleting old sticky message: {e}")
        
        # Always create new message at bottom
        message = await channel.send(embed=embed, view=view)
        db.update_sticky_message_id(COURT_RECORD_CHANNEL_ID, message.id)
        print(f"Created new sticky message at bottom: {message.id}")
        
    except Exception as e:
        print(f"Error sending sticky message: {e}")

async def update_bot_activity():
    """Update the bot's activity status to show current inmate count"""
    try:
        total_inmates = 0
        
        # Count inmates across all guilds the bot is in
        for guild in bot.guilds:
            suspend_role = guild.get_role(SUSPENDED_ROLE_ID)
            if suspend_role:
                total_inmates += len(suspend_role.members)
        
        # Set activity based on inmate count
        if total_inmates == 0:
            activity = discord.Activity(type=discord.ActivityType.watching, name="an empty jail")
        elif total_inmates == 1:
            activity = discord.Activity(type=discord.ActivityType.watching, name="1 inmate")
        else:
            activity = discord.Activity(type=discord.ActivityType.watching, name=f"{total_inmates} inmates")
        
        await bot.change_presence(activity=activity)
        
        # Also update the sticky message when activity changes
        await send_sticky_message()
        
    except Exception as e:
        print(f"Error updating bot activity: {e}")

async def check_expired_suspensions():
    """Background task to check for expired suspensions"""
    while not bot.is_closed():
        try:
            expired_suspensions = db.get_expired_suspensions()
            status_updated = False
            
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
                    status_updated = True
                    continue
                
                # Restore roles
                suspend_role = guild.get_role(SUSPENDED_ROLE_ID)
                previous_role_ids = json.loads(suspension[6])
                previous_roles = [guild.get_role(role_id) for role_id in previous_role_ids]
                previous_roles = [role for role in previous_roles if role]  # Filter out None roles
                
                try:
                    if suspend_role in member.roles:
                        await member.remove_roles(suspend_role, reason="Sentence ended")
                        status_updated = True
                    
                    if previous_roles:
                        await member.add_roles(*previous_roles, reason="Roles restored after release")
                    
                    # Send confirmation embed for automatic release
                    log_channel = bot.get_channel(LOG_CHANNEL_ID)
                    
                    # Calculate how long they were actually incarcerated
                    start_time = datetime.fromisoformat(suspension[3])
                    actual_time_served = datetime.now() - start_time
                    days_served = actual_time_served.days
                    hours_served, remainder = divmod(actual_time_served.seconds, 3600)
                    
                    # Format time served
                    served_str = []
                    if days_served > 0:
                        served_str.append(f"{days_served} day{'s' if days_served != 1 else ''}")
                    if hours_served > 0:
                        served_str.append(f"{hours_served} hour{'s' if hours_served != 1 else ''}")
                    
                    time_served_display = ", ".join(served_str) if served_str else "Less than an hour"
                    
                    # Create confirmation embed (similar to manual release)
                    confirmation_embed = discord.Embed(
                        title="Inmate Released - Time Served",
                        description=f"{member.mention} has completed their sentence and roles have been restored.",
                        color=discord.Color.green()
                    )
                    confirmation_embed.add_field(name="Released By", value="Time Served", inline=True)
                    confirmation_embed.add_field(name="Original Sentence", value=suspension[5], inline=True)
                    confirmation_embed.add_field(name="Actual Time Served", value=time_served_display, inline=True)
                    confirmation_embed.set_thumbnail(url=member.avatar.url if member.avatar else "")
                    
                    # Add your original footer with timestamp
                    central_tz = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    confirmation_embed.set_footer(text=f"©2024 | OfficerMills™ | {central_tz}", icon_url="https://i.imgur.com/uQxfWpy.png")
                    
                    if log_channel:
                        await log_channel.send(embed=confirmation_embed)
                    
                    # Create detailed log embed
                    if log_channel:
                        log_embed = discord.Embed(
                            title="Automatic Release Log",
                            description=f"{member.mention} was automatically released after completing their sentence.",
                            color=discord.Color.green()
                        )
                        log_embed.add_field(name="Released By", value="Time Served", inline=True)
                        log_embed.add_field(name="Original Sentence", value=suspension[5], inline=True)
                        log_embed.add_field(name="Sentenced By", value=f"<@{suspension[2]}>", inline=True)
                        log_embed.add_field(name="Start Time", value=f"<t:{int(start_time.timestamp())}:F>", inline=True)
                        log_embed.add_field(name="Completion Time", value=f"<t:{int(datetime.now().timestamp())}:F>", inline=True)
                        log_embed.add_field(name="Time Served", value=time_served_display, inline=True)
                        log_embed.add_field(name="Inmate ID", value=f"||{user_id}||", inline=False)
                        log_embed.set_thumbnail(url=member.avatar.url if member.avatar else "")
                        
                        # Add your original footer with timestamp
                        central_tz = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        log_embed.set_footer(text=f"©2024 | OfficerMills™ | {central_tz}", icon_url="https://i.imgur.com/uQxfWpy.png")
                        await log_channel.send(embed=log_embed)
                
                except discord.Forbidden:
                    print(f"Could not restore roles for user {user_id}")
                except discord.HTTPException as e:
                    print(f"Error restoring roles for user {user_id}: {e}")
                
                # Mark suspension as ended
                db.end_suspension(user_id)
            
            # Update bot activity if any releases occurred
            if status_updated:
                await update_bot_activity()
        
        except Exception as e:
            print(f"Error in expired suspension check: {e}")
        
        # Check every 60 seconds
        await asyncio.sleep(60)

@bot.event
async def on_message(message):
    # Ignore bot messages
    if message.author.bot:
        return
    
    # Check if message is in court record channel
    if message.channel.id == COURT_RECORD_CHANNEL_ID:
        # Wait a moment to avoid rate limits, then re-send sticky message
        await asyncio.sleep(1)
        await send_sticky_message()
    
    # Process other commands
    await bot.process_commands(message)

@bot.event
async def on_ready():
    # Add the persistent view for buttons first
    bot.add_view(TimeRemainingView())
    
    # Sync commands WITHOUT clearing them
    try:
        synced = await bot.tree.sync()
        print(f'Logged in as {bot.user} (ID: {bot.user.id})')
        print(f'Synced {len(synced)} command(s):')
        for cmd in synced:
            print(f'  - /{cmd.name}: {cmd.description}')
    except Exception as e:
        print(f'Failed to sync commands: {e}')
    
    print('Database initialized and ready!')
    
    # Initialize sticky message system
    print('Initializing sticky message system...')
    await send_sticky_message()
    
    # Set initial bot activity
    await update_bot_activity()
    print('Bot activity status set!')
    print('Sticky message system ready!')
    
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
            title="Suspect Incarcerated",
            description=f"{member.mention} has been incarcerated for {duration}.",
            color=discord.Color.red()
        )
        embed.add_field(name="Sentenced By", value=interaction.user.mention)
        embed.add_field(name="Duration", value=duration)
        embed.set_thumbnail(url=member.avatar.url if member.avatar else "")
        
        # Add your original footer with timestamp
        central_tz = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        embed.set_footer(text=f"©2024 | OfficerMills™ | {central_tz}", icon_url="https://i.imgur.com/uQxfWpy.png")
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
            
            # Add your original footer with timestamp to LOG embed too
            central_tz = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_embed.set_footer(text=f"©2024 | OfficerMills™ | {central_tz}", icon_url="https://i.imgur.com/uQxfWpy.png")
            await log_channel.send(embed=log_embed)

        # Update bot activity status
        await update_bot_activity()

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
            title="Inmate Released",
            description=f"{member.mention} has been released and their roles have been restored.",
            color=discord.Color.green()
        )
        embed.add_field(name="Released By", value=interaction.user.mention)
        embed.set_thumbnail(url=member.avatar.url if member.avatar else "")
        
        # Add your original footer with timestamp
        central_tz = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        embed.set_footer(text=f"©2024 | OfficerMills™ | {central_tz}", icon_url="https://i.imgur.com/uQxfWpy.png")
        await interaction.followup.send(embed=embed)

        # Log the release
        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            log_embed = discord.Embed(
                title="Inmate Released",
                description=f"{member.mention} was released.",
                color=discord.Color.green()
            )
            log_embed.add_field(name="Released By", value=interaction.user.mention)
            log_embed.add_field(name="Inmate ID", value=f"||{member.id}||")
            log_embed.set_thumbnail(url=member.avatar.url if member.avatar else "")
            
            # Add your original footer with timestamp
            central_tz = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_embed.set_footer(text=f"©2024 | OfficerMills™ | {central_tz}", icon_url="https://i.imgur.com/uQxfWpy.png")
            await log_channel.send(embed=log_embed)

        # Update bot activity status
        await update_bot_activity()

    except discord.Forbidden:
        await interaction.followup.send(f"I don't have permission to manage roles for {member.mention}.")
    except discord.HTTPException as e:
        await interaction.followup.send(f"An error occurred while releasing {member.mention}: {e}")

if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"Error starting bot: {e}")