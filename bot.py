import asyncio
import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BOT_TOKEN = "8776921304:AAGRrWDoNy5WWib5V3_wkIlZD_nEttflvDc"
OTP_URL = "https://100067.connect.garena.com/game/account_security/swap:send_otp"
INIT_URL = "https://100067.connect.garena.com/game/account_security/"

ALGIERS_TZ = ZoneInfo("Africa/Algiers")
START_TIME = time(4, 0, 0)
END_TIME = time(6, 0, 0)
INTERVAL_SECONDS = 300

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

target_email = None
is_running = False
active_chat_id = None
scheduler_task = None
waiting_for_email_input = False

def execute_garena_request(email: str) -> tuple[bool, str]:
    session = requests.Session()
    headers = {
        "User-Agent": "GarenaMSDK/4.0.42(22101316I ;Android 14;en;US;app 2.131.1 2019118334;)",
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Host": "100067.connect.garena.com",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip"
    }
    try:
        session.get(INIT_URL, headers=headers, timeout=10)
        payload = {
            "app_id": "100067",
            "email": email,
            "locale": "en_DZ"
        }
        response = session.post(OTP_URL, headers=headers, data=payload, timeout=15)
        if response.status_code == 200:
            if '"result":0' in response.text or '"result": 0' in response.text:
                return True, response.text
            else:
                return False, f"Server response: {response.text}"
        else:
            return False, f"Server error status: {response.status_code} - {response.text}"
    except requests.exceptions.Timeout:
        return False, "Connection timeout."
    except requests.exceptions.ConnectionError:
        return False, "Network connection failed."
    except Exception as err:
        return False, f"Error: {str(err)}"

def build_main_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📧 Set Target Email", callback_data="btn_set_email")],
        [
            InlineKeyboardButton("🚀 Start Automation (04:00 AM)", callback_data="btn_start_auto"),
            InlineKeyboardButton("🛑 Stop Automation", callback_data="btn_stop_auto")
        ],
        [
            InlineKeyboardButton("⚡ Send One OTP Now", callback_data="btn_send_now"),
            InlineKeyboardButton("📊 Check Status", callback_data="btn_status")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def send_telegram_alert(context: ContextTypes.DEFAULT_TYPE, message: str):
    global active_chat_id
    if active_chat_id:
        try:
            await context.bot.send_message(
                chat_id=active_chat_id,
                text=message,
                parse_mode="Markdown"
            )
        except Exception as err:
            logging.error(f"Failed to send alert: {err}")

async def scheduled_dispatcher_loop(context: ContextTypes.DEFAULT_TYPE):
    global is_running, target_email
    await send_telegram_alert(
        context,
        "⏰ **Scheduler Activated!**\nWaiting for 04:00 AM (Algeria Time) to begin dispatch sequence..."
    )
    
    while is_running and target_email:
        now_algiers = datetime.now(ALGIERS_TZ)
        current_time = now_algiers.time()
        
        if START_TIME <= current_time <= END_TIME:
            success, details = await asyncio.to_thread(execute_garena_request, target_email)
            timestamp = now_algiers.strftime("%Y-%m-%d %H:%M:%S")
            if success:
                msg = (
                    f"✅ **OTP Sent Successfully!**\n"
                    f"📧 Email: `{target_email}`\n"
                    f"🕒 Time: `{timestamp}` (Algeria Time)\n"
                    f"⏱️ Next attempt in 5 minutes."
                )
            else:
                msg = (
                    f"❌ **OTP Request Failed!**\n"
                    f"📧 Email: `{target_email}`\n"
                    f"🕒 Time: `{timestamp}` (Algeria Time)\n"
                    f"⚠️ Details: `{details}`\n"
                    f"🔄 Retrying in 5 minutes."
                )
            await send_telegram_alert(context, msg)
            await asyncio.sleep(INTERVAL_SECONDS)
        elif current_time > END_TIME:
            await send_telegram_alert(
                context,
                "🏁 **Daily Window Closed (06:00 AM reached).** Automation completed for today."
            )
            is_running = False
            break
        else:
            await asyncio.sleep(30)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_chat_id
    active_chat_id = update.effective_chat.id
    welcome_text = (
        "⚙️ **Free Fire Automated OTP Dispatcher**\n\n"
        "• **Schedule:** Every day from 04:00 AM to 06:00 AM (Algeria Time)\n"
        "• **Interval:** Every 5 minutes\n\n"
        "Use the interactive buttons below to control the bot:"
    )
    await update.message.reply_text(
        welcome_text,
        reply_markup=build_main_menu(),
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_running, target_email, active_chat_id, scheduler_task, waiting_for_email_input
    query = update.callback_query
    await query.answer()
    active_chat_id = update.effective_chat.id

    if query.data == "btn_set_email":
        waiting_for_email_input = True
        await query.edit_message_text(
            "📩 Please send the target email address in a message now:",
            parse_mode="Markdown"
        )

    elif query.data == "btn_start_auto":
        if not target_email:
            await query.edit_message_text(
                "❌ **No target email set!** Please click 'Set Target Email' first.",
                reply_markup=build_main_menu(),
                parse_mode="Markdown"
            )
            return
        if is_running:
            await query.edit_message_text(
                "⚠️ **Automation is already running!**",
                reply_markup=build_main_menu(),
                parse_mode="Markdown"
            )
            return
        is_running = True
        scheduler_task = asyncio.create_task(scheduled_dispatcher_loop(context))
        await query.edit_message_text(
            f"🚀 **Automation Scheduled!**\n"
            f"📧 Target: `{target_email}`\n"
            f"🕒 Active Window: 04:00 AM - 06:00 AM (Algeria Time)\n"
            f"⏱️ Interval: Every 5 minutes",
            reply_markup=build_main_menu(),
            parse_mode="Markdown"
        )

    elif query.data == "btn_stop_auto":
        if not is_running:
            await query.edit_message_text(
                "⚠️ **Automation is not active.**",
                reply_markup=build_main_menu(),
                parse_mode="Markdown"
            )
            return
        is_running = False
        if scheduler_task:
            scheduler_task.cancel()
            scheduler_task = None
        await query.edit_message_text(
            "🛑 **Automation stopped successfully.**",
            reply_markup=build_main_menu(),
            parse_mode="Markdown"
        )

    elif query.data == "btn_send_now":
        if not target_email:
            await query.edit_message_text(
                "❌ **No target email set!** Please set an email first.",
                reply_markup=build_main_menu(),
                parse_mode="Markdown"
            )
            return
        await query.edit_message_text("⏳ Processing manual OTP request...")
        success, details = await asyncio.to_thread(execute_garena_request, target_email)
        if success:
            res_msg = f"✅ **Instant OTP Sent!**\n`{details}`"
        else:
            res_msg = f"❌ **Manual Request Failed!**\n`{details}`"
        await context.bot.send_message(
            chat_id=active_chat_id,
            text=res_msg,
            reply_markup=build_main_menu(),
            parse_mode="Markdown"
        )

    elif query.data == "btn_status":
        now_algiers = datetime.now(ALGIERS_TZ).strftime("%Y-%m-%d %H:%M:%S")
        status_str = "Scheduled / Running 🟢" if is_running else "Stopped 🔴"
        email_str = target_email if target_email else "Not set"
        msg = (
            f"📊 **Bot Status Summary**\n\n"
            f"• **Status:** {status_str}\n"
            f"• **Target Email:** `{email_str}`\n"
            f"• **Time Window:** 04:00 AM - 06:00 AM\n"
            f"• **Current Algeria Time:** `{now_algiers}`"
        )
        await query.edit_message_text(
            msg,
            reply_markup=build_main_menu(),
            parse_mode="Markdown"
        )

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global target_email, waiting_for_email_input
    if waiting_for_email_input:
        target_email = update.message.text.strip()
        waiting_for_email_input = False
        await update.message.reply_text(
            f"🎯 Target email updated to: `{target_email}`",
            reply_markup=build_main_menu(),
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "Please use the buttons below to interact with the bot:",
            reply_markup=build_main_menu()
        )

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    print("Bot is up and running with buttons and scheduling...")
    app.run_polling()
    