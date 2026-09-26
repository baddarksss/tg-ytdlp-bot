# -*- coding: utf-8 -*-
"""دستورِ /channels — مدیریتِ کانال‌های مقصدِ فیلم (چند‌کاناله).

• ``/channels``            ⇒ پنل: فهرستِ کانال‌ها + وضعیتِ ادمین‌بودنِ ربات
• دکمهٔ «➕ افزودن کانال»   ⇒ آیدی را ریپلای می‌کنی و اضافه می‌شود
• ``/addchannel -100…``     ⇒ همان کار به‌صورت دستی
• دکمه‌های زیرِ فیلم        ⇒ ``chcopy|<key>`` = فرستادنِ همان فیلم به آن کانال
"""
from pyrogram import filters
from pyrogram.types import (InlineKeyboardMarkup, InlineKeyboardButton,
                            ForceReply, ReplyParameters)

from CONFIG.config import Config
from HELPERS.app_instance import get_app
from HELPERS.logger import logger, send_to_logger
from HELPERS.safe_messeger import (safe_send_message, safe_edit_message_text,
                                   safe_edit_reply_markup, safe_copy_message,
                                   safe_delete_messages)
from HELPERS import channel_store as cs

app = get_app()

# کاربرهایی که منتظرِ «آیدی کانال» هستند:  user_id -> message_id پرسش
_PENDING_ADD: dict = {}
# کانال‌هایی که فیلم در آن‌ها کپی شده:  (chat_id, message_id) -> set(keys)
_COPIED: dict = {}
ASK_TEXT = ("📢 آیدیِ کانال را بفرست (مثال: <code>-1001234567890</code> یا "
            "<code>@mychannel</code>).\n"
            "روی <b>همین پیام ریپلای</b> کن و بفرست؛ من خودم چک می‌کنم که در آن "
            "کانال ادمین باشم. اگر لینکِ <code>t.me</code> هم بدهی قبول می‌کنم.")


def _is_admin(user_id) -> bool:
    try:
        return int(user_id) in set(getattr(Config, "ADMIN", []) or [])
    except Exception:
        return False


def _ask_for_channel(user_id: int, reply_to: int = 0):
    """پیامِ درخواستِ آیدی کانال (با ForceReply ⇒ کاربر همان‌جا ریپلای می‌کند)."""
    msg = safe_send_message(
        user_id, ASK_TEXT,
        reply_parameters=ReplyParameters(message_id=reply_to) if reply_to else None,
        reply_markup=ForceReply(selective=True),
    )
    if msg is not None:
        _PENDING_ADD[int(user_id)] = getattr(msg, "id", 0)
    return msg


def _register_channel(app_obj, user_id: int, raw: str, answer_msg_id: int = 0) -> bool:
    """بررسیِ آیدی، چکِ ادمین‌بودنِ ربات، ذخیره و پاسخ به کاربر."""
    key, err = cs.normalize(raw)
    if err:
        safe_send_message(user_id, "❌ %s" % err)
        return False
    ok, title, aerr = cs.bot_can_post(app_obj, key, force=True)
    if not ok:
        safe_send_message(user_id,
                          "❌ <b>%s</b>\n%s\n\n"
                          "• ربات را در آن کانال <b>ادمین</b> کن (با اجازهٔ «ارسال پیام»)\n"
                          "• آیدی عددی را از فورواردِ یک پیامِ کانال به "
                          "@userinfobot می‌گیری"
                          % (key, aerr or "ربات در این کانال ادمین نیست"))
        return False
    added, msg = cs.add_channel(key, title)
    name = title or key
    if added:
        safe_send_message(user_id,
                          "✅ کانال اضافه شد: <b>%s</b>\n<code>%s</code>\n%s"
                          % (name, key, msg if msg != "افزوده شد" else ""))
        logger.info(f"channels: added {key} ({name}) by {user_id}")
    else:
        safe_send_message(user_id, "❌ %s" % msg)
        return False
    _show_panel(user_id, answer_msg_id)
    return True


def _show_panel(user_id: int, answer_msg_id: int = 0):
    text, kb = cs.panel(app)
    if answer_msg_id:
        if safe_edit_message_text(user_id, answer_msg_id, text, reply_markup=kb) is not None:
            return
    safe_send_message(user_id, text, reply_markup=kb)


# ─────────────────────────── دستورها ───────────────────────────

@app.on_message(filters.command("channels") & filters.private)
def channels_command(app, message):
    """پنلِ کانال‌ها (فقط ادمین)."""
    user_id = int(message.chat.id)
    if not _is_admin(user_id):
        safe_send_message(user_id, "⛔️ این دستور فقط برای مدیرِ ربات است.")
        return True
    text, kb = cs.panel(app)
    safe_send_message(user_id, text, reply_markup=kb)
    send_to_logger(message, f"channels panel opened ({len(cs.channels())} channels)")
    return True


@app.on_message(filters.command("addchannel") & filters.private)
def addchannel_command(app, message):
    """``/addchannel -100…`` یا ``/addchannel @name``"""
    user_id = int(message.chat.id)
    if not _is_admin(user_id):
        safe_send_message(user_id, "⛔️ این دستور فقط برای مدیرِ ربات است.")
        return True
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        _ask_for_channel(user_id, message.id)
        return True
    _register_channel(app, user_id, parts[1].strip())
    return True


@app.on_message(filters.private & filters.reply & filters.text)
def channel_reply_handler(app, message):
    """پاسخِ ریپلای به پیامِ «آیدی کانال را بفرست» ⇒ افزودنِ کانال.

    اگر کاربر منتظر نیست، False برمی‌گردانیم تا بقیهٔ هندلرها (روترِ لینک) کار کنند.
    """
    user_id = int(message.chat.id)
    ask_id = _PENDING_ADD.get(user_id, 0)
    if not ask_id:
        return False
    replied = getattr(getattr(message, "reply_to_message", None), "id", 0) or 0
    if replied != ask_id:
        return False
    _PENDING_ADD.pop(user_id, None)
    if not _is_admin(user_id):
        return True
    _register_channel(app, user_id, (message.text or "").strip(), ask_id)
    return True


# ─────────────────────────── دکمه‌های پنل ───────────────────────────

@app.on_callback_query(filters.regex(r"^chadd$"))
def chadd_callback(app, cq):
    if not _is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    cq.answer("آیدیِ کانال را ریپلای کن")
    _ask_for_channel(int(cq.message.chat.id), getattr(cq.message, "id", 0))
    return True


@app.on_callback_query(filters.regex(r"^chrefresh$"))
def chrefresh_callback(app, cq):
    if not _is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    cs.refresh_titles(app)
    text, kb = cs.panel(app)
    try:
        cq.message.edit_text(text, reply_markup=kb)
    except Exception as e:
        logger.warning(f"channels: refresh edit failed: {e}")
    cq.answer("بررسی شد ✅")
    return True


@app.on_callback_query(filters.regex(r"^chdel\|"))
def chdel_callback(app, cq):
    if not _is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    key = cq.data.split("|", 1)[1]
    ok, msg = cs.remove_channel(key)
    text, kb = cs.panel(app)
    try:
        cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    cq.answer(msg if ok else ("⚠️ " + msg), show_alert=not ok)
    return True


@app.on_callback_query(filters.regex(r"^chlock$"))
def chlock_callback(app, cq):
    cq.answer("این کانال از متغیرِ COPY_CHANNEL_ID می‌آید — از Railway → Variables حذفش کن",
              show_alert=True)
    return True


@app.on_callback_query(filters.regex(r"^chhide$"))
def chhide_callback(app, cq):
    """بستنِ دکمه‌های زیرِ فیلم."""
    safe_edit_reply_markup(cq.message.chat.id, cq.message.id, reply_markup=None)
    cq.answer("بسته شد")
    return True


# ─────────────────────────── ارسالِ فیلم به کانال ───────────────────────────

def send_film_to_channel(app, cq, key: str) -> bool:
    """همان فیلمِ زیرِ دکمه‌ها را به کانال می‌فرستد (کپیِ تمیز، با همان کپشن)."""
    ch = cs.get(key)
    if not ch:
        cq.answer("این کانال در فهرست نیست — /channels", show_alert=True)
        return False
    ok, title, err = cs.bot_can_post(app, key, force=True)
    if not ok:
        cq.answer("❌ %s" % (err or "ربات در این کانال ادمین نیست"), show_alert=True)
        return False
    target = ch["id"]
    src_chat = int(cq.message.chat.id)
    src_msg = int(getattr(cq.message, "id", 0) or 0)
    copied = safe_copy_message(target, src_chat, src_msg)
    if copied is None:
        cq.answer("❌ نشد — ربات در آن کانال ادمین است؟", show_alert=True)
        logger.error(f"channels: copy to {key} failed (msg {src_chat}/{src_msg})")
        return False
    marker = (src_chat, src_msg)
    _COPIED.setdefault(marker, set()).add(key)
    safe_edit_reply_markup(src_chat, src_msg, reply_markup=cs.film_keyboard(_COPIED[marker]))
    cq.answer("✅ رفت به «%s»" % (title or key))
    logger.info(f"channels: film {src_chat}/{src_msg} copied to {key} ({title})")
    return True


@app.on_callback_query(filters.regex(r"^chcopy\|"))
def chcopy_callback(app, cq):
    key = cq.data.split("|", 1)[1]
    copied_keys = _COPIED.get((int(cq.message.chat.id), int(cq.message.id or 0)), set())
    if key in copied_keys:
        cq.answer("قبلاً همین فیلم را در این کانال گذاشته‌ام", show_alert=True)
        return True
    send_film_to_channel(app, cq, key)
    return True
