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
ADMIN_ID = 7373420615  # 👑 معرف المطور الخاص بك

OTP_URL = "https://100067.connect.garena.com/game/account_security/swap:send_otp"
INIT_URL = "https://100067.connect.garena.com/game/account_security/"

ALGIERS_TZ = ZoneInfo("Africa/Algiers")
START_TIME = time(4, 0, 0)
END_TIME = time(6, 0, 0)
INTERVAL_SECONDS = 10
MAX_EMAILS = 5

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

target_emails = []  # قائمة الإيميلات
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
    
    user_agents = [
        "GarenaMSDK/4.0.42(22101316I ;Android 14;en;US;app 2.131.1 2019118334;)",
        "GarenaMSDK/4.0.30(19120300 ;Android 12;ar;DZ;app 2.100.1;)",
        "Mozilla/5.0 (Linux; Android 13; Redmi Note 12 Pro 5G) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36"
    ]
    
    headers = {
        "User-Agent": random.choice(user_agents),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://100067.connect.garena.com",
        "Referer": "https://100067.connect.garena.com/game/account_security/",
        "Connection": "keep-alive"
    }
    
    payload = {
        "app_id": "100067",
        "email": email,
        "locale": "en_DZ",
        "format": "json"
    }
    
    try:
        # الاتصال المباشر باستخدام IP الاستضافة الأصلي (Railway)
        session.get(INIT_URL, headers=headers, timeout=8)
        response = session.post(OTP_URL, headers=headers, data=payload, timeout=10)
        
        if response.status_code == 200:
            if '"result":0' in response.text or '"result": 0' in response.text or '"error":0' in response.text:
                return True, "OTP Dispatched Successfully"
            elif "too_frequent" in response.text or "too many" in response.text:
                return False, "Rate Limited / Too Many Requests"
            else:
                return False, f"Garena Response: {response.text}"
        else:
            return False, f"HTTP Error Status: {response.status_code}"
            
    except requests.exceptions.Timeout:
        return False, "Connection timeout with Garena server."
    except Exception as err:
        return False, f"Error: {str(err)}"

def build_main_menu(user_id: int) -> InlineKeyboardMarkup:
    burn_button_text = "🛑 Stop Recovery Burn" if is_running else "Start Recovery Burn🔥"
    burn_callback = "btn_stop_burn" if is_running else "btn_start_burn"
    
    keyboard = [
        [
            InlineKeyboardButton(f"➕ Add Email ({len(target_emails)}/{MAX_EMAILS})", callback_data="btn_add_email"),
            InlineKeyboardButton("🗑️ Delete Email", callback_data="btn_delete_menu")
        ],
        [InlineKeyboardButton("📋 View Email List", callback_data="btn_view_emails")],
        [InlineKeyboardButton(burn_button_text, callback_data=burn_callback)],
        [InlineKeyboardButton("👥 Referral System", callback_data="btn_referral")]
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
    global is_running, target_emails
    await send_telegram_alert(
        context,
        "⏰ **Scheduler Activated!**\nWaiting for 04:00 AM (Algeria Time) to begin dispatch sequence..."
    )
    
    while is_running and target_emails:
        now_algiers = datetime.now(ALGIERS_TZ)
        current_time = now_algiers.time()
        
        # التأكد من العمل داخل النافذة الزمنية أو التجاوز للاختبار المباشر إذا تطلب الأمر
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
            # الانتظار حتى دخول الوقت المخصص
            await asyncio.sleep(15)

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
        "• **Schedule:** Every day from 04:00 AM to 06:00 AM (Algeria Time)\n"
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

async def add_credits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ **Access Denied!** Admin only command.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("⚠️ **Usage:** `/add <user_id> <amount>`", parse_mode="Markdown")
        return

    try:
        target_id = int(context.args[0])
        amount = int(context.args[1])
        user_burn_credits[target_id] = user_burn_credits.get(target_id, 0) + amount
        
        await update.message.reply_text(
            f"✅ **Success!** Added `{amount}` credits to user `{target_id}`.\n"
            f"Current Total: `{user_burn_credits[target_id]}`",
            parse_mode="Markdown"
        )
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=f"🎉 **Admin Grant!** You have received `{amount}` Recovery Burn credits."
            )
        except Exception as err:
            logging.error(f"Failed to notify target user: {err}")
    except ValueError:
        await update.message.reply_text("⚠️ Please enter valid numeric values.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_running, target_emails, active_chat_id, scheduler_task, waiting_for_email_input, waiting_for_admin_credit_input
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

    elif query.data == "btn_add_email":
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
            
        emails_formatted = "\n".join([f"{i+1}. `{email}`" for i, email in enumerate(target_emails)])
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

    elif query.data == "btn_start_burn":
        if not target_emails:
            await query.edit_message_text(
                "❌ **No target emails set!** Please click '➕ Add Email' first.",
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
            f"🚀 **Automation Scheduled!**\n"
            f"📧 Targets ({len(target_emails)}): {emails_str}\n"
            f"🕒 Active Window: 04:00 AM - 06:00 AM (Algeria Time)\n"
            f"⏱️ Interval: Every 10 seconds per email\n"
            f"🎫 Remaining Burn Credits: {rem_credits_text}",
            reply_markup=build_main_menu(user_id),
            parse_mode="Markdown"
        )

    elif query.data == "btn_stop_burn":
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
            "Example: `123456789 5`",
            parse_mode="Markdown"
        )

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global target_emails, waiting_for_email_input, waiting_for_admin_credit_input
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
                "⚠️ Invalid format. Please use: `<user_id> <amount>`\nExample: `123456789 5`",
                parse_mode="Markdown"
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
    app.add_handler(CommandHandler("add", add_credits_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    print("Bot is running with Direct Server IP & Active Scheduler...")
    app.run_polling()
