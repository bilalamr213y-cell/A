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
    BOT_TOKEN, ADMIN_ID, FIREBASE_URL, FIREBASE_SECRET,
    GARENA_BASE_URL, GARENA_APP_ID, GARENA_USER_AGENT,
    ALGIERS_TZ, INTERVAL_SECONDS, MAX_EMAILS,
    MAX_DAILY_SENDS_PER_EMAIL, REQUIRED_REFERRALS_PER_BURN,
    MAX_BIND_SEARCHES_PER_DAY, START_TIME_HOUR,
)
from api_clients import (
    FirebaseClient, GarenaClient,
    is_valid_token, truncate_token,
)
from services import BindService, OtpService, ReferralService
import keyboards as kb

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
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
    text = "⚙️ **القائمة الرئيسية:**"

    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await target.reply_text(text, reply_markup=markup, parse_mode="Markdown")


async def burn_loop(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    otp_service = context.bot_data["otp_service"]

    await context.bot.send_message(
        chat_id=user_id,
        text="🔥 **بدأ الحرق** — كل إيميل رح يجرب 20 مرة يومياً.",
        parse_mode="Markdown",
    )

    last_reset_day = None

    while True:
        if not is_running(user_id):
            return

        emails = otp_service.load_emails(user_id)
        if not emails:
            await context.bot.send_message(chat_id=user_id, text="⚠️ ما في إيميلات. توقف الحرق.")
            return

        now = datetime.now(ALGIERS_TZ)
        today = now.date()

        if now.hour >= START_TIME_HOUR and last_reset_day != today:
            otp_service.reset_counts(user_id)
            last_reset_day = today
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🔄 **Reset يومي** — {now.strftime('%Y-%m-%d %H:%M')}",
                parse_mode="Markdown",
            )

        all_done = True
        for email in emails:
            if otp_service.get_count(user_id, email) < MAX_DAILY_SENDS_PER_EMAIL:
                all_done = False
                break

        if all_done:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🏁 وصلت الحد ({MAX_DAILY_SENDS_PER_EMAIL}). انتظار reset الساعة {START_TIME_HOUR}.",
                parse_mode="Markdown",
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

            ts = datetime.now(ALGIERS_TZ).strftime("%H:%M:%S")
            icon = "✅" if success else "❌"
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"{icon} `{email}` — `{new_count}/{MAX_DAILY_SENDS_PER_EMAIL}` — `{ts}`",
                    parse_mode="Markdown",
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
                    text="🎉 حصلت على credit جديد! (5 إحالات)",
                )
            except Exception:
                pass

    admin_tag = " 👑" if user_id == ADMIN_ID else ""
    await update.message.reply_text(
        f"⚙️ **Free Fire Recovery Bot**{admin_tag}\n\nاختر من الأزرار:",
        reply_markup=kb.main_menu(user_id, ADMIN_ID, False, 0, MAX_EMAILS),
        parse_mode="Markdown",
    )


async def bind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("الاستخدام: `/bind <access_token>`", parse_mode="Markdown")
        return

    token = context.args[0].strip().lower()
    user_id = update.effective_user.id

    if not is_valid_token(token):
        await update.message.reply_text("❌ توكن غير صالح. لازم 64 حرف hex.")
        return

    wait = await update.message.reply_text("🔍 **جاري البحث...**")

    bind_service = context.bot_data["bind_service"]
    result = await bind_service.search(token, user_id)

    if not result["success"]:
        errors = {
            "daily_limit": "⚠️ وصلت للحد اليومي.",
            "invalid_token": "❌ التوكن غير صالح أو لا بيانات.",
            "no_binding": "⚠️ الحساب غير مربوط بإيميل.",
        }
        await wait.edit_text(errors.get(result["error"], "❌ خطأ."))
        return

    await wait.delete()
    text = (
        f"✅ **تم جلب الحساب**\n\n"
        f"📧 **الإيميل:** `{result['email']}`\n"
        f"📱 **الموبايل:** `{result['mobile'] or '—'}`\n"
        f"🔑 `{truncate_token(token)}`"
    )
    await update.message.reply_text(
        text,
        reply_markup=kb.control_menu(token),
        parse_mode="Markdown",
    )


async def addcredits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    if len(context.args) != 2:
        await update.message.reply_text("الاستخدام: `/addcredits <user_id> <amount>`", parse_mode="Markdown")
        return
    try:
        target = int(context.args[0])
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ أرقام غير صالحة.")
        return

    referral_service = context.bot_data["referral_service"]
    total = referral_service.add_credits(target, amount)
    await update.message.reply_text(
        f"✅ `{amount}` credit لـ `{target}`. المجموع: `{total}`",
        parse_mode="Markdown",
    )
    try:
        await context.bot.send_message(
            chat_id=target,
            text=f"🎉 **Admin Grant!** تم إضافة `{amount}` credit.",
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
            await query.answer("شغال بالفعل!", show_alert=True)
            return
        emails = otp_service.load_emails(user_id)
        if not emails:
            await query.edit_message_text(
                "❌ أضف إيميل أولاً.",
                reply_markup=kb.main_menu(user_id, ADMIN_ID, False, 0, MAX_EMAILS),
            )
            return

        if user_id != ADMIN_ID:
            if not referral_service.use_credit(user_id):
                stats = referral_service.stats(user_id)
                await query.edit_message_text(
                    f"⚠️ **تحتاج credit**\n\n"
                    f"• إحالاتك: `{stats['referrals']}`\n"
                    f"• التقدم: `{stats['progress']}/{REQUIRED_REFERRALS_PER_BURN}`\n\n"
                    f"اجمع {REQUIRED_REFERRALS_PER_BURN} إحالات للحصول على credit.",
                    reply_markup=kb.main_menu(user_id, ADMIN_ID, False, len(emails), MAX_EMAILS),
                    parse_mode="Markdown",
                )
                return

        RUNNING_TASKS[user_id] = True
        task = asyncio.create_task(burn_loop(user_id, context))
        RUNNING_TASKS[user_id] = task

        await query.edit_message_text(
            f"🔥 **بدأ الحرق** — {len(emails)} إيميل",
            reply_markup=kb.main_menu(user_id, ADMIN_ID, True, len(emails), MAX_EMAILS),
            parse_mode="Markdown",
        )
        return

    if data == "btn_stop_burn":
        task = RUNNING_TASKS.pop(user_id, None)
        if task and hasattr(task, "cancel"):
            task.cancel()
        emails = otp_service.load_emails(user_id)
        await query.edit_message_text(
            "🛑 **تم الإيقاف.**",
            reply_markup=kb.main_menu(user_id, ADMIN_ID, False, len(emails), MAX_EMAILS),
            parse_mode="Markdown",
        )
        return

    if data == "btn_add_account":
        WAITING_INPUT[user_id] = "token"
        await query.edit_message_text(
            "🔑 **أرسل الـ access_token** (64 حرف hex).",
            parse_mode="Markdown",
        )
        return

    if data == "btn_my_accounts":
        accounts = bind_service.get_user_accounts(user_id)
        USER_ACCOUNTS_CACHE[user_id] = accounts
        if not accounts:
            await query.edit_message_text(
                "📭 ما عندك حسابات محفوظة.",
                reply_markup=kb.main_menu(user_id, ADMIN_ID, is_running(user_id), 0, MAX_EMAILS),
            )
            return
        await query.edit_message_text(
            f"📋 **حساباتك ({len(accounts)}):**",
            reply_markup=kb.accounts_menu(accounts),
            parse_mode="Markdown",
        )
        return

    if data == "btn_control_menu":
        accounts = bind_service.get_user_accounts(user_id)
        USER_ACCOUNTS_CACHE[user_id] = accounts
        if not accounts:
            await query.edit_message_text(
                "📭 ما عندك حسابات.",
                reply_markup=kb.main_menu(user_id, ADMIN_ID, False, 0, MAX_EMAILS),
            )
            return
        await query.edit_message_text(
            "🎯 **اختر حساب للتحكم:**",
            reply_markup=kb.accounts_menu(accounts),
        )
        return

    if data.startswith("acc_select_"):
        idx = int(data.split("_")[-1])
        accounts = USER_ACCOUNTS_CACHE.get(user_id, [])
        if idx >= len(accounts):
            await query.answer("غير موجود", show_alert=True)
            return
        acc = accounts[idx]
        token = acc["token"]
        text = (
            f"🎮 **الحساب المحدد**\n\n"
            f"📧 `{acc.get('email', '?')}`\n"
            f"📱 `{acc.get('mobile') or '—'}`\n"
            f"🔑 `{truncate_token(token)}`"
        )
        await query.edit_message_text(text, reply_markup=kb.control_menu(token), parse_mode="Markdown")
        return

    if data.startswith("ctrl_info_"):
        await query.answer("المعلومات ظاهرة فوق ✅")
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
            await query.answer("غير موجود", show_alert=True)
            return
        email = acc.get("email")
        await query.answer(f"جاري الإرسال...")
        success, _ = await asyncio.to_thread(otp_service.send_one, email)
        await query.answer(f"{'✅ تم' if success else '❌ فشل'}", show_alert=True)
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
            await query.answer("غير موجود", show_alert=True)
            return
        emails = otp_service.load_emails(user_id)
        if acc["email"] in emails:
            await query.answer("موجود مسبقاً", show_alert=True)
            return
        if len(emails) >= MAX_EMAILS:
            await query.answer(f"وصلت الحد ({MAX_EMAILS})", show_alert=True)
            return
        emails.append(acc["email"])
        otp_service.save_emails(user_id, emails)
        await query.answer(f"✅ تم إضافة {acc['email']}", show_alert=True)
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
            await query.answer("غير موجود", show_alert=True)
            return
        bind_service.remove_account(acc["token"])
        await query.answer("✅ تم الحذف", show_alert=True)
        accounts = bind_service.get_user_accounts(user_id)
        USER_ACCOUNTS_CACHE[user_id] = accounts
        if not accounts:
            await query.edit_message_text(
                "📭 ما عندك حسابات.",
                reply_markup=kb.main_menu(user_id, ADMIN_ID, False, 0, MAX_EMAILS),
            )
        else:
            await query.edit_message_text(
                f"📋 **حساباتك ({len(accounts)}):**",
                reply_markup=kb.accounts_menu(accounts),
                parse_mode="Markdown",
            )
        return

    if data == "btn_emails_menu":
        emails = otp_service.load_emails(user_id)
        await query.edit_message_text(
            f"📧 **إدارة الإيميلات ({len(emails)}/{MAX_EMAILS})**",
            reply_markup=kb.emails_menu(),
            parse_mode="Markdown",
        )
        return

    if data == "btn_email_add":
        WAITING_INPUT[user_id] = "email"
        await query.edit_message_text("📩 أرسل الإيميل الجديد:")
        return

    if data == "btn_email_view":
        emails = otp_service.load_emails(user_id)
        if not emails:
            await query.edit_message_text(
                "📭 ما في إيميلات.",
                reply_markup=kb.emails_menu(),
            )
            return
        lines = []
        for i, e in enumerate(emails):
            c = otp_service.get_count(user_id, e)
            lines.append(f"{i+1}. `{e}` — {c}/{MAX_DAILY_SENDS_PER_EMAIL}")
        await query.edit_message_text(
            f"📋 **الإيميلات ({len(emails)}/{MAX_EMAILS}):**\n\n" + "\n".join(lines),
            reply_markup=kb.emails_menu(),
            parse_mode="Markdown",
        )
        return

    if data == "btn_email_delete":
        emails = otp_service.load_emails(user_id)
        if not emails:
            await query.edit_message_text("📭 ما في إيميلات.", reply_markup=kb.emails_menu())
            return
        await query.edit_message_text(
            "🗑️ اختر الإيميل للحذف:",
            reply_markup=kb.delete_email_menu(emails),
        )
        return

    if data.startswith("email_del_"):
        idx = int(data.split("_")[-1])
        removed = otp_service.remove_email(user_id, idx)
        if not removed:
            await query.answer("غير موجود", show_alert=True)
            return
        emails = otp_service.load_emails(user_id)
        if not emails:
            await query.edit_message_text(
                f"🗑️ تم حذف `{removed}`.\n\nما بقى إيميلات.",
                reply_markup=kb.emails_menu(),
                parse_mode="Markdown",
            )
        else:
            await query.edit_message_text(
                f"🗑️ تم حذف `{removed}`.\nاختر التالي:",
                reply_markup=kb.delete_email_menu(emails),
                parse_mode="Markdown",
            )
        return

    if data == "btn_send_otp":
        emails = otp_service.load_emails(user_id)
        if not emails:
            await query.edit_message_text(
                "❌ ما في إيميلات.",
                reply_markup=kb.main_menu(user_id, ADMIN_ID, is_running(user_id), 0, MAX_EMAILS),
            )
            return
        await query.edit_message_text("⏳ جاري الإرسال...")
        results = []
        for email in emails:
            success, _ = await asyncio.to_thread(otp_service.send_one, email)
            results.append(f"{'✅' if success else '❌'} `{email}`")
        await query.edit_message_text(
            "📊 **النتائج:**\n\n" + "\n".join(results),
            reply_markup=kb.back_to_main(),
            parse_mode="Markdown",
        )
        return

    if data == "btn_status":
        now = datetime.now(ALGIERS_TZ).strftime("%Y-%m-%d %H:%M:%S")
        running = is_running(user_id)
        emails = otp_service.load_emails(user_id)
        lines = []
        for e in emails:
            c = otp_service.get_count(user_id, e)
            lines.append(f"• `{e}`: {c}/{MAX_DAILY_SENDS_PER_EMAIL}")
        body = "\n".join(lines) if lines else "— لا يوجد"
        await query.edit_message_text(
            f"📊 **الحالة**\n\n"
            f"• التشغيل: {'🟢' if running else '🔴'}\n"
            f"• الإيميلات:\n{body}\n\n"
            f"• الوقت: `{now}`",
            reply_markup=kb.back_to_main(),
            parse_mode="Markdown",
        )
        return

    if data == "btn_referral":
        stats = referral_service.stats(user_id)
        me = await context.bot.get_me()
        link = f"https://t.me/{me.username}?start={user_id}"
        credits = "∞" if user_id == ADMIN_ID else str(stats["credits"])
        await query.edit_message_text(
            f"👥 **الإحالات**\n\n"
            f"🔗 `{link}`\n\n"
            f"• إحالاتك: `{stats['referrals']}`\n"
            f"• التقدم: `{stats['progress']}/{REQUIRED_REFERRALS_PER_BURN}`\n"
            f"• Credits: `{credits}`",
            reply_markup=kb.back_to_main(),
            parse_mode="Markdown",
        )
        return

    if data == "btn_admin":
        if user_id != ADMIN_ID:
            await query.answer("⛔", show_alert=True)
            return
        await query.edit_message_text(
            "👑 **Admin Panel**",
            reply_markup=kb.admin_menu(),
        )
        return

    if data == "admin_add_credits":
        if user_id != ADMIN_ID:
            await query.answer("⛔", show_alert=True)
            return
        WAITING_INPUT[user_id] = "admin_credits"
        await query.edit_message_text(
            "⚡ أرسل: `<user_id> <amount>`",
            parse_mode="Markdown",
        )
        return


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    state = WAITING_INPUT.get(user_id)

    _, otp_service, referral_service = get_services(context)

    if state == "token":
        WAITING_INPUT.pop(user_id, None)
        if not is_valid_token(text.lower()):
            await update.message.reply_text("❌ توكن غير صالح. لازم 64 حرف hex.")
return

    wait = await update.message.reply_text("🔍 **جاري البحث...**")

    bind_service = context.bot_data["bind_service"]
    result = await bind_service.search(token, user_id)

    if not result["success"]:
        errors = {
            "daily_limit": "⚠️ وصلت للحد اليومي.",
            "invalid_token": "❌ التوكن غير صالح أو لا بيانات.",
            "no_binding": "⚠️ الحساب غير مربوط بإيميل.",
        }
        await wait.edit_text(errors.get(result["error"], "❌ خطأ."))
        return

    await wait.delete()
    text = (
        f"✅ **تم جلب الحساب**\n\n"
        f"📧 **الإيميل:** `{result['email']}`\n"
        f"📱 **الموبايل:** `{result['mobile'] or '—'}`\n"
        f"🔑 `{truncate_token(token)}`"
    )
    await update.message.reply_text(
        text,
        reply_markup=kb.control_menu(token),
        parse_mode="Markdown",
    )


async def addcredits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    if len(context.args) != 2:
        await update.message.reply_text("الاستخدام: `/addcredits <user_id> <amount>`", parse_mode="Markdown")
        return
    try:
        target = int(context.args[0])
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ أرقام غير صالحة.")
        return

    referral_service = context.bot_data["referral_service"]
    total = referral_service.add_credits(target, amount)
    await update.message.reply_text(
        f"✅ `{amount}` credit لـ `{target}`. المجموع: `{total}`",
        parse_mode="Markdown",
    )
    try:
        await context.bot.send_message(
            chat_id=target,
            text=f"🎉 **Admin Grant!** تم إضافة `{amount}` credit.",
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
            await query.answer("شغال بالفعل!", show_alert=True)
            return
        emails = otp_service.load_emails(user_id)
        if not emails:
            await query.edit_message_text(
                "❌ أضف إيميل أولاً.",
                reply_markup=kb.main_menu(user_id, ADMIN_ID, False, 0, MAX_EMAILS),
            )
            return

        if user_id != ADMIN_ID:
            if not referral_service.use_credit(user_id):
                stats = referral_service.stats(user_id)
                await query.edit_message_text(
                    f"⚠️ **تحتاج credit**\n\n"
                    f"• إحالاتك: `{stats['referrals']}`\n"
                    f"• التقدم: `{stats['progress']}/{REQUIRED_REFERRALS_PER_BURN}`\n\n"
                    f"اجمع {REQUIRED_REFERRALS_PER_BURN} إحالات للحصول على credit.",
                    reply_markup=kb.main_menu(user_id, ADMIN_ID, False, len(emails), MAX_EMAILS),
                    parse_mode="Markdown",
                )
                return

        RUNNING_TASKS[user_id] = True
        task = asyncio.create_task(burn_loop(user_id, context))
        RUNNING_TASKS[user_id] = task

        await query.edit_message_text(
            f"🔥 **بدأ الحرق** — {len(emails)} إيميل",
            reply_markup=kb.main_menu(user_id, ADMIN_ID, True, len(emails), MAX_EMAILS),
            parse_mode="Markdown",
        )
        return

    if data == "btn_stop_burn":
        task = RUNNING_TASKS.pop(user_id, None)
        if task and hasattr(task, "cancel"):
            task.cancel()
        emails = otp_service.load_emails(user_id)
        await query.edit_message_text(
            "🛑 **تم الإيقاف.**",
            reply_markup=kb.main_menu(user_id, ADMIN_ID, False, len(emails), MAX_EMAILS),
            parse_mode="Markdown",
        )
        return

    if data == "btn_add_account":
        WAITING_INPUT[user_id] = "token"
        await query.edit_message_text(
            "🔑 **أرسل الـ access_token** (64 حرف hex).",
            parse_mode="Markdown",
        )
        return

    if data == "btn_my_accounts":
        accounts = bind_service.get_user_accounts(user_id)
        USER_ACCOUNTS_CACHE[user_id] = accounts
        if not accounts:
            await query.edit_message_text(
                "📭 ما عندك حسابات محفوظة.",
                reply_markup=kb.main_menu(user_id, ADMIN_ID, is_running(user_id), 0, MAX_EMAILS),
            )
            return
        await query.edit_message_text(
            f"📋 **حساباتك ({len(accounts)}):**",
            reply_markup=kb.accounts_menu(accounts),
            parse_mode="Markdown",
        )
        return

    if data == "btn_control_menu":
        accounts = bind_service.get_user_accounts(user_id)
        USER_ACCOUNTS_CACHE[user_id] = accounts
        if not accounts:
            await query.edit_message_text(
                "📭 ما عندك حسابات.",
                reply_markup=kb.main_menu(user_id, ADMIN_ID, False, 0, MAX_EMAILS),
            )
            return
        await query.edit_message_text(
            "🎯 **اختر حساب للتحكم:**",
            reply_markup=kb.accounts_menu(accounts),
        )
        return

    if data.startswith("acc_select_"):
        idx = int(data.split("_")[-1])
        accounts = USER_ACCOUNTS_CACHE.get(user_id, [])
        if idx >= len(accounts):
            await query.answer("غير موجود", show_alert=True)
            return
        acc = accounts[idx]
        token = acc["token"]
        text = (
            f"🎮 **الحساب المحدد**\n\n"
            f"📧 `{acc.get('email', '?')}`\n"
            f"📱 `{acc.get('mobile') or '—'}`\n"
            f"🔑 `{truncate_token(token)}`"
        )
        await query.edit_message_text(text, reply_markup=kb.control_menu(token), parse_mode="Markdown")
        return

    if data.startswith("ctrl_info_"):
        await query.answer("المعلومات ظاهرة فوق ✅")
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
            await query.answer("غير موجود", show_alert=True)
            return
        email = acc.get("email")
        await query.answer(f"جاري الإرسال...")
        success, _ = await asyncio.to_thread(otp_service.send_one, email)
        await query.answer(f"{'✅ تم' if success else '❌ فشل'}", show_alert=True)
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
            await query.answer("غير موجود", show_alert=True)
            return
        emails = otp_service.load_emails(user_id)
        if acc["email"] in emails:
            await query.answer("موجود مسبقاً", show_alert=True)
            return
        if len(emails) >= MAX_EMAILS:
            await query.answer(f"وصلت الحد ({MAX_EMAILS})", show_alert=True)
            return
        emails.append(acc["email"])
        otp_service.save_emails(user_id, emails)
        await query.answer(f"✅ تم إضافة {acc['email']}", show_alert=True)
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
            await query.answer("غير موجود", show_alert=True)
            return
        bind_service.remove_account(acc["token"])
        await query.answer("✅ تم الحذف", show_alert=True)
        accounts = bind_service.get_user_accounts(user_id)
        USER_ACCOUNTS_CACHE[user_id] = accounts
        if not accounts:
            await query.edit_message_text(
                "📭 ما عندك حسابات.",
                reply_markup=kb.main_menu(user_id, ADMIN_ID, False, 0, MAX_EMAILS),
            )
        else:
            await query.edit_message_text(
                f"📋 **حساباتك ({len(accounts)}):**",
                reply_markup=kb.accounts_menu(accounts),
                parse_mode="Markdown",
            )
        return

    if data == "btn_emails_menu":
        emails = otp_service.load_emails(user_id)
        await query.edit_message_text(
            f"📧 **إدارة الإيميلات ({len(emails)}/{MAX_EMAILS})**",
            reply_markup=kb.emails_menu(),
            parse_mode="Markdown",
        )
        return

    if data == "btn_email_add":
        WAITING_INPUT[user_id] = "email"
        await query.edit_message_text("📩 أرسل الإيميل الجديد:")
        return

    if data == "btn_email_view":
        emails = otp_service.load_emails(user_id)
        if not emails:
            await query.edit_message_text(
                "📭 ما في إيميلات.",
                reply_markup=kb.emails_menu(),
            )
            return
        lines = []
        for i, e in enumerate(emails):
            c = otp_service.get_count(user_id, e)
            lines.append(f"{i+1}. `{e}` — {c}/{MAX_DAILY_SENDS_PER_EMAIL}")
        await query.edit_message_text(
            f"📋 **الإيميلات ({len(emails)}/{MAX_EMAILS}):**\n\n" + "\n".join(lines),
            reply_markup=kb.emails_menu(),
            parse_mode="Markdown",
        )
        return

    if data == "btn_email_delete":
        emails = otp_service.load_emails(user_id)
        if not emails:
            await query.edit_message_text("📭 ما في إيميلات.", reply_markup=kb.emails_menu())
            return
        await query.edit_message_text(
            "🗑️ اختر الإيميل للحذف:",
            reply_markup=kb.delete_email_menu(emails),
        )
        return

    if data.startswith("email_del_"):
        idx = int(data.split("_")[-1])
        removed = otp_service.remove_email(user_id, idx)
        if not removed:
            await query.answer("غير موجود", show_alert=True)
            return
        emails = otp_service.load_emails(user_id)
        if not emails:
            await query.edit_message_text(
                f"🗑️ تم حذف `{removed}`.\n\nما بقى إيميلات.",
                reply_markup=kb.emails_menu(),
                parse_mode="Markdown",
            )
        else:
            await query.edit_message_text(
                f"🗑️ تم حذف `{removed}`.\nاختر التالي:",
                reply_markup=kb.delete_email_menu(emails),
                parse_mode="Markdown",
            )
        return

    if data == "btn_send_otp":
        emails = otp_service.load_emails(user_id)
        if not emails:
            await query.edit_message_text(
                "❌ ما في إيميلات.",
                reply_markup=kb.main_menu(user_id, ADMIN_ID, is_running(user_id), 0, MAX_EMAILS),
            )
            return
        await query.edit_message_text("⏳ جاري الإرسال...")
        results = []
        for email in emails:
            success, _ = await asyncio.to_thread(otp_service.send_one, email)
            results.append(f"{'✅' if success else '❌'} `{email}`")
        await query.edit_message_text(
            "📊 **النتائج:**\n\n" + "\n".join(results),
            reply_markup=kb.back_to_main(),
            parse_mode="Markdown",
        )
        return

    if data == "btn_status":
        now = datetime.now(ALGIERS_TZ).strftime("%Y-%m-%d %H:%M:%S")
        running = is_running(user_id)
        emails = otp_service.load_emails(user_id)
        lines = []
        for e in emails:
            c = otp_service.get_count(user_id, e)
            lines.append(f"• `{e}`: {c}/{MAX_DAILY_SENDS_PER_EMAIL}")
        body = "\n".join(lines) if lines else "— لا يوجد"
        await query.edit_message_text(
            f"📊 **الحالة**\n\n"
            f"• التشغيل: {'🟢' if running else '🔴'}\n"
            f"• الإيميلات:\n{body}\n\n"
            f"• الوقت: `{now}`",
            reply_markup=kb.back_to_main(),
            parse_mode="Markdown",
        )
        return

    if data == "btn_referral":
        stats = referral_service.stats(user_id)
        me = await context.bot.get_me()
        link = f"https://t.me/{me.username}?start={user_id}"
        credits = "∞" if user_id == ADMIN_ID else str(stats["credits"])
        await query.edit_message_text(
            f"👥 **الإحالات**\n\n"
            f"🔗 `{link}`\n\n"
            f"• إحالاتك: `{stats['referrals']}`\n"
            f"• التقدم: `{stats['progress']}/{REQUIRED_REFERRALS_PER_BURN}`\n"
            f"• Credits: `{credits}`",
            reply_markup=kb.back_to_main(),
            parse_mode="Markdown",
        )
        return

    if data == "btn_admin":
        if user_id != ADMIN_ID:
            await query.answer("⛔", show_alert=True)
            return
        await query.edit_message_text(
            "👑 **Admin Panel**",
            reply_markup=kb.admin_menu(),
        )
        return

    if data == "admin_add_credits":
        if user_id != ADMIN_ID:
            await query.answer("⛔", show_alert=True)
            return
        WAITING_INPUT[user_id] = "admin_credits"
        await query.edit_message_text(
            "⚡ أرسل: `<user_id> <amount>`",
            parse_mode="Markdown",
        )
        return


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    state = WAITING_INPUT.get(user_id)

    _, otp_service, referral_service = get_services(context)

    if state == "token":
        WAITING_INPUT.pop(user_id, None)
        if not is_valid_token(text.lower()):
            await update.message.reply_text("❌ توكن غير صالح. لازم 64 حرف hex.")
            return
        wait = await update.message.reply_text("🔍 **جاري البحث...**")
        bind_service = context.bot_data["bind_service"]
        result = await bind_service.search(text.lower(), user_id)
        if not result["success"]:
            errors = {
                "daily_limit": "⚠️ وصلت للحد اليومي.",
                "invalid_token": "❌ التوكن غير صالح.",
                "no_binding": "⚠️ الحساب غير مربوط بإيميل.",
            }
            await wait.edit_text(errors.get(result["error"], "❌ خطأ."))
            return
        await wait.delete()
        await update.message.reply_text(
            f"✅ **تم جلب الحساب**\n\n"
            f"📧 `{result['email']}`\n"
            f"📱 `{result['mobile'] or '—'}`\n"
            f"🔑 `{truncate_token(text.lower())}`",
            reply_markup=kb.control_menu(text.lower()),
            parse_mode="Markdown",
        )
        return

    if state == "email":
        WAITING_INPUT.pop(user_id, None)
        ok, reason = otp_service.add_email(user_id, text)
        if not ok:
            msg = "⚠️ موجود مسبقاً." if reason == "exists" else f"⚠️ وصلت الحد ({MAX_EMAILS})."
            await update.message.reply_text(msg)
            return
        emails = otp_service.load_emails(user_id)
        await update.message.reply_text(
            f"✅ تم إضافة `{text}` ({len(emails)}/{MAX_EMAILS})",
            reply_markup=kb.emails_menu(),
            parse_mode="Markdown",
        )
        return

    if state == "admin_credits":
        if user_id != ADMIN_ID:
            WAITING_INPUT.pop(user_id, None)
            return
        parts = text.split()
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].lstrip("-").isdigit():
            await update.message.reply_text("⚠️ استخدم: `<user_id> <amount>`", parse_mode="Markdown")
            return
        WAITING_INPUT.pop(user_id, None)
        target = int(parts[0])
        amount = int(parts[1])
        total = referral_service.add_credits(target, amount)
        await update.message.reply_text(
            f"✅ `{amount}` credit لـ `{target}`. المجموع: `{total}`",
            reply_markup=kb.admin_menu(),
            parse_mode="Markdown",
        )
        try:
            await context.bot.send_message(
                chat_id=target,
                text=f"🎉 **Admin Grant!** `{amount}` credit.",
            )
        except Exception:
            pass
        return

    await update.message.reply_text(
        "استخدم الأزرار:",
        reply_markup=kb.main_menu(user_id, ADMIN_ID, is_running(user_id), 0, MAX_EMAILS),
    )


def build_application():
    firebase = FirebaseClient(FIREBASE_URL, FIREBASE_SECRET)
    garena = GarenaClient(GARENA_BASE_URL, GARENA_APP_ID, GARENA_USER_AGENT)

    bind_service = BindService(garena, firebase, ALGIERS_TZ, MAX_BIND_SEARCHES_PER_DAY)
    otp_service = OtpService(garena, firebase, INTERVAL_SECONDS, MAX_DAILY_SENDS_PER_EMAIL, MAX_EMAILS)
    referral_service = ReferralService(firebase, REQUIRED_REFERRALS_PER_BURN)

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.bot_data["firebase"] = firebase
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
    if not FIREBASE_URL:
        print("❌ FIREBASE_URL غير محدد في البيئة!")
        exit(1)

    print("🚀 Bot is starting...")
    app = build_application()
    app.run_polling()
