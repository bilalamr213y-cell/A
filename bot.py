import asyncio
import logging
import random
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

# قائمة بروكسيات موسعة
PROXY_LIST = [
    "http://43.134.20.79:3128",
    "http://47.251.43.113:8080",
    "http://8.219.97.248:80",
    "http://103.152.112.162:80",
    "http://198.23.239.134:80",
    "http://20.205.61.143:80",
    "http://47.254.153.183:80",
    "http://8.219.175.110:80",
    "http://161.35.70.249:8080",
    "http://165.22.254.40:8080",
    "http://138.68.60.8:8080",
    "http://206.189.144.184:8080",
    "http://64.225.8.121:8080",
    "http://159.65.133.197:8080",
    "http://167.99.234.199:8080",
    "http://139.59.1.139:8080",
    "http://104.248.63.15:8080",
    "http://157.245.92.194:8080",
    "http://178.128.89.177:8080",
    "http://143.198.228.250:8080"
]

ALGIERS_TZ = ZoneInfo("Africa/Algiers")
START_TIME = time(4, 0, 0)
END_TIME = time(6, 0, 0)
INTERVAL_SECONDS = 10
MAX_EMAILS = 5

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

target_emails = []  # قائمة لتخزين حتى 5 إيميلات
is_running = False
active_chat_id = None
scheduler_task = None
waiting_for_email_input = False

# Proxy Rotation Control (تغيير البروكسي كل إرسالين)
current_proxy = None
request_counter = 0

# Referral & Credits System
user_referrals = {}    # {user_id: count}
referred_by = {}       # {user_id: referrer_id}
user_burn_credits = {} # {user_id: credits_count}

REQUIRED_REFERRALS_PER_BURN = 5

def get_rotated_proxy():
    global current_proxy, request_counter
    if not PROXY_LIST:
        return None
    
    if current_proxy is None or request_counter >= 2:
        proxy_url = random.choice(PROXY_LIST)
        current_proxy = {
            "http": proxy_url,
            "https": proxy_url
        }
        request_counter = 0
    
    request_counter += 1
    return current_proxy

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
    
    proxies = get_rotated_proxy()
    
    try:
        session.get(INIT_URL, headers=headers, proxies=proxies, timeout=10)
        payload = {
            "app_id": "100067",
            "email": email,
            "locale": "en_DZ"
        }
        response = session.post(OTP_URL, headers=headers, data=payload, proxies=proxies, timeout=15)
        if response.status_code == 200:
            if '"result":0' in response.text or '"result": 0' in response.text:
                return True, response.text
            else:
                return False, f"Server response: {response.text}"
        else:
            return False, f"Server error status: {response.status_code} - {response.text}"
    except requests.exceptions.Timeout:
        return False, "Connection timeout (Proxy or Server)."
    except requests.exceptions.ConnectionError:
        return False, "Network connection failed (Proxy Error)."
    except Exception as err:
        return False, f"Error: {str(err)}"

def build_main_menu() -> InlineKeyboardMarkup:
    burn_button_text = "Stop Recovery Burn🛑" if is_running else "Start Recovery Burn🔥"
    burn_callback = "btn_stop_burn" if is_running else "btn_start_burn"
    
    keyboard = [
        [
            InlineKeyboardButton(f"➕ Add Email ({len(target_emails)}/{MAX_EMAILS})", callback_data="btn_add_email"),
            InlineKeyboardButton("🗑️ Clear All Emails", callback_data="btn_clear_emails")
        ],
        [InlineKeyboardButton("📋 View Email List", callback_data="btn_view_emails")],
        [InlineKeyboardButton(burn_button_text, callback_data=burn_callback)],
        [InlineKeyboardButton("👥 Referral System", callback_data="btn_referral")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_email_list_menu() -> InlineKeyboardMarkup:
    keyboard = []
    # إنشاء زر حذف بجانب كل إيميل
    for idx, email in enumerate(target_emails):
        keyboard.append([
            InlineKeyboardButton(f"📧 {email}", callback_data=f"email_info_{idx}"),
            InlineKeyboardButton("❌ Delete", callback_data=f"delete_email_{idx}")
        ])
    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="btn_main_menu")])
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
    global is_running, target_emails
    await send_telegram_alert(
        context,
        "⏰ **Scheduler Activated!**\nWaiting for 04:00 AM (Algeria Time) to begin dispatch sequence..."
    )
    
    while is_running and target_emails:
        now_algiers = datetime.now(ALGIERS_TZ)
        current_time = now_algiers.time()
        
        if START_TIME <= current_time <= END_TIME:
            for email in list(target_emails):
                if not is_running:
                    break
                success, details = await asyncio.to_thread(execute_garena_request, email)
                timestamp = now_algiers.strftime("%Y-%m-%d %H:%M:%S")
                if success:
                    msg = (
                        f"✅ **OTP Sent Successfully!**\n"
                        f"📧 Email: `{email}`\n"
                        f"🕒 Time: `{timestamp}` (Algeria Time)\n"
                        f"⏱️ Next attempt in 10 seconds."
                    )
                else:
                    msg = (
                        f"❌ **OTP Request Failed!**\n"
                        f"📧 Email: `{email}`\n"
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
        "• **Interval:** Every 10 seconds per email\n"
        "• **Max Emails Allowed:** Up to 5 Emails\n\n"
        "Use the interactive buttons below to control the bot:"
    )
    await update.message.reply_text(
        welcome_text,
        reply_markup=build_main_menu(),
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_running, target_emails, active_chat_id, scheduler_task, waiting_for_email_input
    query = update.callback_query
    await query.answer()
    active_chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if query.data == "btn_main_menu":
        await query.edit_message_text(
            "⚙️ **Main Menu:**",
            reply_markup=build_main_menu(),
            parse_mode="Markdown"
        )

    elif query.data == "btn_add_email":
        if len(target_emails) >= MAX_EMAILS:
            await query.edit_message_text(
                f"⚠️ **Limit Reached!** You can only add up to {MAX_EMAILS} emails.",
                reply_markup=build_main_menu(),
                parse_mode="Markdown"
            )
            return
            
        waiting_for_email_input = True
        await query.edit_message_text(
            f"📩 Please send target email address ({len(target_emails) + 1}/{MAX_EMAILS}):",
            parse_mode="Markdown"
        )

    elif query.data == "btn_view_emails":
        if not target_emails:
            await query.edit_message_text(
                "❌ **No emails added yet.** Use '➕ Add Email' to add emails.",
                reply_markup=build_main_menu(),
                parse_mode="Markdown"
            )
            return
            
        emails_formatted = "\n".join([f"{i+1}. `{email}`" for i, email in enumerate(target_emails)])
        text = (
            f"📋 **Target Email List ({len(target_emails)}/{MAX_EMAILS}):**\n\n"
            f"{emails_formatted}\n\n"
            "Select an email below to remove it:"
        )
        await query.edit_message_text(
            text,
            reply_markup=build_email_list_menu(),
            parse_mode="Markdown"
        )

    elif query.data.startswith("delete_email_"):
        idx = int(query.data.split("_")[-1])
        if 0 <= idx < len(target_emails):
            removed = target_emails.pop(idx)
            if not target_emails and is_running:
                is_running = False
                if scheduler_task:
                    scheduler_task.cancel()
                    scheduler_task = None
            
            if target_emails:
                emails_formatted = "\n".join([f"{i+1}. `{email}`" for i, email in enumerate(target_emails)])
                text = f"🗑️ Removed `{removed}`.\n\n📋 **Updated List:**\n{emails_formatted}"
                await query.edit_message_text(
                    text,
                    reply_markup=build_email_list_menu(),
                    parse_mode="Markdown"
                )
            else:
                await query.edit_message_text(
                    f"🗑️ Removed `{removed}`. List is now empty.",
                    reply_markup=build_main_menu(),
                    parse_mode="Markdown"
                )

    elif query.data == "btn_clear_emails":
        if not target_emails:
            await query.edit_message_text(
                "⚠️ **Email list is already empty!**",
                reply_markup=build_main_menu(),
                parse_mode="Markdown"
            )
            return
        
        if is_running:
            is_running = False
            if scheduler_task:
                scheduler_task.cancel()
                scheduler_task = None

        target_emails.clear()
        await query.edit_message_text(
            "🗑️ **All target emails deleted successfully.**",
            reply_markup=build_main_menu(),
            parse_mode="Markdown"
        )

    elif query.data == "btn_start_burn":
        if not target_emails:
            await query.edit_message_text(
                "❌ **No target emails set!** Please click '➕ Add Email' first.",
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
        emails_str = ", ".join([f"`{e}`" for e in target_emails])
        await query.edit_message_text(
            f"🚀 **Automation Scheduled!**\n"
            f"📧 Targets ({len(target_emails)}): {emails_str}\n"
            f"🕒 Active Window: 04:00 AM - 06:00 AM (Algeria Time)\n"
            f"⏱️ Interval: Every 10 seconds per email\n"
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
    global target_emails, waiting_for_email_input
    if waiting_for_email_input:
        new_email = update.message.text.strip()
        if new_email in target_emails:
            await update.message.reply_text(
                f"⚠️ Email `{new_email}` is already in the list!",
                reply_markup=build_main_menu(),
                parse_mode="Markdown"
            )
        else:
            target_emails.append(new_email)
            waiting_for_email_input = False
            await update.message.reply_text(
                f"🎯 Added: `{new_email}`\nTotal Emails: `{len(target_emails)}/{MAX_EMAILS}`",
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
    print("Bot is up and running with multi-email list support...")
    app.run_polling()
    