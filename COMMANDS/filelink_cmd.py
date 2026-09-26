# -*- coding: utf-8 -*-
"""فایل → لینکِ مستقیم: دکمه‌ها و جریانِ گفتگو.

• فایلی (فیلم/سند/صوت/عکس) برای ربات بفرستی ⇒ می‌پرسد «چند وقت زنده بماند؟»
  (۳۰ دقیقه / ۱ ساعت / ۶ ساعت / ۲۴ ساعت) ⇒ لینکِ مستقیم می‌دهد.
• بعد از تمام‌شدنِ زمان، فایل خودکار پاک می‌شود؛ دکمهٔ «🗑 پاک کن» هم برای حذفِ دستی هست.
• پنل: دستور ``/links`` یا دکمهٔ «🔗 لینکِ فایل» در کیبوردِ پایینِ چت.

این ماژول در magic.py **قبل از** URL_PARSERS/DOWN_AND_UP ایمپورت می‌شود تا هندلرِ
فایلِ ما (که مخصوصِ پیام‌های چندرسانه‌ای است) زودتر از بقیه ثبت شود.
"""
from __future__ import annotations

import contextlib
import os
import secrets
import time

from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from CONFIG.config import Config
from HELPERS.app_instance import get_app
from HELPERS.logger import logger
from HELPERS.safe_messeger import (safe_send_message, safe_edit_message_text,
                                   run_pyrogram_client_coroutine)
from HELPERS import filelink as fl

app = get_app()

LABELS = {"🔗 لینکِ فایل", "🔗 لینک فایل", "🔗 لینک‌ها", "🔗 لینکها", "لینک‌ها", "لینکها"}
_COMMANDS = ("/links", "/link", "/filelink", "/files")


def is_admin(user_id) -> bool:
    try:
        return int(user_id) in set(getattr(Config, "ADMIN", []) or [])
    except Exception:
        return False


# ─────────────────────────── کمکی ───────────────────────────

def _media_of(message):
    """(name, size, kind) برای هر نوع فایل؛ None اگر فایل نباشد."""
    for attr, kind, ext in (("document", "document", ""), ("video", "video", ".mp4"),
                            ("audio", "audio", ".mp3"), ("animation", "animation", ".mp4")):
        obj = getattr(message, attr, None)
        if obj is None:
            continue
        name = getattr(obj, "file_name", None) or ("%s_%s%s" % (kind, getattr(obj, "file_id", "x")[:8], ext))
        return name, int(getattr(obj, "file_size", 0) or 0), kind
    photos = getattr(message, "photo", None)
    if photos:
        biggest = max(photos, key=lambda p: int(getattr(p, "file_size", 0) or 0))
        return "photo_%s.jpg" % (getattr(biggest, "file_id", "x")[:8]), \
               int(getattr(biggest, "file_size", 0) or 0), "photo"
    return None


def _looks_like_cookie(message) -> bool:
    doc = getattr(message, "document", None)
    if doc is None:
        return False
    name = (getattr(doc, "file_name", "") or "").lower()
    size = int(getattr(doc, "file_size", 0) or 0)
    return name.endswith(".txt") and size <= 128 * 1024


def _delegate_cookie(app_obj, message) -> bool:
    """فایلِ کوکیِ کاربران را به مسیرِ همیشگیِ خودش می‌فرستد (رفتارِ قبلی حفظ شود)."""
    try:
        from COMMANDS.cookies_cmd import save_my_cookie
        save_my_cookie(app_obj, message)
        logger.info("filelink: delegated document to save_my_cookie")
        return True
    except Exception as e:
        logger.error(f"filelink: cookie delegate failed: {e}")
        return False


def _expiry_keyboard(chat_id: int, message_id: int):
    rows, row = [], []
    for key, _sec, label in fl.TTL_OPTIONS:
        row.append(InlineKeyboardButton("⏱ " + label, callback_data="flmk|%s|%d|%d" % (key, chat_id, message_id)))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🗑 انصراف", callback_data="flclose")])
    return InlineKeyboardMarkup(rows)


def _link_keyboard(token: str):
    rows = [[InlineKeyboardButton("🗑 پاک کن (فایل + لینک)", callback_data="flrm|%s" % token)]]
    rows.append([InlineKeyboardButton("⏱ تمدید ۳۰ دقیقه", callback_data="flex|%s|30m" % token),
                 InlineKeyboardButton("⏱ تمدید ۶ ساعت", callback_data="flex|%s|6h" % token)])
    rows.append([InlineKeyboardButton("🔗 همهٔ لینک‌ها", callback_data="fllist")])
    return InlineKeyboardMarkup(rows)


def _link_text(rec: dict) -> str:
    url = fl.link_url(rec)
    left = (rec.get("exp") or 0) - time.time()
    lines = ["🔗 <b>لینکِ مستقیم آماده است</b>", "",
             "📁 <code>%s</code> — %s" % (rec.get("name"), fl.human_size(rec.get("size"))),
             "⏱ انقضا: %s دیگر" % fl.human(left)]
    if url:
        lines += ["", "<code>%s</code>" % url, "",
                  "با کلیک روی لینک (یا لمسِ طولانی) کپی/بازش کن؛ "
                  "همین لینک برای دانلود‌منیجر هم کار می‌کند."]
    return "\n".join(lines)


def _panel() -> tuple:
    recs = fl.active()
    base = fl.base_url()
    lines = ["🔗 <b>لینک‌های فایل</b>", ""]
    if not base:
        lines += ["⚠️ <b>دامنهٔ عمومی نداری</b> — تا دامنه نسازی لینک کار نمی‌کند.",
                  "Railway → سرویس → <b>Settings → Networking → Generate Domain</b> "
                  "(پورت را ۸۰۸۰ بگذار) و بعد دکمهٔ «🔄» را بزن.", ""]
    elif not app:
        pass
    if not recs:
        lines.append("الان هیچ لینکی فعال نیست. یک فایل برای ربات بفرست تا لینک بسازم.")
    else:
        n, total = fl.storage_used()
        lines.append("فایل‌های فعال: <b>%d</b> — حجم: %s" % (len(recs), fl.human_size(total)))
        lines.append("")
        for r in recs:
            left = (r.get("exp") or 0) - time.time()
            lines.append("• <b>%s</b> — %s — ⏱ %s" % (r.get("name"), fl.human_size(r.get("size")),
                                                       fl.human(left) if left > 0 else "منقضی"))
    rows, row = [], []
    for r in recs[:12]:
        row.append(InlineKeyboardButton("🗑 %s" % (r.get("name") or "")[:24], callback_data="flrm|%s" % r["token"]))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔄 به‌روزرسانی", callback_data="fllist")])
    if recs:
        rows.append([InlineKeyboardButton("🧹 پاک‌کردنِ همه", callback_data="flclean")])
    rows.append([InlineKeyboardButton("✖️ بستن", callback_data="flclose")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def show_panel(uid: int, answer_msg_id: int = 0):
    fl.start_server()
    text, kb = _panel()
    if answer_msg_id:
        if safe_edit_message_text(uid, answer_msg_id, text, reply_markup=kb) is not None:
            return
    safe_send_message(uid, text, reply_markup=kb)


# ─────────────────────────── هندلرِ فایل ───────────────────────────

def _refresh_keyboard(uid, message=None):
    """کیبوردِ پایینِ چت را تازه می‌کند (مثلِ @reply_with_keyboard، ولی تنبل/lazy
    تا این ماژول بتواند *قبل از* لودشدنِ بقیهٔ ماژول‌ها ایمپورت شود)."""
    try:
        from HELPERS.decorators import _get_keyboard_mode, send_reply_keyboard_always
        enabled, mode = _get_keyboard_mode(uid)
        if enabled:
            send_reply_keyboard_always(uid, mode)
    except Exception:
        pass


@app.on_message(filters.private & (filters.document | filters.video | filters.audio
                                   | filters.animation | filters.photo))
def filelink_media(app, message):
    try:
        _refresh_keyboard(int(message.chat.id), message)
    except Exception:
        pass
    try:
        uid = int(message.chat.id)
    except Exception:
        return
    info = _media_of(message)
    if not info:
        return
    name, size, kind = info

    # ۱) کاربرِ عادی: رفتارِ قبلیِ پروژه (فایلِ کوکی) دست‌نخورده بماند
    if not is_admin(uid):
        if kind == "document":
            _delegate_cookie(app, message)
        return

    # ۲) فایلِ کوکیِ خودِ ادمین هم به مسیرِ خودش برود
    if _looks_like_cookie(message):
        _delegate_cookie(app, message)
        return

    # ۳) بزرگ‌تر از سقفِ خودِ تلگرام (۲ گیگ) ⇒ حتی با MTProto هم نمی‌شود
    if size > fl.MAX_TG_DOWNLOAD:
        safe_send_message(uid,
                          "⚠️ این فایل <b>%s</b> است و تلگرام به ربات بیشتر از "
                          "<b>۲ گیگابایت</b> نمی‌دهد.\n\n"
                          "اگر فایل بزرگ‌تری داری، تکه‌تکه‌اش کن (دستورِ /split) یا "
                          "لینکش را جای دیگری بگذار و به ربات بده تا خودم بگیرم."
                          % fl.human_size(size))
        return

    # ۴) پرسشِ انقضا
    safe_send_message(uid,
                      "🔗 <b>%s</b> — %s (%s)\n\nلینکِ مستقیم بسازم؟ چند وقت زنده بماند؟"
                      % (name, fl.human_size(size), kind),
                      reply_markup=_expiry_keyboard(uid, getattr(message, "id", 0)))
    return


# ─────────────────────────── دکمه‌ها ───────────────────────────

@app.on_callback_query(filters.regex(r"^flmk\|"))
def flmk_callback(app, cq):
    if not is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    try:
        _, ttl, chat_id, msg_id = cq.data.split("|", 3)
        chat_id, msg_id = int(chat_id), int(msg_id)
    except Exception:
        cq.answer("خطا در داده", show_alert=True)
        return True
    uid = int(cq.message.chat.id)
    cq.answer("⏳ در حال آماده‌سازیِ لینک...")
    fl.start_server()
    base = fl.base_url()
    if not base:
        safe_send_message(uid, "⚠️ اول باید دامنهٔ عمومی بسازی: Railway → Settings → Networking → "
                               "Generate Domain (پورت ۸۰۸۰) و بعد «🔄» را بزن.")
        return True
    try:
        msg = run_pyrogram_client_coroutine(app, app.get_messages(chat_id, msg_id), timeout=60)
    except Exception as e:
        logger.error(f"filelink: get_messages failed: {e}")
        msg = None
    if msg is None:
        safe_send_message(uid, "❌ پیامِ فایل پیدا نشد (پاکش کردی؟) — دوباره بفرست.")
        return True
    info = _media_of(msg)
    if not info:
        safe_send_message(uid, "❌ فایل پیدا نشد — دوباره بفرست.")
        return True
    name, size, _kind = info
    if size > fl.MAX_TG_DOWNLOAD:
        safe_send_message(uid, "⚠️ این فایل از سقفِ ۲ گیگابایتیِ تلگرام بزرگ‌تر است.")
        return True
    # فایل‌های بزرگ چند دقیقه‌ای طول می‌کشند ⇒ همان پیام را ⏳ می‌کنیم و بعد لینک می‌گذاریم
    with contextlib.suppress(Exception):
        safe_edit_message_text(uid, cq.message.id,
                               "⏳ دارم فایل (<b>%s</b>) را از تلگرام می‌گیرم…"
                               % fl.human_size(size))
    tmp = os.path.join(fl.links_dir(), "tmp_%s_%s" % (secrets.token_hex(4), name))
    try:
        got = run_pyrogram_client_coroutine(app, app.download_media(msg, file_name=tmp),
                                            timeout=fl.DOWNLOAD_TIMEOUT)
        path = got if isinstance(got, str) else tmp
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            raise RuntimeError("file not downloaded")
    except Exception as e:
        logger.error(f"filelink: download failed: {e}")
        with contextlib.suppress(Exception):
            safe_edit_message_text(uid, cq.message.id, "❌ دانلودِ فایل از تلگرام نشد.")
        safe_send_message(uid,
                          "❌ فایل را نتوانستم از تلگرام بگیرم: <code>%s</code>\n\n"
                          "یک بار دیگر امتحان کن؛ اگر باز هم نشد بگو، راهِ دیگری "
                          "(سرورِ Bot API محلی) می‌گذاریم." % str(e)[:150])
        return True
    rec = fl.add_file(path, name, uid, ttl, message_id=msg_id)
    with contextlib.suppress(Exception):
        safe_edit_message_text(uid, cq.message.id, _link_text(rec), reply_markup=_link_keyboard(rec["token"]))
    return True


@app.on_callback_query(filters.regex(r"^flrm\|"))
def flrm_callback(app, cq):
    if not is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    token = cq.data.split("|", 1)[1]
    uid = int(cq.message.chat.id)
    ok = fl.delete(token, notify=False)
    cq.answer("پاک شد ✅" if ok else "پیدا نشد")
    with contextlib.suppress(Exception):
        safe_edit_message_text(uid, cq.message.id, "🗑 فایل و لینکش پاک شد." if ok
                               else "❌ این لینک پیدا نشد (قبلاً پاک شده).")
    return True


@app.on_callback_query(filters.regex(r"^flex\|"))
def flex_callback(app, cq):
    if not is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    try:
        _, token, ttl = cq.data.split("|", 2)
    except Exception:
        cq.answer("خطا", show_alert=True)
        return True
    rec = fl.extend(token, ttl)
    if not rec:
        cq.answer("این لینک دیگر نیست", show_alert=True)
        return True
    cq.answer("تمدید شد ⏱")
    with contextlib.suppress(Exception):
        safe_edit_message_text(int(cq.message.chat.id), cq.message.id, _link_text(rec),
                               reply_markup=_link_keyboard(token))
    return True


@app.on_callback_query(filters.regex(r"^fllist$"))
def fllist_callback(app, cq):
    if not is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    show_panel(int(cq.message.chat.id), cq.message.id)
    cq.answer("به‌روز شد 🔄")
    return True


@app.on_callback_query(filters.regex(r"^flclean$"))
def flclean_callback(app, cq):
    if not is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    safe_send_message(int(cq.message.chat.id), "🧹 همهٔ فایل‌ها و لینک‌ها پاک شوند؟",
                      reply_markup=InlineKeyboardMarkup([[
                          InlineKeyboardButton("✅ پاک کن", callback_data="flcleanok"),
                          InlineKeyboardButton("❌ انصراف", callback_data="flclose")]]))
    cq.answer("تأیید لازم است")
    return True


@app.on_callback_query(filters.regex(r"^flcleanok$"))
def flcleanok_callback(app, cq):
    if not is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    n = fl.clear_all()
    cq.answer("پاک شد ✅")
    safe_send_message(int(cq.message.chat.id), "🧹 %d فایل/لینک پاک شد." % n)
    return True


@app.on_callback_query(filters.regex(r"^flclose$"))
def flclose_callback(app, cq):
    with contextlib.suppress(Exception):
        app.delete_messages(cq.message.chat.id, [cq.message.id])
    cq.answer("بسته شد")
    return True


# ─────────────────────────── متن‌ها (دستور/دکمهٔ کیبورد) ───────────────────────────

def handle_filelink_text(app, message) -> bool:
    try:
        uid = int(message.chat.id)
    except Exception:
        return False
    text = (getattr(message, "text", None) or "").strip()
    if not text:
        return False
    head = text.lower().split()[0].split("@")[0]
    label = text.replace("\u200c", "").strip()
    if head in _COMMANDS or label in LABELS or label.replace("\u200c", "") in LABELS:
        if not is_admin(uid):
            safe_send_message(uid, "⛔️ این بخش فقط برای مدیرِ ربات است.")
            return True
        show_panel(uid)
        return True
    return False


# پاک‌سازیِ دوره‌ایِ لینک‌های منقضی از همان لحظهٔ بالا آمدنِ ربات
try:
    fl.start_sweeper()
except Exception as _e:      # noqa: BLE001
    logger.warning(f"filelink: sweeper not started: {_e}")
