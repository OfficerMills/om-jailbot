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
BACKGROUND_CHANNEL_ID = int(os.getenv('BACKGROUND_CHANNEL_ID'))
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
if not BACKGROUND_CHANNEL_ID:
    raise ValueError("BACKGROUND_CHANNEL_ID environment variable is required")
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
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
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
                timestamp TEXT DEFAULT (datetime('now')),
                details TEXT
            )
        ''')
        
        # Create sticky_messages table to persist sticky message IDs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sticky_messages (
                channel_id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL,
                last_updated TEXT DEFAULT (datetime('now'))
            )
        ''')
        
        # Create criminal_records table for comprehensive record keeping
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS criminal_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                sentenced_by INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                actual_end_time TEXT,
                duration_text TEXT NOT NULL,
                reason TEXT NOT NULL,
                released_by INTEGER,
                release_type TEXT DEFAULT 'TIME_SERVED'
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
        
        # Store as ISO format strings to avoid SQLite datetime issues
        start_time_str = start_time.isoformat()
        end_time_str = end_time.isoformat()
        
        cursor.execute('''
            INSERT OR REPLACE INTO suspensions 
            (user_id, guild_id, suspended_by, start_time, end_time, duration_text, previous_roles, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, guild_id, suspended_by, start_time_str, end_time_str, duration_text, roles_json, reason))
        
        # Add to logs
        cursor.execute('''
            INSERT INTO suspension_logs 
            (user_id, guild_id, action, performed_by, details, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, guild_id, "SUSPENDED", suspended_by, f"Duration: {duration_text}", start_time_str))
        
        # Add to criminal records
        cursor.execute('''
            INSERT INTO criminal_records 
            (user_id, guild_id, sentenced_by, start_time, end_time, duration_text, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, guild_id, suspended_by, start_time_str, end_time_str, duration_text, reason))
        
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
            WHERE is_active = 1
        ''')
        
        results = cursor.fetchall()
        conn.close()
        
        # Filter by end_time in Python to avoid SQLite datetime issues
        current_time = datetime.now()
        active_suspensions = []
        for result in results:
            try:
                if isinstance(result[4], str):
                    end_time = datetime.fromisoformat(result[4])
                else:
                    end_time = result[4]
                
                if end_time > current_time:
                    active_suspensions.append(result)
            except (ValueError, TypeError):
                continue
        
        return active_suspensions
    
    def get_expired_suspensions(self):
        """Get all expired suspensions that are still marked as active"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM suspensions 
            WHERE is_active = 1
        ''')
        
        results = cursor.fetchall()
        conn.close()
        
        # Filter by end_time in Python to avoid SQLite datetime issues
        current_time = datetime.now()
        expired_suspensions = []
        for result in results:
            try:
                if isinstance(result[4], str):
                    end_time = datetime.fromisoformat(result[4])
                else:
                    end_time = result[4]
                
                if end_time <= current_time:
                    expired_suspensions.append(result)
            except (ValueError, TypeError):
                # If we can't parse the datetime, consider it expired for safety
                expired_suspensions.append(result)
        
        return expired_suspensions
    
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
        current_time = datetime.now().isoformat()
        cursor.execute('''
            INSERT INTO suspension_logs 
            (user_id, guild_id, action, performed_by, details, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, suspension[1], action, ended_by or 0, "", current_time))
        
        # Update criminal records with actual end time and release info
        release_type = "MANUAL_RELEASE" if ended_by else "TIME_SERVED"
        cursor.execute('''
            UPDATE criminal_records 
            SET actual_end_time = ?, released_by = ?, release_type = ?
            WHERE user_id = ? AND actual_end_time IS NULL
        ''', (current_time, ended_by, release_type, user_id))
        
        conn.commit()
        conn.close()
        return suspension
    
    def get_previous_roles(self, user_id):
        """Get previous roles for a user"""
        suspension = self.get_active_suspension(user_id)
        if suspension:
            return json.loads(suspension[6])  # previous_roles column
        return []
    
    def get_criminal_record(self, user_id, guild_id):
        """Get complete criminal record for a user in a specific guild"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM criminal_records 
            WHERE user_id = ? AND guild_id = ?
        ''', (user_id, guild_id))
        
        results = cursor.fetchall()
        conn.close()
        
        # Sort by start_time in Python to avoid SQLite datetime issues
        def sort_key(record):
            try:
                start_time_str = record[4]  # start_time is at index 4
                if isinstance(start_time_str, str):
                    return datetime.fromisoformat(start_time_str)
                else:
                    return start_time_str
            except (ValueError, TypeError, IndexError):
                return datetime.min  # Put problematic records at the end
        
        # Sort in descending order (most recent first)
        try:
            results.sort(key=sort_key, reverse=True)
        except Exception as e:
            print(f"Error sorting criminal records: {e}")
            # Return unsorted if sorting fails
        
        return results
    
    def get_total_time_served(self, user_id, guild_id):
        """Calculate total time served by a user across all sentences"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT start_time, end_time, actual_end_time FROM criminal_records 
            WHERE user_id = ? AND guild_id = ?
        ''', (user_id, guild_id))
        
        records = cursor.fetchall()
        conn.close()
        
        total_seconds = 0
        for record in records:
            try:
                # Safely parse start_time
                if isinstance(record[0], str):
                    start_time = datetime.fromisoformat(record[0])
                else:
                    start_time = record[0]
                
                actual_end = record[2]  # actual_end_time
                scheduled_end = record[1]  # end_time
                
                if actual_end:
                    # They were released (either manually or automatically)
                    if isinstance(actual_end, str):
                        end_time = datetime.fromisoformat(actual_end)
                    else:
                        end_time = actual_end
                else:
                    # Still serving or record incomplete, use scheduled end
                    if isinstance(scheduled_end, str):
                        end_time = datetime.fromisoformat(scheduled_end)
                    else:
                        end_time = scheduled_end
                
                time_served = (end_time - start_time).total_seconds()
                total_seconds += max(0, time_served)  # Ensure non-negative
                
            except (ValueError, TypeError) as e:
                print(f"Error calculating time served for record {record}: {e}")
                continue  # Skip records with bad datetime data
        
        return total_seconds
    
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
        
        current_time = datetime.now().isoformat()
        cursor.execute('''
            INSERT OR REPLACE INTO sticky_messages 
            (channel_id, message_id, last_updated)
            VALUES (?, ?, ?)
        ''', (channel_id, message_id, current_time))
        
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

def format_time_duration(seconds):
    """Format seconds into a readable duration string"""
    if seconds < 60:
        return "Less than a minute"
    
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    
    parts = []
    if days > 0:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours > 0:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes > 0:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    
    return ", ".join(parts)

def format_role_list(guild, role_ids, list_type="removed"):
    """Format a list of roles for display in embeds"""
    if not role_ids:
        return f"No roles {list_type}"
    
    roles = [guild.get_role(role_id) for role_id in role_ids]
    valid_roles = [role for role in roles if role is not None]
    
    if not valid_roles:
        return f"No valid roles {list_type}"
    
    # Sort roles by position (highest to lowest)
    valid_roles.sort(key=lambda r: r.position, reverse=True)
    
    role_mentions = [role.mention for role in valid_roles]
    
    # If too many roles, truncate the list
    if len(role_mentions) > 10:
        displayed_roles = role_mentions[:10]
        remaining = len(role_mentions) - 10
        return "\n".join([f"• {role}" for role in displayed_roles]) + f"\n*...and {remaining} more*"
    else:
        return "\n".join([f"• {role}" for role in role_mentions])

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
        
        # Parse suspension data with safe datetime handling
        try:
            if isinstance(suspension[4], str):
                end_time = datetime.fromisoformat(suspension[4])
            else:
                end_time = suspension[4]
        except (ValueError, TypeError):
            await interaction.response.send_message(
                "❌ Error: Unable to parse your sentence data. Please contact an administrator.",
                ephemeral=True
            )
            return
        remaining_time = end_time - datetime.now()
        
        if remaining_time.total_seconds() <= 0:
            await interaction.response.send_message(
                "Your sentence has expired but hasn't been processed yet. You should be released shortly.",
                ephemeral=True
            )
            return
        
        # Format remaining time
        remaining_str = format_time_duration(remaining_time.total_seconds())
        
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
        if not any(role.id in ALLOWED_ROLES for role in interaction.user.roles):
            await interaction.response.send_message(
                "❌ **Access Denied**\nYou don't have permission to use this command.", 
                ephemeral=True
            )
            return False
        return True
    return discord.app_commands.check(predicate)

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
                    try:
                        if isinstance(suspension[3], str):
                            start_time = datetime.fromisoformat(suspension[3])
                        else:
                            start_time = suspension[3]
                    except (ValueError, TypeError):
                        start_time = datetime.now()  # Fallback
                    
                    actual_time_served = datetime.now() - start_time
                    time_served_display = format_time_duration(actual_time_served.total_seconds())
                    
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
                    
                    # Create detailed log embed with role information
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
                        
                        # Add roles restored field
                        roles_restored = format_role_list(guild, previous_role_ids, "restored")
                        log_embed.add_field(name="Roles Restored", value=roles_restored, inline=False)
                        
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
async def suspend(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str = "No reason provided"):
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
            current_roles,
            reason
        )

        # Send confirmation embed
        embed = discord.Embed(
            title="Suspect Incarcerated",
            description=f"{member.mention} has been incarcerated for {duration}.",
            color=discord.Color.red()
        )
        embed.add_field(name="Sentenced By", value=interaction.user.mention, inline=True)
        embed.add_field(name="Duration", value=duration, inline=True)
        embed.add_field(name="Reason", value=reason, inline=True)
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
            log_embed.add_field(name="Arresting Officer", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Duration", value=duration, inline=True)
            log_embed.add_field(name="Reason", value=reason, inline=True)
            
            # Add roles removed field
            role_ids = [role.id for role in current_roles]
            roles_removed = format_role_list(interaction.guild, role_ids, "removed")
            log_embed.add_field(name="Roles Removed", value=roles_removed, inline=False)
            
            log_embed.add_field(name="Inmate ID", value=f"||{member.id}||", inline=False)
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
            log_embed.add_field(name="Released By", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Original Sentence", value=suspension[5], inline=True)
            log_embed.add_field(name="Reason for Sentence", value=suspension[8] if suspension[8] else "No reason provided", inline=True)
            
            # Add roles restored field
            roles_restored = format_role_list(interaction.guild, previous_role_ids, "restored")
            log_embed.add_field(name="Roles Restored", value=roles_restored, inline=False)
            
            log_embed.add_field(name="Inmate ID", value=f"||{member.id}||", inline=False)
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

@bot.tree.command(name="background", description="View criminal record and background check for a user")
async def background_check(interaction: discord.Interaction, member: discord.Member):
    # Send a quick acknowledgment that will be deleted
    await interaction.response.send_message("🔍 Processing background check...", ephemeral=True)
    
    try:
        print(f"Starting background check for user {member.id} in guild {interaction.guild.id}")
        
        # Get the designated background channel
        background_channel = bot.get_channel(BACKGROUND_CHANNEL_ID)
        if not background_channel:
            await interaction.edit_original_response(content="❌ Error: Background check channel not found. Please contact an administrator.")
            return
        
        # Get criminal records for the user
        records = db.get_criminal_record(member.id, interaction.guild.id)
        print(f"Found {len(records)} criminal records")
        
        total_time_served = db.get_total_time_served(member.id, interaction.guild.id)
        print(f"Total time served: {total_time_served} seconds")
        
        # Create main embed
        main_embed = discord.Embed(
            title="🔍 CRIMINAL BACKGROUND CHECK",
            description=f"**Subject:** {member.mention}\n**User ID:** `{member.id}`\n**Guild:** {interaction.guild.name}\n**Requested by:** {interaction.user.mention}",
            color=discord.Color.dark_red()
        )
        
        # Set thumbnail to the subject's avatar
        main_embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
        
        # Basic information
        join_time_text = f"<t:{int(member.joined_at.timestamp())}:D>" if member.joined_at else "Unknown"
        main_embed.add_field(
            name="📋 **SUBJECT INFORMATION**",
            value=f"**Name:** {member.display_name}\n"
                  f"**Account Created:** <t:{int(member.created_at.timestamp())}:D>\n"
                  f"**Joined Server:** {join_time_text}",
            inline=False
        )
        
        # Criminal record summary
        if not records:
            main_embed.add_field(
                name="✅ **CRIMINAL RECORD STATUS**",
                value="```\n🟢 CLEAN RECORD\nNo criminal history found.\nSubject has no prior offenses.\n```",
                inline=False
            )
        else:
            total_sentences = len(records)
            total_time_formatted = format_time_duration(total_time_served)
            
            # Check if currently incarcerated
            current_suspension = db.get_active_suspension(member.id)
            status = "🔴 **CURRENTLY INCARCERATED**" if current_suspension else "🟡 **PREVIOUSLY INCARCERATED**"
            
            main_embed.add_field(
                name="⚠️ **CRIMINAL RECORD STATUS**",
                value=f"```\n{status}\nTotal Offenses: {total_sentences}\nTotal Time Served: {total_time_formatted}\n```",
                inline=False
            )
            
            # Current status if incarcerated
            if current_suspension:
                try:
                    end_time_raw = current_suspension[4]
                    if isinstance(end_time_raw, str):
                        end_time = datetime.fromisoformat(end_time_raw)
                    else:
                        end_time = end_time_raw
                    
                    remaining_time = end_time - datetime.now()
                    
                    if remaining_time.total_seconds() > 0:
                        remaining_formatted = format_time_duration(remaining_time.total_seconds())
                        main_embed.add_field(
                            name="🔒 **CURRENT INCARCERATION STATUS**",
                            value=f"```\n⚠️ SUBJECT IS CURRENTLY INCARCERATED\n\n"
                                  f"Sentence: {current_suspension[5]}\n"
                                  f"Time Remaining: {remaining_formatted}\n"
                                  f"Release Date: {end_time.strftime('%Y-%m-%d %H:%M')}\n"
                                  f"Reason: {current_suspension[8] if len(current_suspension) > 8 and current_suspension[8] else 'No reason provided'}\n```",
                            inline=False
                        )
                except (ValueError, TypeError, IndexError) as e:
                    print(f"Error parsing current suspension datetime: {e}")
                    # Add basic status without time calculations
                    main_embed.add_field(
                        name="🔒 **CURRENT INCARCERATION STATUS**",
                        value=f"```\n⚠️ SUBJECT IS CURRENTLY INCARCERATED\n\n"
                              f"Sentence: {current_suspension[5] if len(current_suspension) > 5 else 'Unknown'}\n"
                              f"Reason: {current_suspension[8] if len(current_suspension) > 8 and current_suspension[8] else 'No reason provided'}\n```",
                        inline=False
                    )
        
        # Footer information
        central_tz = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        main_embed.set_footer(
            text=f"©2024 | OfficerMills™ | {central_tz}",
            icon_url="https://i.imgur.com/uQxfWpy.png"
        )
        
        # Add timestamp
        main_embed.timestamp = datetime.now()
        
        # Send main embed to designated channel
        await background_channel.send(embed=main_embed)
        
        # If there are records, create offense history embeds
        if records:
            embeds_to_send = []
            current_embed = None
            offense_count = 0
            embed_count = 1
            
            for i, record in enumerate(records):
                print(f"Processing record {i+1}: {record}")
                offense_count += 1
                
                # Safely parse datetime fields with error handling
                try:
                    start_time_raw = record[4]  # start_time
                    print(f"Start time raw: {start_time_raw} (type: {type(start_time_raw)})")
                    
                    if isinstance(start_time_raw, str):
                        start_time = datetime.fromisoformat(start_time_raw)
                    else:
                        start_time = start_time_raw  # Already a datetime object
                    
                    end_time_raw = record[5]  # end_time  
                    print(f"End time raw: {end_time_raw} (type: {type(end_time_raw)})")
                    
                    if isinstance(end_time_raw, str):
                        scheduled_end = datetime.fromisoformat(end_time_raw)
                    else:
                        scheduled_end = end_time_raw  # Already a datetime object
                        
                except (ValueError, TypeError, IndexError) as e:
                    print(f"Error parsing datetime for record {record}: {e}")
                    continue  # Skip this record if datetime parsing fails
                
                try:
                    actual_end = record[6]  # actual_end_time
                    duration_text = record[7]
                    reason = record[8] if record[8] else "No reason provided"
                    sentenced_by = record[3]
                    released_by = record[9] if len(record) > 9 else None
                    release_type = record[10] if len(record) > 10 else None
                except IndexError as e:
                    print(f"Error accessing record fields: {e}")
                    continue
                
                # Calculate actual time served for this offense
                if actual_end:
                    try:
                        if isinstance(actual_end, str):
                            actual_end_time = datetime.fromisoformat(actual_end)
                        else:
                            actual_end_time = actual_end  # Already a datetime object
                        time_served = actual_end_time - start_time
                    except (ValueError, TypeError):
                        # Fallback if actual_end parsing fails
                        time_served = datetime.now() - start_time
                else:
                    # Still serving or incomplete record
                    time_served = datetime.now() - start_time
                
                time_served_formatted = format_time_duration(time_served.total_seconds())
                
                # Format release information
                if release_type == "MANUAL_RELEASE" and released_by:
                    release_info = f"Released by <@{released_by}>"
                elif release_type == "TIME_SERVED":
                    release_info = "Completed full sentence"
                else:
                    release_info = "Status unknown"
                
                # Create offense field value
                offense_value = (
                    f"```\n"
                    f"Charge: {reason}\n"
                    f"Sentenced: {duration_text}\n"
                    f"Time Served: {time_served_formatted}\n"
                    f"Date: {start_time.strftime('%Y-%m-%d %H:%M')}\n"
                    f"Officer: User ID {sentenced_by}\n"
                    f"Status: {release_info}\n"
                    f"```"
                )
                
                # Check if we need a new embed (Discord limit ~6000 chars per embed)
                if current_embed is None:
                    current_embed = discord.Embed(
                        title=f"📜 **OFFENSE HISTORY** - Page {embed_count}",
                        description=f"*Criminal record for {member.display_name} (continued)*" if embed_count > 1 else "*Listed chronologically (most recent first)*",
                        color=discord.Color.dark_red()
                    )
                    current_embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
                
                # Estimate the current embed size
                current_size = len(str(current_embed.to_dict()))
                field_size = len(f"🚨 **OFFENSE #{offense_count}**") + len(offense_value)
                
                # If adding this field would exceed Discord's limit (6000 chars), start a new embed
                if current_size + field_size > 5500:  # Leave some buffer
                    # Add footer to current embed and save it
                    current_embed.set_footer(
                        text=f"©2024 | OfficerMills™ | {central_tz} | Page {embed_count}",
                        icon_url="https://i.imgur.com/uQxfWpy.png"
                    )
                    embeds_to_send.append(current_embed)
                    
                    # Start new embed
                    embed_count += 1
                    current_embed = discord.Embed(
                        title=f"📜 **OFFENSE HISTORY** - Page {embed_count}",
                        description=f"*Criminal record for {member.display_name} (continued)*",
                        color=discord.Color.dark_red()
                    )
                    current_embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
                
                # Add the offense field (not inline)
                current_embed.add_field(
                    name=f"🚨 **OFFENSE #{offense_count}**",
                    value=offense_value,
                    inline=False
                )
            
            # Add the last embed if it has content
            if current_embed and len(current_embed.fields) > 0:
                # Show remaining offenses count if there were more than what we could display
                if len(records) > offense_count:
                    additional = len(records) - offense_count
                    current_embed.add_field(
                        name="📋 **ADDITIONAL RECORDS**",
                        value=f"```\n+{additional} older offense(s) truncated due to Discord limits.\nContact system administrator for complete criminal history.\n```",
                        inline=False
                    )
                
                current_embed.set_footer(
                    text=f"©2024 | OfficerMills™ | {central_tz} | Page {embed_count}",
                    icon_url="https://i.imgur.com/uQxfWpy.png"
                )
                embeds_to_send.append(current_embed)
            
            # Send all offense history embeds to designated channel
            for embed in embeds_to_send:
                await background_channel.send(embed=embed)
        
        # Update the ephemeral response to confirm completion
        await interaction.edit_original_response(content=f"✅ Background check for {member.display_name} completed. Results sent to {background_channel.mention}")
        
        print("Successfully created and sent all embeds to designated channel")
        
    except Exception as e:
        print(f"Error in background_check: {e}")
        import traceback
        traceback.print_exc()
        await interaction.edit_original_response(content=f"❌ An error occurred while retrieving criminal record: {e}")

if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"Error starting bot: {e}")