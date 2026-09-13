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
INTERVAL_SECONDS = 10

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

target_email = None
is_running = False
active_chat_id = None
scheduler_task = None
waiting_for_email_input = False

# Referral & Credits System
user_referrals = {}    # {user_id: count}
referred_by = {}       # {user_id: referrer_id}
user_burn_credits = {} # {user_id: credits_count}

REQUIRED_REFERRALS_PER_BURN = 5

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
    # زر التشغيل/الإيقاف المتبدل
    burn_button_text = "🛑Stop Recovery Burn" if is_running else "Start Recovery Burn🔥"
    burn_callback = "btn_stop_burn" if is_running else "btn_start_burn"
    
    keyboard = [
        [
            InlineKeyboardButton("➕ Add Email", callback_data="btn_add_email"),
            InlineKeyboardButton("🗑️ Delete Email", callback_data="btn_delete_email")
        ],
        [InlineKeyboardButton(burn_button_text, callback_data=burn_callback)],
        [InlineKeyboardButton("👥 Referral System", callback_data="btn_referral")]
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
                    f"⏱️ Next attempt in 10 seconds."
                )
            else:
                msg = (
                    f"❌ **OTP Request Failed!**\n"
                    f"📧 Email: `{target_email}`\n"
                    f"🕒 Time: `{timestamp}` (Algeria Time)\n"
                    f"⚠️ Details: `{details}`\n"
                    f"🔄 Retrying in 10 seconds."
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
            await asyncio.sleep(10)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_chat_id
    active_chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if context.args:
        referrer_id_str = context.args[0]
        if referrer_id_str.isdigit():
            referrer_id = int(referrer_id_str)
            if referrer_id != user_id and user_id not in referred_by:
                referred_by[user_id] = referrer_id
                user_referrals[referrer_id] = user_referrals.get(referrer_id, 0) + 1
                
                if user_referrals[referrer_id] % REQUIRED_REFERRALS_PER_BURN == 0:
                    user_burn_credits[referrer_id] = user_burn_credits.get(referrer_id, 0) + 1
                    try:
                        await context.bot.send_message(
                            chat_id=referrer_id,
                            text=f"🎉 **Congratulations!** You invited 5 new users!\n🔥 You unlocked 1 Recovery Burn session."
                        )
                    except Exception as err:
                        logging.error(f"Failed to notify referrer: {err}")

    welcome_text = (
        "⚙️ **Free Fire Automated OTP Dispatcher**\n\n"
        "• **Schedule:** Every day from 04:00 AM to 06:00 AM (Algeria Time)\n"
        "• **Interval:** Every 10 seconds\n\n"
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
    user_id = update.effective_user.id

    if query.data == "btn_add_email":
        waiting_for_email_input = True
        await query.edit_message_text(
            "📩 Please send the target email address in a message now:",
            parse_mode="Markdown"
        )

    elif query.data == "btn_delete_email":
        if not target_email:
            await query.edit_message_text(
                "⚠️ **No target email to delete!**",
                reply_markup=build_main_menu(),
                parse_mode="Markdown"
            )
            return
        
        # إذا كان الحرق يعمل، يتم إيقافه تلقائياً عند حذف الإيميل
        if is_running:
            is_running = False
            if scheduler_task:
                scheduler_task.cancel()
                scheduler_task = None

        target_email = None
        await query.edit_message_text(
            "🗑️ **Target email deleted successfully.**",
            reply_markup=build_main_menu(),
            parse_mode="Markdown"
        )

    elif query.data == "btn_start_burn":
        if not target_email:
            await query.edit_message_text(
                "❌ **No target email set!** Please click '➕ Add Email' first.",
                reply_markup=build_main_menu(),
                parse_mode="Markdown"
            )
            return
            
        credits = user_burn_credits.get(user_id, 0)
        if credits <= 0:
            total_refs = user_referrals.get(user_id, 0)
            needed = REQUIRED_REFERRALS_PER_BURN - (total_refs % REQUIRED_REFERRALS_PER_BURN)
            await query.edit_message_text(
                f"⚠️ **Access Denied! You do not have enough Recovery Burn Credits.**\n\n"
                f"• Every **5 referrals** = **1 Recovery Burn Session**\n"
                f"• Current Referrals: `{total_refs}`\n"
                f"• Referrals needed: `{needed}` more\n\n"
                f"Share your link via '👥 Referral System' to earn credits!",
                reply_markup=build_main_menu(),
                parse_mode="Markdown"
            )
            return

        user_burn_credits[user_id] -= 1
        is_running = True
        scheduler_task = asyncio.create_task(scheduled_dispatcher_loop(context))
        await query.edit_message_text(
            f"🚀 **Automation Scheduled!**\n"
            f"📧 Target: `{target_email}`\n"
            f"🕒 Active Window: 04:00 AM - 06:00 AM (Algeria Time)\n"
            f"⏱️ Interval: Every 10 seconds\n"
            f"🎫 Remaining Burn Credits: `{user_burn_credits[user_id]}`",
            reply_markup=build_main_menu(),
            parse_mode="Markdown"
        )

    elif query.data == "btn_stop_burn":
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

    elif query.data == "btn_referral":
        bot_username = (await context.bot.get_me()).username
        referral_link = f"https://t.me/{bot_username}?start={user_id}"
        total_refs = user_referrals.get(user_id, 0)
        credits = user_burn_credits.get(user_id, 0)
        progress = total_refs % REQUIRED_REFERRALS_PER_BURN
        
        ref_text = (
            f"👥 **Referral System**\n\n"
            f"Share your referral link with your friends to invite them:\n"
            f"🔗 `{referral_link}`\n\n"
            f"📊 **Your Stats:**\n"
            f"• Total Invites: `{total_refs}` user(s)\n"
            f"• Progress: `{progress}/{REQUIRED_REFERRALS_PER_BURN}` to next credit\n"
            f"• Available Burn Credits: `{credits}` session(s)"
        )
        await query.edit_message_text(
            ref_text,
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
    