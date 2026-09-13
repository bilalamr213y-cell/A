import asyncio
import logging
import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

BOT_TOKEN = "8776921304:AAGRrWDoNy5WWib5V3_wkIlZD_nEttflvDc"
OTP_URL = "https://100067.connect.garena.com/game/account_security/swap:send_otp"
INIT_URL = "https://100067.connect.garena.com/game/account_security/"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

target_email = None
is_running = False
active_chat_id = None
sending_task = None
interval_seconds = 300

def execute_garena_request(email: str) -> tuple[bool, str]:
    session = requests.Session()
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://100067.connect.garena.com",
        "Referer": "https://100067.connect.garena.com/game/account_security/"
    }
    
    try:
        session.get(INIT_URL, headers=headers, timeout=10)
        
        payload = {
            "email": email
        }
        
        response = session.post(OTP_URL, headers=headers, data=payload, timeout=15)
        
        if response.status_code == 200:
            if '"result":0' in response.text:
                return True, response.text
            else:
                return False, f"Unexpected response: {response.text}"
        else:
            return False, f"Server error status: {response.status_code} - {response.text}"
            
    except requests.exceptions.Timeout:
        return False, "Connection timeout."
    except requests.exceptions.ConnectionError:
        return False, "Network or server connection failed."
    except Exception as err:
        return False, f"Unexpected error: {str(err)}"

async def auto_sender_loop(context: ContextTypes.DEFAULT_TYPE):
    global is_running, target_email, active_chat_id
    while is_running and target_email and active_chat_id:
        success, details = await asyncio.to_thread(execute_garena_request, target_email)
        if success:
            msg = f"✅ OTP sent successfully!\n📧 Email: `{target_email}`\n⏱️ Next request in 5 minutes."
        else:
            msg = f"❌ Failed to send OTP!\n📧 Email: `{target_email}`\n⚠️ Details: `{details}`\n🔄 Retrying in 5 minutes."
        try:
            await context.bot.send_message(chat_id=active_chat_id, text=msg, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Failed to send telegram message: {e}")
        await asyncio.sleep(interval_seconds)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "⚙️ **Free Fire OTP Bot Advanced Control Panel**\n\n"
        "Commands:\n"
        "1️⃣ `/set_email <email>` - Set target email.\n"
        "2️⃣ `/start_auto` - Start automated sending every 5 mins.\n"
        "3️⃣ `/stop_auto` - Stop automation.\n"
        "4️⃣ `/send_now` - Send one OTP immediately.\n"
        "5️⃣ `/status` - Check bot status."
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def set_email_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global target_email
    if not context.args:
        await update.message.reply_text("⚠️ Please provide an email.\nExample: `/set_email test@gmail.com`", parse_mode="Markdown")
        return
    target_email = context.args[0].strip()
    await update.message.reply_text(f"🎯 Target email set to: `{target_email}`", parse_mode="Markdown")

async def start_auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_running, target_email, active_chat_id, sending_task
    if not target_email:
        await update.message.reply_text("❌ Please set an email first using `/set_email`.", parse_mode="Markdown")
        return
    if is_running:
        await update.message.reply_text("⚠️ Automated sending is already running!", parse_mode="Markdown")
        return
    is_running = True
    active_chat_id = update.effective_chat.id
    await update.message.reply_text(f"🚀 Automation started successfully!\n📧 Target: `{target_email}`\n⏱️ Interval: 5 minutes.", parse_mode="Markdown")
    sending_task = asyncio.create_task(auto_sender_loop(context))

async def stop_auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_running, sending_task
    if not is_running:
        await update.message.reply_text("⚠️ Automation is already stopped.", parse_mode="Markdown")
        return
    is_running = False
    if sending_task:
        sending_task.cancel()
        sending_task = None
    await update.message.reply_text("🛑 Automation stopped successfully.", parse_mode="Markdown")

async def send_now_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global target_email
    if not target_email:
        await update.message.reply_text("❌ Set an email first using `/set_email`", parse_mode="Markdown")
        return
    await update.message.reply_text("⏳ Sending request now...")
    success, details = await asyncio.to_thread(execute_garena_request, target_email)
    if success:
        await update.message.reply_text(f"✅ Sent successfully!\n`{details}`", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Request failed!\n`{details}`", parse_mode="Markdown")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_str = "Running 🟢" if is_running else "Stopped 🔴"
    email_str = target_email if target_email else "Not set"
    msg = f"📊 **Bot Status:**\n• Status: {status_str}\n• Target Email: `{email_str}`\n• Interval: 5 minutes"
    await update.message.reply_text(msg, parse_mode="Markdown")

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("set_email", set_email_cmd))
    app.add_handler(CommandHandler("start_auto", start_auto_cmd))
    app.add_handler(CommandHandler("stop_auto", stop_auto_cmd))
    app.add_handler(CommandHandler("send_now", send_now_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    print("Advanced Bot is running...")
    app.run_polling()
    