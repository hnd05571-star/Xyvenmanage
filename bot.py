import os
import re
import json
import asyncio
import sqlite3
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
)

# ---------- LOAD ENVIRONMENT ----------
load_dotenv()

# For Render – set environment variable BOT_TOKEN
BOT_TOKEN = os.getenv("BOT_TOKEN", "8639978539:AAE3rkrUMELtU74Gn7XidLbz_z1lIp_vBX8")
OWNER_ID = int(os.getenv("OWNER_ID", "8745088070"))
DB_PATH = os.getenv("DB_PATH", "bot_data.db")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ---------- DATABASE ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            enabled INTEGER DEFAULT 1,
            settings TEXT DEFAULT '{}',
            welcome_text TEXT DEFAULT 'Welcome {first_name} to {group_name}!',
            welcome_enabled INTEGER DEFAULT 1,
            goodbye_text TEXT DEFAULT '{first_name} left the group.',
            goodbye_enabled INTEGER DEFAULT 0,
            rules TEXT DEFAULT '',
            captcha_enabled INTEGER DEFAULT 0,
            captcha_timeout INTEGER DEFAULT 120
        );
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT
        );
        CREATE TABLE IF NOT EXISTS warnings (
            chat_id INTEGER,
            user_id INTEGER,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS group_members (
            chat_id INTEGER,
            user_id INTEGER,
            verified INTEGER DEFAULT 0,
            join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS custom_filters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            trigger TEXT,
            response TEXT,
            created_by INTEGER
        );
        CREATE TABLE IF NOT EXISTS blocked_words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            word TEXT
        );
        CREATE TABLE IF NOT EXISTS locked_types (
            chat_id INTEGER,
            lock_type TEXT,
            PRIMARY KEY (chat_id, lock_type)
        );
        CREATE TABLE IF NOT EXISTS moderation_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            admin_id INTEGER,
            target_id INTEGER,
            action TEXT,
            reason TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()

def db_query(query, params=(), fetch_one=False, fetch_all=False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(query, params)
    if fetch_one:
        row = c.fetchone()
        conn.close()
        return dict(row) if row else None
    if fetch_all:
        rows = c.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    conn.commit()
    conn.close()

init_db()

# ---------- HELPERS ----------
def get_setting(chat_id, key, default):
    row = db_query("SELECT settings FROM groups WHERE chat_id = ?", (chat_id,), fetch_one=True)
    if row:
        settings = json.loads(row['settings'])
        return settings.get(key, default)
    return default

def set_setting(chat_id, key, value):
    row = db_query("SELECT settings FROM groups WHERE chat_id = ?", (chat_id,), fetch_one=True)
    settings = json.loads(row['settings']) if row else {}
    settings[key] = value
    db_query("INSERT OR REPLACE INTO groups (chat_id, settings) VALUES (?, ?)",
             (chat_id, json.dumps(settings)))

async def is_admin(chat_id, user_id, context):
    if user_id == OWNER_ID:
        return True
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except:
        return False

async def is_bot_admin(chat_id, context):
    try:
        member = await context.bot.get_chat_member(chat_id, context.bot.id)
        return member.status in ("administrator", "creator")
    except:
        return False

async def log_action(chat_id, admin_id, target_id, action, reason=""):
    db_query("INSERT INTO moderation_logs (chat_id, admin_id, target_id, action, reason) VALUES (?,?,?,?,?)",
             (chat_id, admin_id, target_id, action, reason))

def parse_user(arg):
    if arg.startswith('@'):
        return arg[1:]
    elif arg.isdigit():
        return int(arg)
    return None

# ---------- START / HELP ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Group Management Bot*\n\n"
        "I help you manage your group with powerful moderation, anti-spam, welcome, filters, and more.\n\n"
        "Use /help to see all commands.\n"
        "Add me as admin to get started.",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
📋 *Commands*

*General*
/start – Bot info
/help – This help
/id – Your ID
/rules – Group rules

*Moderation (reply to a message)*
/ban – Ban user
/unban – Unban user
/mute [time] – Mute (e.g., /mute 10m)
/unmute – Unmute
/kick – Kick
/warn – Warn (3 warnings → auto-mute)
/unwarn – Remove last warning
/warnings – Show warnings
/resetwarn – Reset warnings

*Admin Setup*
/setwelcome <text> – Set welcome message
/welcome on/off – Enable/disable welcome
/setgoodbye <text> – Set goodbye message
/goodbye on/off – Enable/disable goodbye
/setrules <text> – Set group rules
/delrules – Delete rules
/captcha on/off – Enable/disable captcha

*Filters & Locks*
/filter <trigger> <response> – Add custom filter
/stop <trigger> – Remove filter
/filters – List filters
/addword <word> – Add blocked word
/delword <word> – Remove blocked word
/words – List blocked words
/lock <type> – Lock media type (links, photos, gifs, etc.)
/unlock <type> – Unlock
/locks – Show current locks

*Cleanup & Pins*
/purge [count] – Delete recent messages (reply to start)
/del – Delete replied message
/pin – Pin replied message
/unpin – Unpin

*Info*
/userinfo – Show user details
/chatinfo – Show group info

*Settings*
/settings – Open settings dashboard

*Owner Only*
/admin – Owner panel
/broadcast – Broadcast to all groups (with confirmation)
/stats – Bot statistics
"""
    await update.message.reply_text(text, parse_mode="Markdown")

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🆔 Your ID: `{update.effective_user.id}`", parse_mode="Markdown")

# ---------- RULES ----------
async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = db_query("SELECT rules FROM groups WHERE chat_id = ?", (update.effective_chat.id,), fetch_one=True)
    text = row['rules'] if row and row['rules'] else "No rules set."
    await update.message.reply_text(f"📜 *Rules*\n{text}", parse_mode="Markdown")

async def setrules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        await update.message.reply_text("⛔ Admin only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /setrules <your rules text>")
        return
    text = " ".join(context.args)
    db_query("UPDATE groups SET rules = ? WHERE chat_id = ?", (text, update.effective_chat.id))
    await update.message.reply_text("✅ Rules updated.")

async def delrules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    db_query("UPDATE groups SET rules = '' WHERE chat_id = ?", (update.effective_chat.id,))
    await update.message.reply_text("✅ Rules deleted.")

# ---------- MODERATION COMMANDS ----------
def get_target(update):
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user.id
    return None

async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        await update.message.reply_text("⛔ Admin only.")
        return
    target = get_target(update)
    if not target:
        await update.message.reply_text("Reply to a user's message.")
        return
    reason = " ".join(context.args) if context.args else ""
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target)
        await log_action(update.effective_chat.id, update.effective_user.id, target, "ban", reason)
        await update.message.reply_text(f"🚫 Banned user `{target}`. Reason: {reason or 'None'}", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    target = get_target(update)
    if not target:
        await update.message.reply_text("Reply to a user's message.")
        return
    try:
        await context.bot.unban_chat_member(update.effective_chat.id, target)
        await log_action(update.effective_chat.id, update.effective_user.id, target, "unban")
        await update.message.reply_text(f"✅ Unbanned user `{target}`.", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    target = get_target(update)
    if not target:
        await update.message.reply_text("Reply to a user's message.")
        return
    duration = 5  # default minutes
    reason = ""
    if context.args:
        try:
            dur_str = context.args[0]
            if dur_str.endswith('m'):
                duration = int(dur_str[:-1])
            elif dur_str.endswith('h'):
                duration = int(dur_str[:-1]) * 60
            elif dur_str.endswith('d'):
                duration = int(dur_str[:-1]) * 1440
            else:
                duration = int(dur_str)
            reason = " ".join(context.args[1:]) if len(context.args) > 1 else ""
        except:
            reason = " ".join(context.args)
    until = datetime.now() + timedelta(minutes=duration)
    try:
        perms = ChatPermissions(can_send_messages=False)
        await context.bot.restrict_chat_member(update.effective_chat.id, target, perms, until_date=until)
        await log_action(update.effective_chat.id, update.effective_user.id, target, "mute", f"{duration}m {reason}")
        await update.message.reply_text(f"🔇 Muted user `{target}` for {duration} minutes. Reason: {reason or 'None'}", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    target = get_target(update)
    if not target:
        await update.message.reply_text("Reply to a user's message.")
        return
    perms = ChatPermissions(
        can_send_messages=True,
        can_send_media_messages=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_change_info=True,
        can_invite_users=True,
        can_pin_messages=True
    )
    try:
        await context.bot.restrict_chat_member(update.effective_chat.id, target, perms)
        await log_action(update.effective_chat.id, update.effective_user.id, target, "unmute")
        await update.message.reply_text(f"🔊 Unmuted user `{target}`.", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    target = get_target(update)
    if not target:
        await update.message.reply_text("Reply to a user's message.")
        return
    reason = " ".join(context.args) if context.args else ""
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target)
        await context.bot.unban_chat_member(update.effective_chat.id, target)
        await log_action(update.effective_chat.id, update.effective_user.id, target, "kick", reason)
        await update.message.reply_text(f"👢 Kicked user `{target}`. Reason: {reason or 'None'}", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    target = get_target(update)
    if not target:
        await update.message.reply_text("Reply to a user's message.")
        return
    chat_id = update.effective_chat.id
    try:
        member = await context.bot.get_chat_member(chat_id, target)
        if member.status in ("administrator", "creator"):
            await update.message.reply_text("Cannot warn an admin.")
            return
    except:
        pass
    reason = " ".join(context.args) if context.args else ""
    row = db_query("SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?", (chat_id, target), fetch_one=True)
    count = row['count'] + 1 if row else 1
    db_query("INSERT OR REPLACE INTO warnings (chat_id, user_id, count) VALUES (?, ?, ?)",
             (chat_id, target, count))
    await log_action(chat_id, update.effective_user.id, target, "warn", f"{count}/3 {reason}")
    await update.message.reply_text(f"⚠️ Warned user `{target}` (warnings: {count}/3). Reason: {reason or 'None'}", parse_mode="Markdown")
    if count >= 3:
        until = datetime.now() + timedelta(minutes=15)
        perms = ChatPermissions(can_send_messages=False)
        try:
            await context.bot.restrict_chat_member(chat_id, target, perms, until_date=until)
            await update.message.reply_text(f"🔇 Auto-muted `{target}` for 15 minutes (3 warnings).")
            db_query("DELETE FROM warnings WHERE chat_id = ? AND user_id = ?", (chat_id, target))
            await log_action(chat_id, update.effective_user.id, target, "auto_mute", "3 warnings")
        except Exception as e:
            await update.message.reply_text(f"Auto-mute failed: {e}")

async def unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    target = get_target(update)
    if not target:
        await update.message.reply_text("Reply to a user's message.")
        return
    chat_id = update.effective_chat.id
    row = db_query("SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?", (chat_id, target), fetch_one=True)
    if row and row['count'] > 0:
        new_count = row['count'] - 1
        if new_count == 0:
            db_query("DELETE FROM warnings WHERE chat_id = ? AND user_id = ?", (chat_id, target))
        else:
            db_query("UPDATE warnings SET count = ? WHERE chat_id = ? AND user_id = ?", (new_count, chat_id, target))
        await log_action(chat_id, update.effective_user.id, target, "unwarn", f"now {new_count}")
        await update.message.reply_text(f"✅ Removed one warning from `{target}`. Now {new_count} warnings.", parse_mode="Markdown")
    else:
        await update.message.reply_text("ℹ️ User has no warnings.")

async def warnings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    target = get_target(update)
    if not target:
        await update.message.reply_text("Reply to a user's message.")
        return
    row = db_query("SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?", (update.effective_chat.id, target), fetch_one=True)
    count = row['count'] if row else 0
    await update.message.reply_text(f"📊 User `{target}` has {count} warnings.", parse_mode="Markdown")

async def resetwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    target = get_target(update)
    if not target:
        await update.message.reply_text("Reply to a user's message.")
        return
    db_query("DELETE FROM warnings WHERE chat_id = ? AND user_id = ?", (update.effective_chat.id, target))
    await update.message.reply_text(f"✅ Reset warnings for `{target}`.", parse_mode="Markdown")

# ---------- WELCOME & GOODBYE ----------
async def setwelcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    if not context.args:
        await update.message.reply_text("Usage: /setwelcome Welcome {first_name} to {group_name}!\nVariables: {first_name}, {username}, {user_id}, {group_name}, {member_count}")
        return
    text = " ".join(context.args)
    db_query("UPDATE groups SET welcome_text = ? WHERE chat_id = ?", (text, update.effective_chat.id))
    await update.message.reply_text("✅ Welcome message updated.")

async def welcome_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.message.reply_text("Usage: /welcome on/off")
        return
    status = 1 if context.args[0].lower() == "on" else 0
    db_query("UPDATE groups SET welcome_enabled = ? WHERE chat_id = ?", (status, update.effective_chat.id))
    await update.message.reply_text(f"✅ Welcome {'enabled' if status else 'disabled'}.")

async def setgoodbye(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    if not context.args:
        await update.message.reply_text("Usage: /setgoodbye Goodbye {first_name}!")
        return
    text = " ".join(context.args)
    db_query("UPDATE groups SET goodbye_text = ? WHERE chat_id = ?", (text, update.effective_chat.id))
    await update.message.reply_text("✅ Goodbye message updated.")

async def goodbye_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.message.reply_text("Usage: /goodbye on/off")
        return
    status = 1 if context.args[0].lower() == "on" else 0
    db_query("UPDATE groups SET goodbye_enabled = ? WHERE chat_id = ?", (status, update.effective_chat.id))
    await update.message.reply_text(f"✅ Goodbye {'enabled' if status else 'disabled'}.")

# ---------- CAPTCHA ----------
async def captcha_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.message.reply_text("Usage: /captcha on/off")
        return
    status = 1 if context.args[0].lower() == "on" else 0
    db_query("UPDATE groups SET captcha_enabled = ? WHERE chat_id = ?", (status, update.effective_chat.id))
    await update.message.reply_text(f"✅ Captcha {'enabled' if status else 'disabled'}.")

# ---------- CUSTOM FILTERS ----------
async def add_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /filter <trigger> <response>")
        return
    trigger = context.args[0].lower()
    response = " ".join(context.args[1:])
    db_query("INSERT INTO custom_filters (chat_id, trigger, response, created_by) VALUES (?,?,?,?)",
             (update.effective_chat.id, trigger, response, update.effective_user.id))
    await update.message.reply_text(f"✅ Filter added: `{trigger}` → `{response}`", parse_mode="Markdown")

async def stop_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    if not context.args:
        await update.message.reply_text("Usage: /stop <trigger>")
        return
    trigger = context.args[0].lower()
    db_query("DELETE FROM custom_filters WHERE chat_id = ? AND trigger = ?", (update.effective_chat.id, trigger))
    await update.message.reply_text(f"✅ Filter removed: `{trigger}`", parse_mode="Markdown")

async def list_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    rows = db_query("SELECT trigger, response FROM custom_filters WHERE chat_id = ?", (update.effective_chat.id,), fetch_all=True)
    if rows:
        text = "📋 *Custom Filters*\n" + "\n".join([f"• `{r['trigger']}` → {r['response'][:30]}" for r in rows])
        await update.message.reply_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text("No custom filters set.")

# ---------- BLOCKED WORDS ----------
async def add_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    if not context.args:
        await update.message.reply_text("Usage: /addword <word>")
        return
    word = context.args[0].lower()
    db_query("INSERT INTO blocked_words (chat_id, word) VALUES (?, ?)", (update.effective_chat.id, word))
    await update.message.reply_text(f"✅ Blocked word added: `{word}`", parse_mode="Markdown")

async def del_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    if not context.args:
        await update.message.reply_text("Usage: /delword <word>")
        return
    word = context.args[0].lower()
    db_query("DELETE FROM blocked_words WHERE chat_id = ? AND word = ?", (update.effective_chat.id, word))
    await update.message.reply_text(f"✅ Blocked word removed: `{word}`", parse_mode="Markdown")

async def list_words(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    rows = db_query("SELECT word FROM blocked_words WHERE chat_id = ?", (update.effective_chat.id,), fetch_all=True)
    if rows:
        text = "🚫 *Blocked Words*\n" + "\n".join([f"• {r['word']}" for r in rows])
        await update.message.reply_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text("No blocked words.")

# ---------- LOCKS ----------
async def lock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    if not context.args:
        await update.message.reply_text("Usage: /lock <type>\nTypes: links, photos, gifs, videos, documents, voice, polls, games, forwards, commands")
        return
    lock_type = context.args[0].lower()
    db_query("INSERT OR IGNORE INTO locked_types (chat_id, lock_type) VALUES (?, ?)", (update.effective_chat.id, lock_type))
    await update.message.reply_text(f"🔒 Locked `{lock_type}`.", parse_mode="Markdown")

async def unlock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    if not context.args:
        await update.message.reply_text("Usage: /unlock <type>")
        return
    lock_type = context.args[0].lower()
    db_query("DELETE FROM locked_types WHERE chat_id = ? AND lock_type = ?", (update.effective_chat.id, lock_type))
    await update.message.reply_text(f"🔓 Unlocked `{lock_type}`.", parse_mode="Markdown")

async def locks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db_query("SELECT lock_type FROM locked_types WHERE chat_id = ?", (update.effective_chat.id,), fetch_all=True)
    if rows:
        text = "🔒 *Active Locks*\n" + "\n".join([f"• {r['lock_type']}" for r in rows])
        await update.message.reply_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text("🔓 No locks active.")

# ---------- PURGE / PIN ----------
async def purge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a message to start purging from.")
        return
    count = 10
    if context.args and context.args[0].isdigit():
        count = min(int(context.args[0]), 100)
    msg_id = update.message.reply_to_message.message_id
    deleted = 0
    for i in range(count):
        try:
            await context.bot.delete_message(update.effective_chat.id, msg_id + i)
            deleted += 1
            await asyncio.sleep(0.2)
        except:
            pass
    await update.message.reply_text(f"🧹 Deleted {deleted} messages.")

async def del_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    if update.message.reply_to_message:
        try:
            await context.bot.delete_message(update.effective_chat.id, update.message.reply_to_message.message_id)
            await update.message.delete()
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")

async def pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    if update.message.reply_to_message:
        try:
            await context.bot.pin_chat_message(update.effective_chat.id, update.message.reply_to_message.message_id)
            await update.message.reply_text("📌 Pinned message.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
    else:
        await update.message.reply_text("Reply to a message to pin.")

async def unpin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    try:
        await context.bot.unpin_chat_message(update.effective_chat.id)
        await update.message.reply_text("📌 Unpinned.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# ---------- USER INFO ----------
async def userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.effective_user
    if context.args:
        target_id = parse_user(context.args[0])
        if target_id:
            try:
                target = await context.bot.get_chat(target_id)
            except:
                await update.message.reply_text("User not found.")
                return
    text = f"👤 *User Info*\nID: `{target.id}`\nName: {target.full_name}"
    if target.username:
        text += f"\nUsername: @{target.username}"
    row = db_query("SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?", (update.effective_chat.id, target.id), fetch_one=True)
    if row:
        text += f"\nWarnings: {row['count']}"
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, target.id)
        text += f"\nStatus: {member.status}"
    except:
        pass
    await update.message.reply_text(text, parse_mode="Markdown")

async def chatinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    text = f"📊 *Group Info*\nTitle: {chat.title}\nID: `{chat.id}`"
    if chat.username:
        text += f"\nUsername: @{chat.username}"
    try:
        count = await context.bot.get_chat_member_count(chat.id)
        text += f"\nMembers: {count}"
    except:
        pass
    await update.message.reply_text(text, parse_mode="Markdown")

# ---------- SETTINGS DASHBOARD ----------
async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_chat.id, update.effective_user.id, context):
        return
    keyboard = [
        [InlineKeyboardButton("👋 Welcome", callback_data="sett_welcome"),
         InlineKeyboardButton("🔇 Goodbye", callback_data="sett_goodbye")],
        [InlineKeyboardButton("🛡️ Anti-Spam", callback_data="sett_antispam"),
         InlineKeyboardButton("🔗 Link Filter", callback_data="sett_links")],
        [InlineKeyboardButton("🚫 Bad Words", callback_data="sett_badwords"),
         InlineKeyboardButton("✅ Captcha", callback_data="sett_captcha")],
        [InlineKeyboardButton("⚠️ Warning Limit (3)", callback_data="sett_warnlimit")],
        [InlineKeyboardButton("📋 Rules", callback_data="sett_rules")],
    ]
    await update.message.reply_text("⚙️ *Settings* – Choose a category:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id

    if data == "sett_welcome":
        row = db_query("SELECT welcome_enabled FROM groups WHERE chat_id = ?", (chat_id,), fetch_one=True)
        current = row['welcome_enabled'] if row else 1
        new = 0 if current else 1
        db_query("UPDATE groups SET welcome_enabled = ? WHERE chat_id = ?", (new, chat_id))
        await query.edit_message_text(f"✅ Welcome {'enabled' if new else 'disabled'}.")
    elif data == "sett_goodbye":
        row = db_query("SELECT goodbye_enabled FROM groups WHERE chat_id = ?", (chat_id,), fetch_one=True)
        current = row['goodbye_enabled'] if row else 0
        new = 0 if current else 1
        db_query("UPDATE groups SET goodbye_enabled = ? WHERE chat_id = ?", (new, chat_id))
        await query.edit_message_text(f"✅ Goodbye {'enabled' if new else 'disabled'}.")
    elif data == "sett_antispam":
        row = db_query("SELECT settings FROM groups WHERE chat_id = ?", (chat_id,), fetch_one=True)
        settings = json.loads(row['settings']) if row else {}
        current = settings.get('antispam', True)
        settings['antispam'] = not current
        db_query("UPDATE groups SET settings = ? WHERE chat_id = ?", (json.dumps(settings), chat_id))
        await query.edit_message_text(f"🛡️ Anti-Spam {'enabled' if settings['antispam'] else 'disabled'}.")
    elif data == "sett_links":
        row = db_query("SELECT settings FROM groups WHERE chat_id = ?", (chat_id,), fetch_one=True)
        settings = json.loads(row['settings']) if row else {}
        current = settings.get('linkfilter', True)
        settings['linkfilter'] = not current
        db_query("UPDATE groups SET settings = ? WHERE chat_id = ?", (json.dumps(settings), chat_id))
        await query.edit_message_text(f"🔗 Link filter {'enabled' if settings['linkfilter'] else 'disabled'}.")
    elif data == "sett_badwords":
        row = db_query("SELECT settings FROM groups WHERE chat_id = ?", (chat_id,), fetch_one=True)
        settings = json.loads(row['settings']) if row else {}
        current = settings.get('badwords', True)
        settings['badwords'] = not current
        db_query("UPDATE groups SET settings = ? WHERE chat_id = ?", (json.dumps(settings), chat_id))
        await query.edit_message_text(f"🚫 Bad-word filter {'enabled' if settings['badwords'] else 'disabled'}.")
    elif data == "sett_captcha":
        row = db_query("SELECT captcha_enabled FROM groups WHERE chat_id = ?", (chat_id,), fetch_one=True)
        current = row['captcha_enabled'] if row else 0
        new = 0 if current else 1
        db_query("UPDATE groups SET captcha_enabled = ? WHERE chat_id = ?", (new, chat_id))
        await query.edit_message_text(f"✅ Captcha {'enabled' if new else 'disabled'}.")
    elif data == "sett_warnlimit":
        await query.edit_message_text("⚠️ Warning limit is fixed at 3 warnings → auto-mute (15 minutes).")
    elif data == "sett_rules":
        row = db_query("SELECT rules FROM groups WHERE chat_id = ?", (chat_id,), fetch_one=True)
        text = row['rules'] if row and row['rules'] else "No rules set."
        await query.edit_message_text(f"📜 *Rules*\n{text}", parse_mode="Markdown")

# ---------- ANTI-SPAM MESSAGE HANDLER ----------
flood_data = {}

async def group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or update.effective_chat.type != "group":
        return
    chat_id = update.effective_chat.id
    user = update.effective_user
    if not user or user.id == context.bot.id:
        return
    # Ensure group exists in DB
    db_query("INSERT OR IGNORE INTO groups (chat_id, title) VALUES (?, ?)", (chat_id, update.effective_chat.title))
    db_query("INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
             (user.id, user.username or "", user.first_name or ""))
    db_query("INSERT OR IGNORE INTO group_members (chat_id, user_id) VALUES (?, ?)", (chat_id, user.id))

    # Check if user is admin – skip all checks
    try:
        member = await context.bot.get_chat_member(chat_id, user.id)
        if member.status in ("administrator", "creator"):
            return
    except:
        pass

    # Load settings
    row = db_query("SELECT settings FROM groups WHERE chat_id = ?", (chat_id,), fetch_one=True)
    settings = json.loads(row['settings']) if row else {}
    antispam = settings.get('antispam', True)
    linkfilter = settings.get('linkfilter', True)
    badwords = settings.get('badwords', True)

    if not antispam:
        return

    # Flood detection
    key = f"{chat_id}_{user.id}"
    now = datetime.now()
    flood_data[key] = [t for t in flood_data.get(key, []) if (now - t).seconds < 10]
    if len(flood_data[key]) >= 5:  # 5 messages in 10 sec
        try:
            await update.message.delete()
        except:
            pass
        await warn_user(update, user.id)
        await update.message.reply_text("🐌 Flood detected – warning issued.")
        return
    flood_data[key].append(now)

    # Repeated message
    if not hasattr(context.bot_data, 'last_msgs'):
        context.bot_data['last_msgs'] = {}
    last = context.bot_data['last_msgs'].get(key)
    if last and last == update.message.text:
        try:
            await update.message.delete()
            await warn_user(update, user.id)
            await update.message.reply_text("♻️ Repeated message – warning.")
            return
        except:
            pass
    context.bot_data['last_msgs'][key] = update.message.text

    # Link filter
    if linkfilter and update.message.text:
        if re.search(r'(t\.me/|telegram\.me/|https?://[^\s]+)', update.message.text):
            try:
                await update.message.delete()
                await warn_user(update, user.id)
                await update.message.reply_text("🔗 Links not allowed – warning.")
                return
            except:
                pass

    # Bad words
    if badwords and update.message.text:
        bad_list = ['ass', 'fuck', 'shit', 'damn', 'bastard', 'bitch', 'idiot', 'stupid']
        if any(word in update.message.text.lower() for word in bad_list):
            try:
                await update.message.delete()
                await warn_user(update, user.id)
                await update.message.reply_text("🚫 Bad word – warning.")
                return
            except:
                pass

async def warn_user(update, user_id):
    chat_id = update.effective_chat.id
    row = db_query("SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?", (chat_id, user_id), fetch_one=True)
    count = row['count'] + 1 if row else 1
    db_query("INSERT OR REPLACE INTO warnings (chat_id, user_id, count) VALUES (?, ?, ?)",
             (chat_id, user_id, count))
    if count >= 3:
        until = datetime.now() + timedelta(minutes=15)
        perms = ChatPermissions(can_send_messages=False)
        try:
            await context.bot.restrict_chat_member(chat_id, user_id, perms, until_date=until)
            await update.message.reply_text(f"🔇 Auto-muted `{user_id}` for 15 min (3 warnings).", parse_mode="Markdown")
            db_query("DELETE FROM warnings WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        except:
            pass

# ---------- WELCOME / GOODBYE / CAPTCHA ----------
async def new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            continue
        chat_id = update.effective_chat.id
        db_query("INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
                 (member.id, member.username or "", member.first_name or ""))
        db_query("INSERT OR IGNORE INTO group_members (chat_id, user_id) VALUES (?, ?)", (chat_id, member.id))

        # Captcha
        row = db_query("SELECT captcha_enabled, captcha_timeout FROM groups WHERE chat_id = ?", (chat_id,), fetch_one=True)
        if row and row.get('captcha_enabled', 0) == 1:
            timeout = row.get('captcha_timeout', 120)
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Verify", callback_data=f"verify_{member.id}_{chat_id}")]])
            msg = await update.message.reply_text(
                f"Welcome {member.mention_html()}! Please verify you are human by clicking the button within {timeout} seconds.",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            asyncio.create_task(captcha_timeout(chat_id, member.id, timeout, msg.message_id))

        # Welcome
        row = db_query("SELECT welcome_enabled, welcome_text FROM groups WHERE chat_id = ?", (chat_id,), fetch_one=True)
        if row and row.get('welcome_enabled', 0) == 1:
            members = db_query("SELECT COUNT(*) as cnt FROM group_members WHERE chat_id = ?", (chat_id,), fetch_one=True)
            count = members['cnt'] if members else 0
            text = row['welcome_text'].format(
                first_name=member.first_name or "",
                username=member.username or "",
                user_id=member.id,
                group_name=update.effective_chat.title,
                member_count=count
            )
            await update.message.reply_text(text)

async def captcha_timeout(chat_id, user_id, timeout, msg_id):
    await asyncio.sleep(timeout)
    row = db_query("SELECT verified FROM group_members WHERE chat_id = ? AND user_id = ?", (chat_id, user_id), fetch_one=True)
    if not row or row.get('verified', 0) == 0:
        try:
            await context.bot.ban_chat_member(chat_id, user_id)
            await context.bot.send_message(chat_id, f"User {user_id} removed for not verifying captcha.")
        except:
            pass
    try:
        await context.bot.delete_message(chat_id, msg_id)
    except:
        pass

async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("verify_"):
        _, user_id, chat_id = data.split("_")
        user_id = int(user_id)
        chat_id = int(chat_id)
        if query.from_user.id == user_id:
            db_query("UPDATE group_members SET verified = 1 WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
            await query.edit_message_text("✅ You are verified! Welcome to the group.")
        else:
            await query.answer("This verification is not for you.", show_alert=True)

async def left_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.left_chat_members:
        if member.id == context.bot.id:
            continue
        chat_id = update.effective_chat.id
        row = db_query("SELECT goodbye_enabled, goodbye_text FROM groups WHERE chat_id = ?", (chat_id,), fetch_one=True)
        if row and row.get('goodbye_enabled', 0) == 1:
            text = row['goodbye_text'].format(
                first_name=member.first_name or "",
                username=member.username or "",
                user_id=member.id,
                group_name=update.effective_chat.title
            )
            await update.message.reply_text(text)

# ---------- OWNER PANEL ----------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Owner only.")
        return
    groups = db_query("SELECT COUNT(*) as count FROM groups", fetch_one=True)
    users = db_query("SELECT COUNT(DISTINCT user_id) as count FROM group_members", fetch_one=True)
    keyboard = [
        [InlineKeyboardButton("📊 Stats", callback_data="owner_stats")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="owner_broadcast")],
        [InlineKeyboardButton("📋 Group List", callback_data="owner_groups")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="owner_refresh")],
    ]
    await update.message.reply_text(
        f"👑 *Owner Panel*\nGroups: {groups['count']}\nUsers: {users['count']}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def owner_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != OWNER_ID:
        await query.edit_message_text("⛔ Owner only.")
        return
    data = query.data
    if data == "owner_stats":
        groups = db_query("SELECT COUNT(*) as count FROM groups", fetch_one=True)
        users = db_query("SELECT COUNT(DISTINCT user_id) as count FROM group_members", fetch_one=True)
        logs = db_query("SELECT COUNT(*) as count FROM moderation_logs", fetch_one=True)
        await query.edit_message_text(
            f"📊 *Bot Stats*\nGroups: {groups['count']}\nUsers: {users['count']}\nLog entries: {logs['count']}",
            parse_mode="Markdown"
        )
    elif data == "owner_broadcast":
        await query.edit_message_text("📢 Send your broadcast message now (type and send).")
        context.user_data['broadcast_step'] = 'awaiting_message'
    elif data == "owner_groups":
        rows = db_query("SELECT chat_id, title FROM groups LIMIT 50", fetch_all=True)
        if rows:
            text = "📋 *Groups (first 50)*\n" + "\n".join([f"• {r['title']} (ID: {r['chat_id']})" for r in rows])
            await query.edit_message_text(text, parse_mode="Markdown")
        else:
            await query.edit_message_text("No groups found.")
    elif data == "owner_refresh":
        await query.edit_message_text("Refreshed.")

async def broadcast_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    if context.user_data.get('broadcast_step') == 'awaiting_message':
        msg = update.message.text
        context.user_data['broadcast_msg'] = msg
        context.user_data['broadcast_step'] = 'confirm'
        keyboard = [
            [InlineKeyboardButton("✅ Confirm", callback_data="broadcast_confirm")],
            [InlineKeyboardButton("❌ Cancel", callback_data="broadcast_cancel")],
        ]
        await update.message.reply_text(f"📢 Send broadcast to all groups?\n\n`{msg}`", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != OWNER_ID:
        return
    data = query.data
    if data == "broadcast_confirm":
        msg = context.user_data.get('broadcast_msg', '')
        if not msg:
            await query.edit_message_text("No message to send.")
            return
        groups = db_query("SELECT chat_id FROM groups", fetch_all=True)
        sent = 0
        failed = 0
        for g in groups:
            try:
                await context.bot.send_message(g['chat_id'], f"📢 *Announcement*\n\n{msg}", parse_mode="Markdown")
                sent += 1
                await asyncio.sleep(0.07)
            except Exception as e:
                failed += 1
                logger.warning(f"Broadcast failed to {g['chat_id']}: {e}")
        await query.edit_message_text(f"✅ Broadcast completed.\nSent: {sent}\nFailed: {failed}\nTotal: {sent+failed}")
        context.user_data['broadcast_step'] = None
    elif data == "broadcast_cancel":
        await query.edit_message_text("❌ Broadcast cancelled.")
        context.user_data['broadcast_step'] = None

# ---------- STATS ----------
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Owner only.")
        return
    groups = db_query("SELECT COUNT(*) as count FROM groups", fetch_one=True)
    users = db_query("SELECT COUNT(DISTINCT user_id) as count FROM group_members", fetch_one=True)
    logs = db_query("SELECT COUNT(*) as count FROM moderation_logs", fetch_one=True)
    await update.message.reply_text(
        f"📊 *Bot Statistics*\nGroups: {groups['count']}\nUsers: {users['count']}\nLog entries: {logs['count']}",
        parse_mode="Markdown"
    )

# ---------- MAIN ----------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # General
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("rules", rules))
    app.add_handler(CommandHandler("setrules", setrules))
    app.add_handler(CommandHandler("delrules", delrules))

    # Moderation
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("mute", mute))
    app.add_handler(CommandHandler("unmute", unmute))
    app.add_handler(CommandHandler("kick", kick))
    app.add_handler(CommandHandler("warn", warn))
    app.add_handler(CommandHandler("unwarn", unwarn))
    app.add_handler(CommandHandler("warnings", warnings_cmd))
    app.add_handler(CommandHandler("resetwarn", resetwarn))

    # Welcome / Goodbye / Captcha
    app.add_handler(CommandHandler("setwelcome", setwelcome))
    app.add_handler(CommandHandler("welcome", welcome_toggle))
    app.add_handler(CommandHandler("setgoodbye", setgoodbye))
    app.add_handler(CommandHandler("goodbye", goodbye_toggle))
    app.add_handler(CommandHandler("captcha", captcha_toggle))

    # Filters & Words
    app.add_handler(CommandHandler("filter", add_filter))
    app.add_handler(CommandHandler("stop", stop_filter))
    app.add_handler(CommandHandler("filters", list_filters))
    app.add_handler(CommandHandler("addword", add_word))
    app.add_handler(CommandHandler("delword", del_word))
    app.add_handler(CommandHandler("words", list_words))

    # Locks
    app.add_handler(CommandHandler("lock", lock))
    app.add_handler(CommandHandler("unlock", unlock))
    app.add_handler(CommandHandler("locks", locks))

    # Cleanup & Pin
    app.add_handler(CommandHandler("purge", purge))
    app.add_handler(CommandHandler("del", del_msg))
    app.add_handler(CommandHandler("pin", pin))
    app.add_handler(CommandHandler("unpin", unpin))

    # Info
    app.add_handler(CommandHandler("userinfo", userinfo))
    app.add_handler(CommandHandler("chatinfo", chatinfo))

    # Settings
    app.add_handler(CommandHandler("settings", settings))

    # Owner
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("broadcast", broadcast_message_handler))
    app.add_handler(CommandHandler("stats", stats))

    # Callback queries
    app.add_handler(CallbackQueryHandler(settings_callback, pattern="^sett_"))
    app.add_handler(CallbackQueryHandler(owner_callback, pattern="^owner_"))
    app.add_handler(CallbackQueryHandler(broadcast_callback, pattern="^broadcast_"))
    app.add_handler(CallbackQueryHandler(verify_callback, pattern="^verify_"))

    # Message handlers
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, group_message_handler))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, left_member))
    # Broadcast listener (private)
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & filters.User(OWNER_ID), broadcast_message_handler))

    logger.info("🤖 Bot started polling...")
    app.run_polling(allowed_updates=["message", "callback_query", "chat_member"])

if __name__ == "__main__":
    main()
