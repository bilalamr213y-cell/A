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
ADMIN_ID = 7373420615  # 👑 معرف المطور الخاص بك

OTP_URL = "https://100067.connect.garena.com/game/account_security/swap:send_otp"
INIT_URL = "https://100067.connect.garena.com/game/account_security/"

ALGIERS_TZ = ZoneInfo("Africa/Algiers")
START_TIME = time(4, 0, 0)  # الساعة 4 صباحاً لبدء دورة جديدة وتصفير العدادات
INTERVAL_SECONDS = 10  # الفاصل الزمني بين كل إيميل
MAX_EMAILS = 5
MAX_DAILY_SENDS_PER_EMAIL = 20  # الحد الأقصى للمحاولات لكل إيميل

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

target_emails = []  
email_send_counts = {}  # تتبع عدد الإرسالات لكل إيميل
is_running = False
active_chat_id = None
scheduler_task = None
waiting_for_email_input = False
waiting_for_admin_credit_input = False

# نظام الإحالات والرصيد
user_referrals = {}    
referred_by = {}       
user_burn_credits = {} 
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

def build_main_menu(user_id: int) -> InlineKeyboardMarkup:
    burn_button_text = "🛑 Stop Recovery Burn" if is_running else "Start Recovery Burn🔥"
    burn_callback = "btn_stop_auto" if is_running else "btn_start_auto"
    
    keyboard = [
        [
            InlineKeyboardButton(f"➕ Add Email ({len(target_emails)}/{MAX_EMAILS})", callback_data="btn_set_email"),
            InlineKeyboardButton("🗑️ Delete Email", callback_data="btn_delete_menu")
        ],
        [InlineKeyboardButton("📋 View Email List", callback_data="btn_view_emails")],
        [
            InlineKeyboardButton(burn_button_text, callback_data=burn_callback),
            InlineKeyboardButton("⚡ Send One OTP Now", callback_data="btn_send_now")
        ],
        [
            InlineKeyboardButton("📊 Check Status", callback_data="btn_status"),
            InlineKeyboardButton("👥 Referral System", callback_data="btn_referral")
        ]
    ]
    
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("⚡ Admin: Add Credits", callback_data="btn_admin_add_credits")])

    return InlineKeyboardMarkup(keyboard)

def build_delete_menu() -> InlineKeyboardMarkup:
    keyboard = []
    for idx, email in enumerate(target_emails):
        keyboard.append([
            InlineKeyboardButton(f"❌ Delete: {email}", callback_data=f"delete_single_{idx}")
        ])
    keyboard.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="btn_main_menu")])
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
    global is_running, target_emails, email_send_counts
    
    await send_telegram_alert(
        context,
        "🚀 **Automation Started!**\nDispatching immediately. Each email will send up to 20 times, then wait for 04:00 AM to reset."
    )
    
    last_reset_day = None

    while is_running and target_emails:
        now_algiers = datetime.now(ALGIERS_TZ)
        current_time = now_algiers.time()
        current_day = now_algiers.date()

        # تصفير العدادات عند الساعة 4 صباحاً أو في بداية يوم جديد
        if current_time >= START_TIME and last_reset_day != current_day:
            email_send_counts = {email: 0 for email in target_emails}
            last_reset_day = current_day
            await send_telegram_alert(
                context,
                "🔄 **New Daily Window (04:00 AM reached).** All email limits have been reset to 0/20."
            )

        # التحقق مما إذا كانت كل الإيميلات قد استنفدت محاولاتها الـ 20
        all_completed = all(email_send_counts.get(email, 0) >= MAX_DAILY_SENDS_PER_EMAIL for email in target_emails)

        if all_completed:
            await send_telegram_alert(
                context,
                f"🏁 **All emails reached the limit ({MAX_DAILY_SENDS_PER_EMAIL} sends).** Waiting for 04:00 AM to reset and restart."
            )
            while is_running:
                now_check = datetime.now(ALGIERS_TZ)
                # إذا دخلنا وقت الساعة 4 صباحاً، نقوم بالتصفير ونكسر حلقة الانتظار
                if now_check.time() >= START_TIME and now_check.date() != last_reset_day:
                    email_send_counts = {email: 0 for email in target_emails}
                    last_reset_day = now_check.date()
                    break
                await asyncio.sleep(60)
            continue

        # إرسال دوري للإيميلات التي لم تصل للحد الأقصى
        for email in list(target_emails):
            if not is_running:
                break
            
            current_count = email_send_counts.get(email, 0)
            if current_count >= MAX_DAILY_SENDS_PER_EMAIL:
                continue  # تخطي الإيميل الذي أكمل 20 محاولة

            success, details = await asyncio.to_thread(execute_garena_request, email)
            email_send_counts[email] = current_count + 1
            new_count = email_send_counts[email]
            
            timestamp = datetime.now(ALGIERS_TZ).strftime("%Y-%m-%d %H:%M:%S")
            if success:
                msg = (
                    f"✅ **OTP Sent Successfully!**\n"
                    f"📧 Email: `{email}`\n"
                    f"📊 Progress: `{new_count}/{MAX_DAILY_SENDS_PER_EMAIL}`\n"
                    f"🕒 Time: `{timestamp}` (Algeria Time)\n"
                    f"⏱️ Next attempt in {INTERVAL_SECONDS} seconds."
                )
            else:
                msg = (
                    f"❌ **OTP Request Failed!**\n"
                    f"📧 Email: `{email}`\n"
                    f"📊 Progress: `{new_count}/{MAX_DAILY_SENDS_PER_EMAIL}`\n"
                    f"🕒 Time: `{timestamp}` (Algeria Time)\n"
                    f"⚠️ Details: `{details}`"
                )
            await send_telegram_alert(context, msg)
            await asyncio.sleep(INTERVAL_SECONDS)
        
        await asyncio.sleep(2)

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

    admin_tag = " (👑 Admin)" if user_id == ADMIN_ID else ""
    welcome_text = (
        f"⚙️ **Free Fire Automated OTP Dispatcher**{admin_tag}\n\n"
        "• **Schedule:** Starts immediately, up to 20 sends per email, resets daily at 04:00 AM (Algeria Time)\n"
        "• **Interval:** Every 10 seconds per email\n"
        "• **Max Emails Allowed:** Up to 5 Emails\n"
        "• **Connection:** Direct Railway Server IP\n\n"
        "Use the interactive buttons below to control the bot:"
    )
    await update.message.reply_text(
        welcome_text,
        reply_markup=build_main_menu(user_id),
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_running, target_emails, active_chat_id, scheduler_task, waiting_for_email_input, waiting_for_admin_credit_input, email_send_counts
    query = update.callback_query
    await query.answer()
    active_chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if query.data == "btn_main_menu":
        await query.edit_message_text(
            "⚙️ **Main Menu:**",
            reply_markup=build_main_menu(user_id),
            parse_mode="Markdown"
        )

    elif query.data == "btn_set_email":
        if len(target_emails) >= MAX_EMAILS:
            await query.edit_message_text(
                f"⚠️ **Limit Reached!** You can only add up to {MAX_EMAILS} emails.",
                reply_markup=build_main_menu(user_id),
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
                reply_markup=build_main_menu(user_id),
                parse_mode="Markdown"
            )
            return
            
        emails_formatted = "\n".join([f"{i+1}. `{email}` (Sent: {email_send_counts.get(email, 0)}/{MAX_EMAILS} -> {MAX_DAILY_SENDS_PER_EMAIL})" for i, email in enumerate(target_emails)])
        text = (
            f"📋 **Target Email List ({len(target_emails)}/{MAX_EMAILS}):**\n\n"
            f"{emails_formatted}"
        )
        await query.edit_message_text(
            text,
            reply_markup=build_main_menu(user_id),
            parse_mode="Markdown"
        )

    elif query.data == "btn_delete_menu":
        if not target_emails:
            await query.edit_message_text(
                "⚠️ **No emails available to delete!**",
                reply_markup=build_main_menu(user_id),
                parse_mode="Markdown"
            )
            return
        
        await query.edit_message_text(
            "🗑️ **Select the email you want to delete:**",
            reply_markup=build_delete_menu(),
            parse_mode="Markdown"
        )

    elif query.data.startswith("delete_single_"):
        idx = int(query.data.split("_")[-1])
        if 0 <= idx < len(target_emails):
            removed = target_emails.pop(idx)
            email_send_counts.pop(removed, None)
            if not target_emails and is_running:
                is_running = False
                if scheduler_task:
                    scheduler_task.cancel()
                    scheduler_task = None

            if target_emails:
                await query.edit_message_text(
                    f"🗑️ Deleted: `{removed}`\n\nSelect another email to delete or return to main menu:",
                    reply_markup=build_delete_menu(),
                    parse_mode="Markdown"
                )
            else:
                await query.edit_message_text(
                    f"🗑️ Deleted: `{removed}`\n\nAll emails have been removed.",
                    reply_markup=build_main_menu(user_id),
                    parse_mode="Markdown"
                )

    elif query.data == "btn_start_auto":
        if not target_emails:
            await query.edit_message_text(
                "❌ **No target emails set!** Please click '➕ Add Email' first.",
                reply_markup=build_main_menu(user_id),
                parse_mode="Markdown"
            )
            return
        if is_running:
            await query.edit_message_text(
                "⚠️ **Automation is already running!**",
                reply_markup=build_main_menu(user_id),
                parse_mode="Markdown"
            )
            return

        if user_id != ADMIN_ID:
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
                    reply_markup=build_main_menu(user_id),
                    parse_mode="Markdown"
                )
                return
            user_burn_credits[user_id] -= 1

        is_running = True
        scheduler_task = asyncio.create_task(scheduled_dispatcher_loop(context))
        emails_str = ", ".join([f"`{e}`" for e in target_emails])
        rem_credits_text = "∞ (Admin Unlimited)" if user_id == ADMIN_ID else f"`{user_burn_credits.get(user_id, 0)}`"
        
        await query.edit_message_text(
            f"🚀 **Automation Started!**\n"
            f"📧 Targets ({len(target_emails)}): {emails_str}\n"
            f"🔄 Limit: Max {MAX_DAILY_SENDS_PER_EMAIL} sends per email, resets daily at 04:00 AM\n"
            f"⏱️ Interval: Every 10 seconds per email\n"
            f"🎫 Remaining Burn Credits: {rem_credits_text}",
            reply_markup=build_main_menu(user_id),
            parse_mode="Markdown"
        )

    elif query.data == "btn_stop_auto":
        if not is_running:
            await query.edit_message_text(
                "⚠️ **Automation is not active.**",
                reply_markup=build_main_menu(user_id),
                parse_mode="Markdown"
            )
            return
        is_running = False
        if scheduler_task:
            scheduler_task.cancel()
            scheduler_task = None
        await query.edit_message_text(
            "🛑 **Automation stopped successfully.**",
            reply_markup=build_main_menu(user_id),
            parse_mode="Markdown"
        )

    elif query.data == "btn_send_now":
        if not target_emails:
            await query.edit_message_text(
                "❌ **No target emails set!** Please set at least one email first.",
                reply_markup=build_main_menu(user_id),
                parse_mode="Markdown"
            )
            return
        await query.edit_message_text("⏳ Processing manual OTP request for all target emails...")
        
        results_summary = []
        for email in target_emails:
            success, details = await asyncio.to_thread(execute_garena_request, email)
            if success:
                results_summary.append(f"✅ `{email}`: Success")
            else:
                results_summary.append(f"❌ `{email}`: Failed ({details})")
                
        res_msg = "📊 **Manual Request Results:**\n\n" + "\n".join(results_summary)
        await context.bot.send_message(
            chat_id=active_chat_id,
            text=res_msg,
            reply_markup=build_main_menu(user_id),
            parse_mode="Markdown"
        )

    elif query.data == "btn_status":
        now_algiers = datetime.now(ALGIERS_TZ).strftime("%Y-%m-%d %H:%M:%S")
        status_str = "Running 🟢" if is_running else "Stopped 🔴"
        emails_info = "\n".join([f"• `{e}`: {email_send_counts.get(e, 0)}/{MAX_DAILY_SENDS_PER_EMAIL}" for e in target_emails]) if target_emails else "Not set"
        msg = (
            f"📊 **Bot Status Summary**\n\n"
            f"• **Status:** {status_str}\n"
            f"• **Target Emails & Progress:**\n{emails_info}\n\n"
            f"• **Reset Time:** Daily at 04:00 AM (Algeria Time)\n"
            f"• **Current Algeria Time:** `{now_algiers}`"
        )
        await query.edit_message_text(
            msg,
            reply_markup=build_main_menu(user_id),
            parse_mode="Markdown"
        )

    elif query.data == "btn_referral":
        bot_username = (await context.bot.get_me()).username
        referral_link = f"https://t.me/{bot_username}?start={user_id}"
        total_refs = user_referrals.get(user_id, 0)
        credits = "∞ (Admin)" if user_id == ADMIN_ID else str(user_burn_credits.get(user_id, 0))
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
            reply_markup=build_main_menu(user_id),
            parse_mode="Markdown"
        )

    elif query.data == "btn_admin_add_credits":
        if user_id != ADMIN_ID:
            await query.answer("⛔ Access denied!", show_alert=True)
            return

        waiting_for_admin_credit_input = True
        await query.edit_message_text(
            "⚡ **Admin Mode: Add Credits**\n\n"
            "Please send the user ID and credit amount in this format:\n"
            "`<user_id> <amount>`\n\n"
            "Example: `7373420615 5`",
            parse_mode="Markdown"
        )

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global target_emails, waiting_for_email_input, waiting_for_admin_credit_input, email_send_counts
    user_id = update.effective_user.id

    if user_id == ADMIN_ID and waiting_for_admin_credit_input:
        text = update.message.text.strip().split()
        if len(text) == 2 and text[0].isdigit() and text[1].lstrip('-').isdigit():
            target_id = int(text[0])
            amount = int(text[1])
            user_burn_credits[target_id] = user_burn_credits.get(target_id, 0) + amount
            waiting_for_admin_credit_input = False

            await update.message.reply_text(
                f"✅ Added `{amount}` credits to user `{target_id}`.\n"
                f"New Balance: `{user_burn_credits[target_id]}`",
                reply_markup=build_main_menu(user_id),
                parse_mode="Markdown"
            )
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"🎉 **Admin Grant!** You have received `{amount}` Recovery Burn credits."
                )
            except Exception as err:
                logging.error(f"Failed to notify user: {err}")
        else:
            await update.message.reply_text(
                "⚠️ Invalid format. Please use: `<user_id> <amount>`\nExample: `7373420615 5`",
                parse_Mode="Markdown"
            )
        return

    if waiting_for_email_input:
        new_email = update.message.text.strip()
        if new_email in target_emails:
            await update.message.reply_text(
                f"⚠️ Email `{new_email}` is already in the list!",
                reply_markup=build_main_menu(user_id),
                parse_mode="Markdown"
            )
        else:
            target_emails.append(new_email)
            email_send_counts[new_email] = 0
            waiting_for_email_input = False
            await update.message.reply_text(
                f"🎯 Added: `{new_email}`\nTotal Emails: `{len(target_emails)}/{MAX_EMAILS}`",
                reply_markup=build_main_menu(user_id),
                parse_mode="Markdown"
            )
    else:
        await update.message.reply_text(
            "Please use the buttons below to interact with the bot:",
            reply_markup=build_main_menu(user_id)
        )

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    print("Bot is up and running with 20 limit per email and 4:00 AM daily reset...")
    app.run_polling()