import os
import asyncio
import logging
import threading
import time
from datetime import datetime
from flask import Flask, jsonify, request, render_template
from dotenv import load_dotenv
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler
from scraper import create_scraper
from otp_filter import otp_filter
from utils import format_otp_message, format_multiple_otps, get_status_message

# Load environment variables from .env file (if exists)
load_dotenv()
logger = logging.getLogger(__name__)

# ============= LOGGING CONFIGURATION =============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# ============= ENVIRONMENT VARIABLES (Read from Render) =============
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GROUP_ID = os.getenv('TELEGRAM_GROUP_ID')
IVASMS_EMAIL = os.getenv('IVASMS_EMAIL')
IVASMS_PASSWORD = os.getenv('IVASMS_PASSWORD')

# Log which variables are set (for debugging)
logger.info(f"TELEGRAM_BOT_TOKEN: {'✅ Set' if BOT_TOKEN else '❌ Missing'}")
logger.info(f"TELEGRAM_GROUP_ID: {'✅ Set' if GROUP_ID else '❌ Missing'}")
logger.info(f"IVASMS_EMAIL: {'✅ Set' if IVASMS_EMAIL else '❌ Missing'}")
logger.info(f"IVASMS_PASSWORD: {'✅ Set' if IVASMS_PASSWORD else '❌ Missing'}")

# Check if all required variables are present
missing_vars = []
if not BOT_TOKEN:
    missing_vars.append('TELEGRAM_BOT_TOKEN')
if not GROUP_ID:
    missing_vars.append('TELEGRAM_GROUP_ID')
if not IVASMS_EMAIL:
    missing_vars.append('IVASMS_EMAIL')
if not IVASMS_PASSWORD:
    missing_vars.append('IVASMS_PASSWORD')

if missing_vars:
    logger.error(f"❌ Missing environment variables: {', '.join(missing_vars)}")
    logger.error("Please set them in Render dashboard or create .env file")
else:
    logger.info("✅ All environment variables loaded successfully")

# Flask app for Render health checks
app = Flask(__name__)

# Bot statistics
bot_stats = {
    'start_time': datetime.now(),
    'total_otps_sent': 0,
    'last_check': 'Never',
    'last_error': None,
    'is_running': False
}

# Global bot instances
bot = None
updater = None
scraper = None

# ============= TELEGRAM COMMAND HANDLERS =============
def start_command(update, context):
    """Handle /start command"""
    welcome_message = """🤖 <b>Telegram OTP Bot</b>

🎯 <b>Available Commands:</b>
/start - Show this help message
/status - Show bot status and statistics
/check - Manually check for new OTPs
/test - Send a test OTP message
/stats - Show detailed statistics

🔐 <b>What I do:</b>
• Monitor IVASMS.com for new OTPs
• Send formatted OTPs to the group
• Prevent duplicate notifications
• Run 24/7 with automatic monitoring

📊 <b>Current Status:</b>
Bot is running and monitoring every 60 seconds."""

    update.message.reply_text(welcome_message, parse_mode='HTML')

def status_command(update, context):
    """Handle /status command"""
    uptime = datetime.now() - bot_stats['start_time']
    uptime_str = str(uptime).split('.')[0]
    
    cache_stats = otp_filter.get_cache_stats() if otp_filter else {'total_cached': 0}
    
    status_msg = f"""<b>📊 Bot Status</b>

⏱️ <b>Uptime:</b> {uptime_str}
📨 <b>OTPs Sent:</b> {bot_stats['total_otps_sent']}
🔄 <b>Last Check:</b> {bot_stats['last_check']}
💾 <b>Cache Size:</b> {cache_stats['total_cached']}
🤖 <b>Monitor:</b> {'🟢 Running' if bot_stats['is_running'] else '🔴 Stopped'}"""

    update.message.reply_text(status_msg, parse_mode='HTML')

def check_command(update, context):
    """Handle /check command"""
    update.message.reply_text("🔍 <b>Checking for new OTPs...</b>", parse_mode='HTML')
    
    try:
        check_and_send_otps()
        update.message.reply_text(
            f"✅ <b>OTP check completed!</b>\n\n"
            f"Last check: {bot_stats['last_check']}\n"
            f"Total OTPs sent: {bot_stats['total_otps_sent']}",
            parse_mode='HTML'
        )
    except Exception as e:
        update.message.reply_text(
            f"❌ <b>Error during OTP check:</b>\n<code>{str(e)}</code>",
            parse_mode='HTML'
        )

def test_command(update, context):
    """Handle /test command"""
    test_otp = {
        'otp': '123456',
        'phone': '+8801234567890',
        'service': 'Test Service',
        'timestamp': datetime.now().strftime('%H:%M:%S'),
        'raw_message': 'This is a test OTP message from the bot'
    }
    
    try:
        test_message = format_otp_message(test_otp)
        context.bot.send_message(
            chat_id=GROUP_ID,
            text=test_message,
            parse_mode='HTML'
        )
        update.message.reply_text(
            "✅ <b>Test message sent to the group!</b>",
            parse_mode='HTML'
        )
    except Exception as e:
        update.message.reply_text(
            f"❌ <b>Failed to send test message:</b>\n<code>{str(e)}</code>",
            parse_mode='HTML'
        )

def stats_command(update, context):
    """Handle /stats command"""
    uptime = datetime.now() - bot_stats['start_time']
    uptime_str = str(uptime).split('.')[0]
    
    cache_stats = otp_filter.get_cache_stats() if otp_filter else {'total_cached': 0, 'expire_minutes': 30}
    
    stats_message = f"""📊 <b>Detailed Bot Statistics</b>

⏱️ <b>Runtime Information:</b>
• Uptime: {uptime_str}
• Started: {bot_stats['start_time'].strftime('%Y-%m-%d %H:%M:%S')}
• Status: {'🟢 Running' if bot_stats['is_running'] else '🔴 Stopped'}

📨 <b>OTP Statistics:</b>
• Total OTPs Sent: {bot_stats['total_otps_sent']}
• Last Check: {bot_stats['last_check']}
• Cache Size: {cache_stats['total_cached']} items
• Cache Expiry: {cache_stats.get('expire_minutes', 30)} minutes

🔧 <b>System Information:</b>
• IVASMS Account: {'✅ Set' if IVASMS_EMAIL else '❌ Missing'}
• Target Group: {'✅ Set' if GROUP_ID else '❌ Missing'}
• Check Interval: 60 seconds
• Last Error: {bot_stats['last_error'] or 'None'}"""

    update.message.reply_text(stats_message, parse_mode='HTML')

def initialize_bot():
    """Initialize Telegram bot and scraper"""
    global bot, updater, scraper
    
    try:
        if not BOT_TOKEN:
            logger.error("BOT_TOKEN is missing!")
            return False
        
        if not GROUP_ID:
            logger.error("GROUP_ID is missing!")
            return False
        
        # Initialize bot with Updater (v13.7 style)
        updater = Updater(token=BOT_TOKEN, use_context=True)
        bot = updater.bot
        
        # Add command handlers
        dp = updater.dispatcher
        dp.add_handler(CommandHandler("start", start_command))
        dp.add_handler(CommandHandler("status", status_command))
        dp.add_handler(CommandHandler("check", check_command))
        dp.add_handler(CommandHandler("test", test_command))
        dp.add_handler(CommandHandler("stats", stats_command))
        
        logger.info("✅ Telegram bot initialized successfully")
        
        # Initialize scraper
        try:
            scraper = create_scraper(IVASMS_EMAIL, IVASMS_PASSWORD)
            if scraper:
                logger.info("✅ IVASMS scraper initialized successfully")
            else:
                logger.warning("⚠️ Failed to initialize IVASMS scraper")
        except Exception as e:
            logger.error(f"❌ Scraper initialization error: {e}")
            scraper = None
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize bot: {e}")
        bot_stats['last_error'] = str(e)
        return False

def send_telegram_message(message, parse_mode='HTML'):
    """Send message to Telegram group"""
    try:
        if not bot or not GROUP_ID:
            logger.error("Bot or Group ID not configured")
            return False
        
        bot.send_message(
            chat_id=GROUP_ID,
            text=message,
            parse_mode=parse_mode
        )
        
        logger.info("✅ Message sent to Telegram successfully")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to send Telegram message: {e}")
        bot_stats['last_error'] = str(e)
        return False

def check_and_send_otps():
    """Check for new OTPs and send to Telegram"""
    global bot_stats
    
    try:
        if not scraper:
            logger.error("Scraper not initialized")
            return
        
        # Fetch messages from IVASMS
        logger.info("🔍 Checking for new OTPs...")
        messages = scraper.fetch_messages()
        bot_stats['last_check'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if not messages:
            logger.info("📭 No messages found")
            return
        
        # Filter out duplicates
        new_messages = otp_filter.filter_new_otps(messages)
        
        if not new_messages:
            logger.info("📭 No new OTPs found (all were duplicates)")
            return
        
        logger.info(f"✨ Found {len(new_messages)} new OTPs")
        
        # Send messages to Telegram
        if len(new_messages) == 1:
            message = format_otp_message(new_messages[0])
        else:
            message = format_multiple_otps(new_messages)
        
        if send_telegram_message(message):
            bot_stats['total_otps_sent'] += len(new_messages)
            logger.info(f"✅ Successfully sent {len(new_messages)} OTPs to Telegram")
        else:
            logger.error("❌ Failed to send OTPs to Telegram")
        
    except Exception as e:
        logger.error(f"❌ Error in check_and_send_otps: {e}")
        bot_stats['last_error'] = str(e)

def background_monitor():
    """Background thread to monitor for OTPs"""
    global bot_stats
    
    bot_stats['is_running'] = True
    logger.info("🔄 Background OTP monitor started")
    
    while bot_stats['is_running']:
        try:
            check_and_send_otps()
            # Wait 60 seconds before next check
            time.sleep(60)
            
        except Exception as e:
            logger.error(f"❌ Error in background monitor: {e}")
            bot_stats['last_error'] = str(e)
            # Wait longer on error
            time.sleep(120)

# ============= FLASK ROUTES (for Render health checks) =============
@app.route('/')
def home():
    """Home route - health check"""
    return "OTP Bot is running! ✅", 200

@app.route('/debug')
def debug():
    """Debug endpoint to check environment variables"""
    return {
        "status": "running",
        "TELEGRAM_BOT_TOKEN": "✅ Set" if BOT_TOKEN else "❌ Missing",
        "TELEGRAM_GROUP_ID": "✅ Set" if GROUP_ID else "❌ Missing",
        "IVASMS_EMAIL": "✅ Set" if IVASMS_EMAIL else "❌ Missing",
        "IVASMS_PASSWORD": "✅ Set" if IVASMS_PASSWORD else "❌ Missing",
        "total_otps_sent": bot_stats['total_otps_sent'],
        "last_check": bot_stats['last_check']
    }

@app.route('/status')
def bot_status_api():
    """Status API endpoint"""
    return jsonify({
        'status': 'running',
        'total_otps_sent': bot_stats['total_otps_sent'],
        'last_check': bot_stats['last_check'],
        'last_error': bot_stats['last_error'],
        'monitor_running': bot_stats['is_running']
    })

@app.route('/check-otp')
def manual_check_api():
    """Manual OTP check endpoint"""
    try:
        check_and_send_otps()
        return jsonify({'status': 'success', 'message': 'OTP check completed'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ============= MAIN FUNCTION =============
def main():
    """Main function to start the bot"""
    logger.info("🚀 Starting Telegram OTP Bot...")
    
    # Initialize bot and scraper
    if not initialize_bot():
        logger.error("❌ Failed to initialize bot. Check your configuration.")
        # Keep Flask running for debugging
        port = int(os.environ.get('PORT', 10000))
        app.run(host='0.0.0.0', port=port, debug=False)
        return
    
    # Send startup message
    try:
        startup_message = """🚀 <b>Bot Started Successfully!</b>

✅ IVASMS scraper initialized
✅ Telegram bot connected
✅ Command handlers active
🔍 Monitoring for new OTPs every 60 seconds...

<i>Bot is now running and will automatically send new OTPs to this group.</i>"""
        send_telegram_message(startup_message)
    except Exception as e:
        logger.error(f"❌ Failed to send startup message: {e}")
    
    # Start background monitor
    monitor_thread = threading.Thread(target=background_monitor, daemon=True)
    monitor_thread.start()
    
    # Start Flask in MAIN thread (for Render)
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Starting Flask server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == '__main__':
    main()
