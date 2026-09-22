# ============================================================
# bot.py - FF Like Bot v9 (all-in-one)
# ============================================================
# python-telegram-bot + Flask + Like engine
# بنية نفس البوت القديم الذي كان يعمل
# ============================================================

import asyncio
import hashlib
import json
import logging
import os
import random
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
import urllib3
import blackboxprotobuf
from flask import Flask, jsonify
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from jwt_client import FreeFireLogin, enc_aes, UA_UNITY

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = os.environ.get(
    "TG_TOKEN", "8776921304:AAGRrWDoNy5WWib5V3_wkIlZD_nEttflvDc"
)
ADMIN_ID = int(os.environ.get("ADMIN_ID", "7373420615"))
ACCOUNTS_FILE = os.environ.get("ACCOUNTS_FILE", "accounts.json")
TARGET_FILE = os.environ.get("TARGET_FILE", "target.json")
PORT = int(os.environ.get("PORT", 8080))
WORKERS = int(os.environ.get("LIKE_WORKERS", "5"))
RESUME = os.environ.get("RESUME", "1") == "1"
PROGRESS_EVERY_SEC = 6

LIKE_URL = "https://clientbp.ppmainecoonghj.com/LikeProfile"
MAX_RETRIES = 3
BASE_BACKOFF = 1.5
MAX_BACKOFF = 20.0
RATE_LIMIT_PER_MIN = 90
CHECKPOINT_EVERY = 25

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("ff_like")

# ============================================================
# STATE
# ============================================================
is_running = False
active_chat_id = None
current_target_uid = None
live_progress_msg_id = None
waiting_for_uid_input = False
stats = {
    "done": 0, "ok": 0, "fail": 0, "total": 0,
    "start_time": 0.0,
    "error_breakdown": {},
}
STOP_FLAG = {"stop": False}
LOG_LINES = []
LOG_LOCK = threading.Lock()
_ACCOUNTS_CACHE = {"list": [], "loaded_at": 0}


def log_line(msg):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with LOG_LOCK:
        LOG_LINES.append(line)
        if len(LOG_LINES) > 500:
            del LOG_LINES[:200]


def is_admin(uid):
    return int(uid) == ADMIN_ID


# ============================================================
# TARGET UID (persisted)
# ============================================================
def load_target():
    global current_target_uid
    try:
        if os.path.exists(TARGET_FILE):
            with open(TARGET_FILE, "r") as f:
                current_target_uid = str(json.load(f).get("target_uid") or "")
    except Exception:
        pass


def save_target(uid):
    global current_target_uid
    current_target_uid = str(uid)
    try:
        with open(TARGET_FILE, "w") as f:
            json.dump({"target_uid": current_target_uid}, f)
    except Exception:
        pass


load_target()


# ============================================================
# ACCOUNTS
# ============================================================
def load_accounts(force=False):
    now = time.time()
    if not force and _ACCOUNTS_CACHE["list"] and (now - _ACCOUNTS_CACHE["loaded_at"]) < 60:
        return _ACCOUNTS_CACHE["list"]

    out = []
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            seen = set()
            for acc in data:
                if isinstance(acc, dict):
                    info = acc.get("guest_account_info") or acc
                    uid = info.get("com.garena.msdk.guest_uid") or info.get("uid")
                    pwd = info.get("com.garena.msdk.guest_password") or info.get("password")
                    if uid and pwd and str(uid) not in seen:
                        seen.add(str(uid))
                        out.append({"uid": str(uid), "password": str(pwd)})
    except Exception as e:
        log.error(f"[accounts] {e}")

    _ACCOUNTS_CACHE["list"] = out
    _ACCOUNTS_CACHE["loaded_at"] = now
    return out


# ============================================================
# RATE LIMITER
# ============================================================
class RateLimiter:
    def __init__(self, per_minute):
        self.capacity = per_minute
        self.tokens = float(per_minute)
        self.refill = per_minute / 60.0
        self.last = time.time()
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            now = time.time()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.refill)
            self.last = now
            if self.tokens < 1:
                time.sleep((1 - self.tokens) / self.refill)
                self.tokens = 0.0
            else:
                self.tokens -= 1.0


_rate_limiter = RateLimiter(RATE_LIMIT_PER_MIN)


class GlobalBackoff:
    def __init__(self):
        self.lock = threading.Lock()
        self.until = 0.0

    def trigger(self, s):
        with self.lock:
            self.until = max(self.until, time.time() + s)

    def wait(self):
        with self.lock:
            r = self.until - time.time()
        if r > 0:
            time.sleep(r)


_global_backoff = GlobalBackoff()

_tls = threading.local()


def _session():
    if not hasattr(_tls, "s"):
        s = requests.Session()
        s.verify = False
        s.mount("http://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4))
        s.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4))
        _tls.s = s
    return _tls.s


# ============================================================
# LIKE LOGIC
# ============================================================
def build_payload(target_uid):
    typedef = {"1": {"type": "int", "name": ""}}
    raw = blackboxprotobuf.encode_message({"1": int(target_uid)}, typedef)
    return enc_aes(raw)


def like_once(engine, uid, password, target_uid, retries=MAX_RETRIES):
    last_err = None
    t0 = time.time()
    for attempt in range(retries + 1):
        _rate_limiter.acquire()
        _global_backoff.wait()
        try:
            info = engine.login(uid, password)
            jwt = info["jwt"]
            acc_id = info.get("account_id")

            body = build_payload(target_uid)
            headers = {
                "Host": "clientbp.ppmainecoonghj.com",
                "User-Agent": UA_UNITY,
                "Accept": "*/*",
                "Accept-Encoding": "deflate, gzip",
                "X-GA-SV": str(int(time.time())),
                "Authorization": f"Bearer {jwt}",
                "X-GA": "v1 1",
                "ReleaseVersion": "OB55",
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Unity-Version": "2018.4.12f1",
            }
            r = _session().post(LIKE_URL, headers=headers, data=body, timeout=15, verify=False)

            if r.status_code == 429:
                wait = min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
                _global_backoff.trigger(wait)
                last_err = "429"
                continue
            if r.status_code == 200:
                return {"uid": uid, "account_id": acc_id, "success": True, "elapsed": time.time() - t0}
            last_err = f"HTTP {r.status_code}"
        except requests.exceptions.Timeout:
            last_err = "timeout"
        except requests.exceptions.ConnectionError:
            last_err = "conn_error"
        except Exception as e:
            last_err = str(e)[:80]

        if attempt < retries:
            time.sleep(min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF))
    return {"uid": uid, "success": False, "error": last_err or "unknown", "elapsed": time.time() - t0}


def _key(uid):
    return hashlib.md5(str(uid).encode()).hexdigest()[:12]


def _load_ckpt(path):
    if not path or not os.path.exists(path):
        return set()
    try:
        with open(path) as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_ckpt(path, keys):
    try:
        with open(path, "w") as f:
            json.dump(sorted(keys), f)
    except Exception:
        pass


def run_like_batch(accounts, target_uid, workers, on_progress, stop_flag, resume):
    if not accounts:
        return 0, 0, []

    ckpt = f"like_checkpoint_{target_uid}.json"
    failed_out = f"failed_accounts_{target_uid}.json"

    # dedupe
    seen, uniq = set(), []
    for a in accounts:
        u = str(a.get("uid"))
        if u and u not in seen:
            seen.add(u)
            uniq.append(a)
    accounts = uniq

    done_keys = _load_ckpt(ckpt) if resume else set()
    if done_keys:
        accounts = [a for a in accounts if _key(a["uid"]) not in done_keys]
    if not accounts:
        return 0, 0, []

    engine = FreeFireLogin()
    ok = fail = 0
    results = []
    done = 0
    total = len(accounts)

    def _w(a):
        return like_once(engine, a["uid"], a["password"], target_uid)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_w, a): a for a in accounts}
        for fut in as_completed(futs):
            if stop_flag and stop_flag.get("stop"):
                for f in futs:
                    f.cancel()
                break
            try:
                res = fut.result()
            except Exception as e:
                res = {"uid": "?", "success": False, "error": str(e)[:80]}
            done += 1
            results.append(res)
            if res.get("success"):
                ok += 1
                done_keys.add(_key(res["uid"]))
            else:
                fail += 1
            if on_progress:
                try:
                    on_progress(done, total, ok, fail, res)
                except Exception:
                    pass
            if done % CHECKPOINT_EVERY == 0:
                _save_ckpt(ckpt, done_keys)

    _save_ckpt(ckpt, done_keys)
    failed = [r for r in results if not r.get("success")]
    if failed:
        try:
            with open(failed_out, "w", encoding="utf-8") as f:
                json.dump(failed, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    return ok, fail, results


# ============================================================
# FLASK HEALTHCHECK
# ============================================================
def run_flask():
    app = Flask(__name__)

    @app.route("/")
    def index():
        return jsonify({
            "status": "ok", "running": is_running,
            "target_uid": current_target_uid,
            "done": stats["done"], "ok": stats["ok"], "fail": stats["fail"],
            "total": stats["total"],
        })

    @app.route("/health")
    def health():
        return "ok", 200

    @app.route("/accounts-count")
    def accounts_count():
        return jsonify({"count": len(load_accounts())})

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)


# ============================================================
# KEYBOARDS
# ============================================================
def kb_main():
    like_txt = "🛑 إيقاف الإعجابات" if is_running else "❤️ إرسال إعجابات"
    like_cb = "like_stop" if is_running else "like_start"
    keyboard = [
        [InlineKeyboardButton(like_txt, callback_data=like_cb)],
        [
            InlineKeyboardButton("🎯 تعيين UID الهدف", callback_data="set_uid"),
            InlineKeyboardButton("📊 الحالة", callback_data="status"),
        ],
        [
            InlineKeyboardButton("🔑 اختبار حساب", callback_data="test_acc"),
            InlineKeyboardButton("♻️ إعادة تحميل", callback_data="reload"),
        ],
        [
            InlineKeyboardButton("📥 تحميل الفاشلة", callback_data="dl_failed"),
            InlineKeyboardButton("📜 السجل", callback_data="logs"),
        ],
        [
            InlineKeyboardButton("🗑️ مسح السجل", callback_data="clear_logs"),
            InlineKeyboardButton("🆔 معرّفي", callback_data="myid"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def kb_back():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
    ])


# ============================================================
# TEXT
# ============================================================
def main_text():
    acc_n = len(load_accounts())
    tgt = current_target_uid or "لم يُعيَّن بعد"
    state = "🟢 يعمل" if is_running else "🔴 متوقف"
    return (
        "❤️ *بوت إعجابات Free Fire — v9*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 الحالة: {state}\n"
        f"🎯 الهدف: `{tgt}`\n"
        f"👥 الحسابات: {acc_n}\n"
        f"📈 آخر جلسة: {stats['ok']}/{stats['total']}\n"
    )


# ============================================================
# LIKE ASYNC WRAPPER
# ============================================================
async def run_like_async(chat_id, context):
    global is_running, live_progress_msg_id, stats

    target_uid = current_target_uid
    if not target_uid:
        await context.bot.send_message(chat_id=chat_id, text="❌ لم يتم تعيين UID هدف بعد")
        return

    accounts = load_accounts()
    if not accounts:
        await context.bot.send_message(chat_id=chat_id, text="❌ لا توجد حسابات في accounts.json")
        return

    is_running = True
    STOP_FLAG["stop"] = False
    stats.update({
        "done": 0, "ok": 0, "fail": 0, "total": len(accounts),
        "start_time": time.time(), "error_breakdown": {},
    })
    live_progress_msg_id = None

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🚀 *بدء الإرسال*\n"
            f"🎯 الهدف: `{target_uid}`\n"
            f"👥 الحسابات: {len(accounts)}\n"
            f"⚙️ workers: {WORKERS}"
        ),
        parse_mode="Markdown",
    )

    start_ts = time.time()
    loop = asyncio.get_running_loop()
    last_ts = {"t": 0}

    def on_progress(done, total, ok, fail, res):
        now = time.time()
        if now - last_ts["t"] < PROGRESS_EVERY_SEC and done != total:
            return
        last_ts["t"] = now
        stats["done"], stats["ok"], stats["fail"] = done, ok, fail

        el = now - start_ts
        rate = done / max(el, 1) * 60
        pct = done / total * 100 if total else 0
        bar_n = int(12 * pct / 100)
        bar = "█" * bar_n + "░" * (12 - bar_n)
        eta = (total - done) / max(rate / 60, 0.01) if rate > 0 else 0
        mark = "✅" if res.get("success") else "❌"

        txt = (
            f"❤️ *جارٍ الإرسال*\n"
            f"`{bar}` {pct:.1f}%\n\n"
            f"📦 {done}/{total}\n"
            f"✅ {ok}   ❌ {fail}\n"
            f"⚡ {rate:.0f}/دقيقة   ⏱ {el:.0f}s\n"
            f"⏳ ETA: {eta:.0f}s\n"
            f"آخر: {mark} `…{str(res.get('uid',''))[-6:]}`"
        )
        asyncio.run_coroutine_threadsafe(
            _update_progress(context, chat_id, txt), loop,
        )

    try:
        ok, fail, results = await asyncio.to_thread(
            run_like_batch, accounts, target_uid, WORKERS,
            on_progress, STOP_FLAG, RESUME,
        )
    except Exception as e:
        log.error(f"[like] {e}\n{traceback.format_exc()}")
        ok, fail, results = 0, len(accounts), []

    el = time.time() - start_ts
    breakdown = {}
    for r in results:
        if not r.get("success"):
            err = r.get("error", "unknown")
            breakdown[err] = breakdown.get(err, 0) + 1
    stats["error_breakdown"] = breakdown
    is_running = False

    header = "🛑 *تم الإيقاف*" if STOP_FLAG["stop"] else "🏁 *اكتمل الإرسال*"
    summary = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 الهدف: `{target_uid}`\n"
        f"✅ نجح: {ok}\n"
        f"❌ فشل: {fail}\n"
        f"⏱ {el:.0f}s\n"
    )
    if breakdown:
        summary += "\n*الأخطاء:*\n"
        for err, cnt in sorted(breakdown.items(), key=lambda x: -x[1])[:5]:
            summary += f"  • `{err[:30]}`: {cnt}\n"

    await context.bot.send_message(
        chat_id=chat_id, text=summary,
        reply_markup=kb_main(), parse_mode="Markdown",
    )


async def _update_progress(context, chat_id, txt):
    global live_progress_msg_id
    try:
        if live_progress_msg_id is None:
            m = await context.bot.send_message(chat_id=chat_id, text=txt, parse_mode="Markdown")
            live_progress_msg_id = m.message_id
        else:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=live_progress_msg_id,
                text=txt, parse_mode="Markdown",
            )
    except Exception as e:
        if "not modified" not in str(e):
            log.error(f"[progress] {e}")


# ============================================================
# HANDLERS
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_chat_id
    active_chat_id = update.effective_chat.id
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text(f"🚫 غير مصرح: `{uid}`", parse_mode="Markdown")
        return
    await update.message.reply_text(
        main_text(), reply_markup=kb_main(), parse_mode="Markdown",
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global waiting_for_uid_input, is_running, active_chat_id

    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    active_chat_id = chat_id

    if not is_admin(uid):
        await q.answer("🚫 غير مصرح", show_alert=True)
        return

    data = q.data

    if data == "main_menu":
        await q.edit_message_text(main_text(), reply_markup=kb_main(), parse_mode="Markdown")

    elif data == "set_uid":
        waiting_for_uid_input = True
        await q.edit_message_text(
            "🎯 *أرسل UID الحساب الهدف*\n\n"
            "اكتب UID في المحادثة مباشرة.\n"
            "مثال: `7895804990`",
            reply_markup=kb_back(), parse_mode="Markdown",
        )

    elif data == "like_start":
        if is_running:
            await q.answer("⚠️ قيد التنفيذ", show_alert=True)
            return
        if not current_target_uid:
            await q.edit_message_text(
                "⚠️ *لم تعيّن UID الهدف بعد*\nاضغط 'تعيين UID الهدف' أولاً.",
                reply_markup=kb_main(), parse_mode="Markdown",
            )
            return
        await q.edit_message_text(
            f"🚀 *بدأ الإرسال*\n🎯 `{current_target_uid}`",
            reply_markup=kb_main(), parse_mode="Markdown",
        )
        asyncio.create_task(run_like_async(chat_id, context))

    elif data == "like_stop":
        STOP_FLAG["stop"] = True
        await q.edit_message_text(
            "🛑 *تم طلب الإيقاف*",
            reply_markup=kb_main(), parse_mode="Markdown",
        )

    elif data == "status":
        el = time.time() - stats["start_time"] if stats["start_time"] else 0
        txt = (
            "📊 *الحالة*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 يعمل: `{is_running}`\n"
            f"🎯 الهدف: `{current_target_uid or '—'}`\n"
            f"✅ نجح: {stats['ok']}\n"
            f"❌ فشل: {stats['fail']}\n"
            f"📦 تم: {stats['done']}/{stats['total']}\n"
        )
        if el:
            txt += f"⏱ الزمن: {el:.0f}s\n"
        if stats["error_breakdown"]:
            txt += "\n*الأخطاء:*\n"
            for err, cnt in sorted(stats["error_breakdown"].items(), key=lambda x: -x[1])[:5]:
                txt += f"  • `{err[:30]}`: {cnt}\n"
        await q.edit_message_text(txt, reply_markup=kb_back(), parse_mode="Markdown")

    elif data == "test_acc":
        accs = load_accounts()
        if not accs:
            await q.edit_message_text("❌ لا توجد حسابات", reply_markup=kb_main())
            return
        sample = accs[0]
        await q.edit_message_text(
            f"🔑 *اختبار*\nuid: `{sample['uid']}`",
            parse_mode="Markdown",
        )
        try:
            t0 = time.time()
            engine = FreeFireLogin()
            res = await asyncio.to_thread(engine.login, sample["uid"], sample["password"])
            dt = time.time() - t0
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ *نجح*\naccount_id: `{res['account_id']}`\n⏱ {dt:.2f}s",
                parse_mode="Markdown",
            )
        except Exception as e:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ فشل: `{str(e)[:80]}`",
                parse_mode="Markdown",
            )
        await context.bot.send_message(
            chat_id=chat_id, text="🔙", reply_markup=kb_main(),
        )

    elif data == "reload":
        n = len(load_accounts(force=True))
        await q.edit_message_text(
            f"♻️ تم تحميل {n} حساب",
            reply_markup=kb_main(),
        )

    elif data == "dl_failed":
        tgt = current_target_uid
        fname = f"failed_accounts_{tgt}.json" if tgt else None
        if fname and os.path.exists(fname):
            with open(fname, "rb") as f:
                content = f.read()
            await context.bot.send_document(
                chat_id=chat_id, document=content,
                filename=fname, caption=f"📥 الفاشلة ({tgt})",
            )
        else:
            await q.edit_message_text("📭 لا يوجد ملف فاشل", reply_markup=kb_main())

    elif data == "logs":
        with LOG_LOCK:
            lines = LOG_LINES[-60:]
        txt = "\n".join(lines) if lines else "📭 لا سجل"
        for i in range(0, len(txt), 3500):
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"```\n{txt[i:i+3500]}\n```",
                parse_mode="Markdown",
            )
        await context.bot.send_message(chat_id=chat_id, text="🔙", reply_markup=kb_main())

    elif data == "clear_logs":
        with LOG_LOCK:
            LOG_LINES.clear()
        await q.edit_message_text("🗑️ تم المسح", reply_markup=kb_main())

    elif data == "myid":
        bot_u = (await context.bot.get_me()).username
        await q.edit_message_text(
            f"🆔 معرّفك: `{uid}`\n🤖 @{bot_u}",
            reply_markup=kb_main(), parse_mode="Markdown",
        )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global waiting_for_uid_input, active_chat_id
    uid = update.effective_user.id
    active_chat_id = update.effective_chat.id
    if not is_admin(uid):
        return

    if waiting_for_uid_input:
        txt = update.message.text.strip()
        if not txt.isdigit():
            await update.message.reply_text(
                "❌ أرسل UID رقمي فقط", reply_markup=kb_main(),
            )
            return
        waiting_for_uid_input = False
        save_target(txt)
        await update.message.reply_text(
            f"✅ *تم تعيين UID الهدف*\n`{txt}`\n\n"
            f"اضغط '❤️ إرسال إعجابات' للبدء.",
            reply_markup=kb_main(), parse_mode="Markdown",
        )


# ============================================================
# MAIN
# ============================================================
def main():
    threading.Thread(target=run_flask, daemon=True).start()
    log_line(f"[main] Flask on {PORT}")

    log_line("=== FF LIKE BOT v9 START ===")
    log_line(f"admin={ADMIN_ID} workers={WORKERS} resume={RESUME}")
    log_line(f"accounts loaded: {len(load_accounts(force=True))}")
    log_line(f"target={current_target_uid or 'none'}")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    log_line("Bot polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
