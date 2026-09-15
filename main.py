import asyncio
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import (
    BOT_TOKEN, ADMIN_ID, DATABASE_FILE,
    GARENA_BASE_URL, GARENA_APP_ID, GARENA_USER_AGENT,
    TIMEZONE, START_TIME_HOUR, INTERVAL_SECONDS,
    MAX_EMAILS, MAX_DAILY_SENDS_PER_EMAIL,
    REQUIRED_REFERRALS_PER_BURN, MAX_BIND_SEARCHES_PER_DAY,
    LOG_LEVEL, LOG_FORMAT,
)
from api_clients import (
    DatabaseClient, GarenaClient,
    is_valid_token, truncate_token, TZ,
)
from services import BindService, OtpService, ReferralService
import keyboards as kb

logging.basicConfig(format=LOG_FORMAT, level=getattr(logging, LOG_LEVEL, logging.INFO))
logger = logging.getLogger(__name__)

WAITING_INPUT = {}
RUNNING_TASKS = {}
USER_ACCOUNTS_CACHE = {}


def is_running(user_id: int) -> bool:
    task = RUNNING_TASKS.get(user_id)
    return task is not None and not task.done()


def get_services(context: ContextTypes.DEFAULT_TYPE):
    return (
        context.bot_data["bind_service"],
        context.bot_data["otp_service"],
        context.bot_data["referral_service"],
    )


async def show_main_menu(target, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    otp_service = context.bot_data["otp_service"]
    emails = otp_service.load_emails(user_id)
    running = is_running(user_id)
    markup = kb.main_menu(user_id, ADMIN_ID, running, len(emails), MAX_EMAILS)
    text = "Main Menu:"
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, reply_markup=markup)
    else:
        await target.reply_text(text, reply_markup=markup)


async def burn_loop(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    otp_service = context.bot_data["otp_service"]
    db = context.bot_data["db"]

    await context.bot.send_message(
        chat_id=user_id,
        text=f"Burn started. Each email up to {MAX_DAILY_SENDS_PER_EMAIL} sends daily.",
    )

    last_reset_day = db.get_last_reset(user_id)

    while True:
        if not is_running(user_id):
            return

        emails = otp_service.load_emails(user_id)
        if not emails:
            await context.bot.send_message(chat_id=user_id, text="No emails. Stopping.")
            return

        now = datetime.now(TZ)
        today = now.strftime("%Y-%m-%d")

        if now.hour >= START_TIME_HOUR and last_reset_day != today:
            otp_service.reset_counts(user_id)
            db.set_last_reset(user_id, today)
            last_reset_day = today
            await context.bot.send_message(
                chat_id=user_id,
                text=f"Daily reset - {now.strftime('%Y-%m-%d %H:%M')}",
            )

        if otp_service.all_reached_limit(user_id):
            await context.bot.send_message(
                chat_id=user_id,
                text=f"All emails reached {MAX_DAILY_SENDS_PER_EMAIL}. Waiting for reset at {START_TIME_HOUR}:00.",
            )
            await asyncio.sleep(300)
            continue

        for email in emails:
            if not is_running(user_id):
                return
            current = otp_service.get_count(user_id, email)
            if current >= MAX_DAILY_SENDS_PER_EMAIL:
                continue

            success, details = await asyncio.to_thread(otp_service.send_one, email)
            new_count = current + 1
            otp_service.set_count(user_id, email, new_count)

            ts = datetime.now(TZ).strftime("%H:%M:%S")
            icon = "OK" if success else "FAIL"
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"[{icon}] {email} - {new_count}/{MAX_DAILY_SENDS_PER_EMAIL} - {ts}",
                )
            except Exception as e:
                logger.error(f"alert send: {e}")

            await asyncio.sleep(INTERVAL_SECONDS)

        await asyncio.sleep(2)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    referral_service = context.bot_data["referral_service"]

    if context.args and context.args[0].isdigit():
        referrer = int(context.args[0])
        result = referral_service.register(user_id, referrer)
        if result["success"] and result["credit"]:
            try:
                await context.bot.send_message(
                    chat_id=referrer,
                    text=f"You earned 1 credit! ({REQUIRED_REFERRALS_PER_BURN} referrals reached)",
                )
            except Exception:
                pass

    admin_tag = " [Admin]" if user_id == ADMIN_ID else ""
    await update.message.reply_text(
        f"Free Fire Recovery Bot{admin_tag}\n\nChoose an option:",
        reply_markup=kb.main_menu(user_id, ADMIN_ID, False, 0, MAX_EMAILS),
    )


async def bind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /bind <access_token>")
        return

    token = context.args[0].strip().lower()
    user_id = update.effective_user.id

    if not is_valid_token(token):
        await update.message.reply_text("Invalid token. Must be 64 hex chars.")
        return

    wait = await update.message.reply_text("Searching...")

    bind_service = context.bot_data["bind_service"]
    result = await bind_service.search(token, user_id)

    if not result["success"]:
        errors = {
            "daily_limit": "Daily limit reached.",
            "invalid_token": "Invalid token or no data.",
            "no_binding": "Account has no email binding.",
        }
        await wait.edit_text(errors.get(result["error"], "Unknown error."))
        return

    await wait.delete()
    text = (
        f"Account found\n\n"
        f"Email: {result['email']}\n"
        f"Mobile: {result['mobile'] or '-'}\n"
        f"Token: {truncate_token(token)}"
    )
    await update.message.reply_text(text, reply_markup=kb.control_menu(token))


async def addcredits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /addcredits <user_id> <amount>")
        return
    try:
        target = int(context.args[0])
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Invalid numbers.")
        return

    referral_service = context.bot_data["referral_service"]
    total = referral_service.add_credits(target, amount)
    await update.message.reply_text(f"Added {amount} credits to {target}. Total: {total}")
    try:
        await context.bot.send_message(
            chat_id=target,
            text=f"Admin granted {amount} credit(s).",
        )
    except Exception:
        pass


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

    bind_service, otp_service, referral_service = get_services(context)

    if data == "btn_main_menu":
        await show_main_menu(query, user_id, context)
        return

    if data == "btn_start_burn":
        if is_running(user_id):
            await query.answer("Already running!", show_alert=True)
            return
        emails = otp_service.load_emails(user_id)
        if not emails:
            await query.edit_message_text(
                "Add an email first.",
                reply_markup=kb.main_menu(user_id, ADMIN_ID, False, 0, MAX_EMAILS),
            )
            return

        if user_id != ADMIN_ID:
            if not referral_service.use_credit(user_id):
                stats = referral_service.stats(user_id)
                await query.edit_message_text(
                    f"Need a credit.\n\n"
                    f"Your referrals: {stats['referrals']}\n"
                    f"Progress: {stats['progress']}/{REQUIRED_REFERRALS_PER_BURN}\n\n"
                    f"Get {REQUIRED_REFERRALS_PER_BURN} referrals for 1 credit.",
                    reply_markup=kb.main_menu(user_id, ADMIN_ID, False, len(emails), MAX_EMAILS),
                )
                return

        task = asyncio.create_task(burn_loop(user_id, context))
        RUNNING_TASKS[user_id] = task

        await query.edit_message_text(
            f"Burn started - {len(emails)} email(s)",
            reply_markup=kb.main_menu(user_id, ADMIN_ID, True, len(emails), MAX_EMAILS),
        )
        return

    if data == "btn_stop_burn":
        task = RUNNING_TASKS.pop(user_id, None)
        if task and hasattr(task, "cancel"):
            task.cancel()
        emails = otp_service.load_emails(user_id)
        await query.edit_message_text(
            "Stopped.",
            reply_markup=kb.main_menu(user_id, ADMIN_ID, False, len(emails), MAX_EMAILS),
        )
        return

    if data == "btn_add_account":
        WAITING_INPUT[user_id] = "token"
        await query.edit_message_text("Send the access_token (64 hex chars).")
        return

    if data == "btn_my_accounts":
        accounts = bind_service.get_user_accounts(user_id)
        USER_ACCOUNTS_CACHE[user_id] = accounts
        if not accounts:
            await query.edit_message_text(
                "No saved accounts.",
                reply_markup=kb.main_menu(user_id, ADMIN_ID, is_running(user_id), 0, MAX_EMAILS),
            )
            return
        await query.edit_message_text(
            f"Your accounts ({len(accounts)}):",
            reply_markup=kb.accounts_menu(accounts),
        )
        return

    if data == "btn_control_menu":
        accounts = bind_service.get_user_accounts(user_id)
        USER_ACCOUNTS_CACHE[user_id] = accounts
        if not accounts:
            await query.edit_message_text(
                "No accounts.",
                reply_markup=kb.main_menu(user_id, ADMIN_ID, False, 0, MAX_EMAILS),
            )
            return
        await query.edit_message_text(
            "Choose account:",
            reply_markup=kb.accounts_menu(accounts),
        )
        return

    if data.startswith("acc_select_"):
        idx = int(data.split("_")[-1])
        accounts = USER_ACCOUNTS_CACHE.get(user_id, [])
        if idx >= len(accounts):
            await query.answer("Not found", show_alert=True)
            return
        acc = accounts[idx]
        token = acc["token"]
        text = (
            f"Selected account\n\n"
            f"Email: {acc.get('email', '?')}\n"
            f"Mobile: {acc.get('mobile') or '-'}\n"
            f"Token: {truncate_token(token)}"
        )
        await query.edit_message_text(text, reply_markup=kb.control_menu(token))
        return

    if data.startswith("ctrl_info_"):
        token_short = data.replace("ctrl_info_", "")
        accounts = USER_ACCOUNTS_CACHE.get(user_id, [])
        acc = None
        for a in accounts:
            if a["token"].startswith(token_short):
                acc = a
                break
        if not acc:
            await query.answer("Not found", show_alert=True)
            return
        text = (
            f"Full Info\n\n"
            f"Email: {acc.get('email', '?')}\n"
            f"Mobile: {acc.get('mobile') or '-'}\n"
            f"Token: {acc.get('token', '?')}\n"
            f"Saved: {acc.get('timestamp', '-')}"
        )
        await query.edit_message_text(text, reply_markup=kb.control_menu(acc["token"]))
        return

    if data.startswith("ctrl_otp_"):
        token_short = data.replace("ctrl_otp_", "")
        accounts = USER_ACCOUNTS_CACHE.get(user_id, [])
        acc = None
        for a in accounts:
            if a["token"].startswith(token_short):
                acc = a
                break
        if not acc:
            await query.answer("Not found", show_alert=True)
            return
        email = acc.get("email")
        await query.answer("Sending...")
        success, _ = await asyncio.to_thread(otp_service.send_one, email)
        await query.answer("Sent" if success else "Failed", show_alert=True)
        return

    if data.startswith("ctrl_burn_"):
        token_short = data.replace("ctrl_burn_", "")
        accounts = USER_ACCOUNTS_CACHE.get(user_id, [])
        acc = None
        for a in accounts:
            if a["token"].startswith(token_short):
                acc = a
                break
        if not acc:
            await query.answer("Not found", show_alert=True)
            return
        emails = otp_service.load_emails(user_id)
        if acc["email"] in emails:
            await query.answer("Already in list", show_alert=True)
            return
        if len(emails) >= MAX_EMAILS:
            await query.answer(f"Limit reached ({MAX_EMAILS})", show_alert=True)
            return
        emails.append(acc["email"])
        otp_service.save_emails(user_id, emails)
        await query.answer(f"Added {acc['email']}", show_alert=True)
        return

    if data.startswith("ctrl_del_"):
        token_short = data.replace("ctrl_del_", "")
        accounts = USER_ACCOUNTS_CACHE.get(user_id, [])
        acc = None
        for a in accounts:
            if a["token"].startswith(token_short):
                acc = a
                break
        if not acc:
            await query.answer("Not found", show_alert=True)
            return
        bind_service.remove_account(acc["token"])
        await query.answer("Removed", show_alert=True)
        accounts = bind_service.get_user_accounts(user_id)
        USER_ACCOUNTS_CACHE[user_id] = accounts
        if not accounts:
            await query.edit_message_text(
                "No accounts.",
                reply_markup=kb.main_menu(user_id, ADMIN_ID, False, 0, MAX_EMAILS),
            )
        else:
            await query.edit_message_text(
                f"Your accounts ({len(accounts)}):",
                reply_markup=kb.accounts_menu(accounts),
            )
        return

    if data == "btn_emails_menu":
        emails = otp_service.load_emails(user_id)
        await query.edit_message_text(
            f"Emails ({len(emails)}/{MAX_EMAILS})",
            reply_markup=kb.emails_menu(),
        )
        return

    if data == "btn_email_add":
        WAITING_INPUT[user_id] = "email"
        await query.edit_message_text("Send the new email:")
        return

    if data == "btn_email_view":
        emails = otp_service.load_emails(user_id)
        if not emails:
            await query.edit_message_text("No emails.", reply_markup=kb.emails_menu())
            return
        counts = otp_service.get_all_counts(user_id)
        lines = []
        for i, e in enumerate(emails):
            c = counts.get(e, 0)
            lines.append(f"{i+1}. {e} - {c}/{MAX_DAILY_SENDS_PER_EMAIL}")
        await query.edit_message_text(
            f"Emails ({len(emails)}/{MAX_EMAILS}):\n\n" + "\n".join(lines),
            reply_markup=kb.emails_menu(),
        )
        return

    if data == "btn_email_delete":
        emails = otp_service.load_emails(user_id)
        if not emails:
            await query.edit_message_text("No emails.", reply_markup=kb.emails_menu())
            return
        await query.edit_message_text(
            "Choose email to delete:",
            reply_markup=kb.delete_email_menu(emails),
        )
        return

    if data.startswith("email_del_"):
        idx = int(data.split("_")[-1])
        removed = otp_service.remove_email(user_id, idx)
        if not removed:
            await query.answer("Not found", show_alert=True)
            return
        emails = otp_service.load_emails(user_id)
        if not emails:
            await query.edit_message_text(
                f"Removed {removed}.\nNo emails left.",
                reply_markup=kb.emails_menu(),
            )
        else:
            await query.edit_message_text(
                f"Removed {removed}.\nChoose next:",
                reply_markup=kb.delete_email_menu(emails),
            )
        return

    if data == "btn_send_otp":
        emails = otp_service.load_emails(user_id)
        if not emails:
            await query.edit_message_text(
                "No emails.",
                reply_markup=kb.main_menu(user_id, ADMIN_ID, is_running(user_id), 0, MAX_EMAILS),
            )
            return
        await query.edit_message_text("Sending...")
        results = []
        for email in emails:
            success, _ = await asyncio.to_thread(otp_service.send_one, email)
            results.append(f"[{'OK' if success else 'FAIL'}] {email}")
        await query.edit_message_text(
            "Results:\n\n" + "\n".join(results),
            reply_markup=kb.back_to_main(),
        )
        return

    if data == "btn_status":
        now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
        running = is_running(user_id)
        emails = otp_service.load_emails(user_id)
        counts = otp_service.get_all_counts(user_id)
        lines = []
        for e in emails:
            c = counts.get(e, 0)
            lines.append(f"{e}: {c}/{MAX_DAILY_SENDS_PER_EMAIL}")
        body = "\n".join(lines) if lines else "- none"
        await query.edit_message_text(
            f"Status\n\n"
            f"Running: {'Yes' if running else 'No'}\n"
            f"Emails:\n{body}\n\n"
            f"Time: {now}",
            reply_markup=kb.back_to_main(),
        )
        return

    if data == "btn_referral":
        stats = referral_service.stats(user_id)
        me = await context.bot.get_me()
        link = f"https://t.me/{me.username}?start={user_id}"
        credits = "INF" if user_id == ADMIN_ID else str(stats["credits"])
        await query.edit_message_text(
            f"Referral System\n\n"
            f"Link: {link}\n\n"
            f"Your referrals: {stats['referrals']}\n"
            f"Progress: {stats['progress']}/{REQUIRED_REFERRALS_PER_BURN}\n"
            f"Credits: {credits}",
            reply_markup=kb.back_to_main(),
        )
        return

    if data == "btn_admin":
        if user_id != ADMIN_ID:
            await query.answer("Access denied", show_alert=True)
            return
        await query.edit_message_text("Admin Panel", reply_markup=kb.admin_menu())
        return

    if data == "admin_add_credits":
        if user_id != ADMIN_ID:
            await query.answer("Access denied", show_alert=True)
            return
        WAITING_INPUT[user_id] = "admin_credits"
        await query.edit_message_text("Send: <user_id> <amount>")
        return


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    state = WAITING_INPUT.get(user_id)

    _, otp_service, referral_service = get_services(context)

    if state == "token":
        WAITING_INPUT.pop(user_id, None)
        if not is_valid_token(text.lower()):
            await update.message.reply_text("Invalid token. Must be 64 hex chars.")
            return
        wait = await update.message.reply_text("Searching...")
        bind_service = context.bot_data["bind_service"]
        result = await bind_service.search(text.lower(), user_id)
        if not result["success"]:
            errors = {
                "daily_limit": "Daily limit reached.",
                "invalid_token": "Invalid token.",
                "no_binding": "No email binding.",
            }
            await wait.edit_text(errors.get(result["error"], "Unknown error."))
            return
        await wait.delete()
        await update.message.reply_text(
            f"Account found\n\n"
            f"Email: {result['email']}\n"
            f"Mobile: {result['mobile'] or '-'}\n"
            f"Token: {truncate_token(text.lower())}",
            reply_markup=kb.control_menu(text.lower()),
        )
        return

    if state == "email":
        WAITING_INPUT.pop(user_id, None)
        ok, reason = otp_service.add_email(user_id, text)
        if not ok:
            msg = "Already exists." if reason == "exists" else f"Limit reached ({MAX_EMAILS})."
            await update.message.reply_text(msg)
            return
        emails = otp_service.load_emails(user_id)
        await update.message.reply_text(
            f"Added {text} ({len(emails)}/{MAX_EMAILS})",
            reply_markup=kb.emails_menu(),
        )
        return

    if state == "admin_credits":
        if user_id != ADMIN_ID:
            WAITING_INPUT.pop(user_id, None)
            return
        parts = text.split()
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].lstrip("-").isdigit():
            await update.message.reply_text("Use: <user_id> <amount>")
            return
        WAITING_INPUT.pop(user_id, None)
        target = int(parts[0])
        amount = int(parts[1])
        total = referral_service.add_credits(target, amount)
        await update.message.reply_text(
            f"Added {amount} to {target}. Total: {total}",
            reply_markup=kb.admin_menu(),
        )
        try:
            await context.bot.send_message(
                chat_id=target,
                text=f"Admin granted {amount} credit(s).",
            )
        except Exception:
            pass
        return

    await update.message.reply_text(
        "Use the buttons:",
        reply_markup=kb.main_menu(user_id, ADMIN_ID, is_running(user_id), 0, MAX_EMAILS),
    )


def build_application():
    db = DatabaseClient(DATABASE_FILE)
    garena = GarenaClient(GARENA_BASE_URL, GARENA_APP_ID, GARENA_USER_AGENT)

    bind_service = BindService(garena, db, MAX_BIND_SEARCHES_PER_DAY)
    otp_service = OtpService(garena, db, INTERVAL_SECONDS, MAX_DAILY_SENDS_PER_EMAIL, MAX_EMAILS)
    referral_service = ReferralService(db, REQUIRED_REFERRALS_PER_BURN)

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.bot_data["db"] = db
    app.bot_data["garena"] = garena
    app.bot_data["bind_service"] = bind_service
    app.bot_data["otp_service"] = otp_service
    app.bot_data["referral_service"] = referral_service

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("bind", bind_cmd))
    app.add_handler(CommandHandler("addcredits", addcredits_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    return app


if __name__ == "__main__":
    if not BOT_TOKEN:
        print("BOT_TOKEN is not set.")
        exit(1)

    print("Bot starting...")
    app = build_application()
    app.run_polling()