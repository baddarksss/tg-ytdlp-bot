# -*- coding: utf-8 -*-
"""دکمه‌های پنلِ متغیرها (Variables): دیدن، بکاپ، ویرایش، حذف، افزودن.

• باز کردن: دستور ``/vars`` یا دکمهٔ «🧩 متغیرها» در کیبوردِ پایینِ چت
  (هر دو از HELPERS/extra_router مسیریابی می‌شوند).
• نوشتن روی متغیرها فقط با ``RAILWAY_API_TOKEN`` ممکن است.
"""
import contextlib

from pyrogram import filters
from pyrogram.types import ForceReply, ReplyParameters

from HELPERS.app_instance import get_app
from HELPERS.logger import logger
from HELPERS.safe_messeger import safe_send_message, safe_edit_message_text, safe_delete_messages
from HELPERS import vars_store as vs

app = get_app()

# متنِ مسیریابیِ متن‌ها در همان ماژولِ vars_store است
handle_vars_text = vs.handle_vars_text


def _is_admin(user_id) -> bool:
    return vs.is_admin(user_id)


def _send_file(uid, text: str, file_name: str, caption: str):
    """فایل را از تردِ هندلرِ سینک می‌فرستد (کوروتینِ کلاینت باید await شود)."""
    import io
    with contextlib.suppress(Exception):
        from HELPERS.safe_messeger import run_pyrogram_client_coroutine
        run_pyrogram_client_coroutine(app, app.send_document(
            uid, io.BytesIO(text.encode("utf-8")), file_name=file_name, caption=caption))


# ─────────────────────────── پنل ───────────────────────────

@contextlib.contextmanager
def _progress(cq, text: str = "⏳"):
    with contextlib.suppress(Exception):
        cq.answer(text)


@app.on_callback_query(filters.regex(r"^vmenu$"))
def vmenu_callback(app, cq):
    if not _is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    uid = int(cq.message.chat.id)
    try:
        text, kb = vs.panel(False)
        safe_edit_message_text(uid, cq.message.id, text, reply_markup=kb)
    except Exception as e:
        logger.error(f"vars panel refresh failed: {e}")
    cq.answer("به‌روز شد ✅")
    return True


@app.on_callback_query(filters.regex(r"^vfull$"))
def vfull_callback(app, cq):
    if not _is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    uid = int(cq.message.chat.id)
    text, kb = vs.panel(True)
    # متنِ کاملِ مقدارها ممکن است بلند شود ⇒ فایل + پیامِ کوتاه
    if len(text) > 3800:
        vs.send_text(app, uid, text, file_name="variables-full.txt",
                     caption="🔓 لیستِ کاملِ متغیرها (متنِ چت جا نمی‌شد)")
    else:
        safe_edit_message_text(uid, cq.message.id, text, reply_markup=kb)
    cq.answer("حالتِ کامل 🔓")
    return True


@app.on_callback_query(filters.regex(r"^vbackup$"))
def vbackup_callback(app, cq):
    if not _is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    uid = int(cq.message.chat.id)
    vars_, source, warn = vs.variables()
    _send_file(uid, vs.backup_file_text(vars_, source),
               vs.BACKUP_FILE, "💾 بکاپِ کلِ متغیرها (متنِ کامل) — جای عمومی نفرست")
    safe_send_message(uid, ("📤 فایلِ بکاپ فرستاده شد — <b>%d</b> متغیر (منبع: %s).\n"
                            "این فایل همهٔ مقدارها را کامل دارد؛ جای عمومی نفرست.\n%s"
                            % (len(vars_), source, ("⚠️ " + warn) if warn else "")))
    cq.answer("بکاپ فرستاده شد ✅")
    return True


@app.on_callback_query(filters.regex(r"^vchannels$"))
def vchannels_callback(app, cq):
    if not _is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    uid = int(cq.message.chat.id)
    from COMMANDS.channels_cmd import show_panel
    show_panel(uid, cq.message.id)
    cq.answer("پنلِ کانال‌ها 📢")
    return True


@app.on_callback_query(filters.regex(r"^vclose$"))
def vclose_callback(app, cq):
    with contextlib.suppress(Exception):
        vs._STATE.pop(int(cq.from_user.id), None)
    with contextlib.suppress(Exception):
        app.delete_messages(cq.message.chat.id, [cq.message.id])
    cq.answer("بسته شد")
    return True


# ─────────────────────────── ویرایش / حذف / افزودن ───────────────────────────

@app.on_callback_query(filters.regex(r"^vset$"))
def vset_callback(app, cq):
    if not _is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    uid = int(cq.message.chat.id)
    vars_, _src, _w = vs.variables()
    text, kb = vs.picker("set", vars_)
    safe_send_message(uid, text, reply_markup=kb)
    vs._set_state(uid, "choose_set")
    cq.answer("اسمِ متغیر را انتخاب/بنویس")
    return True


@app.on_callback_query(filters.regex(r"^vdel$"))
def vdel_callback(app, cq):
    if not _is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    uid = int(cq.message.chat.id)
    vars_, _src, _w = vs.variables()
    text, kb = vs.picker("del", vars_)
    safe_send_message(uid, text, reply_markup=kb)
    vs._set_state(uid, "choose_del")
    cq.answer("اسمِ متغیرِ حذف‌شدنی را انتخاب/بنویس")
    return True


@app.on_callback_query(filters.regex(r"^vadd$"))
def vadd_callback(app, cq):
    if not _is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    uid = int(cq.message.chat.id)
    vs._set_state(uid, "add")
    safe_send_message(uid, "➕ متغیرِ جدید را به این شکل بفرست:\n<code>NAME=value</code>",
                      reply_markup=ForceReply(selective=True),
                      reply_parameters=ReplyParameters(message_id=cq.message.id))
    cq.answer("منتظرِ NAME=value")
    return True


@app.on_callback_query(filters.regex(r"^vset\|"))
def vset_named_callback(app, cq):
    if not _is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    uid = int(cq.message.chat.id)
    name = cq.data.split("|", 1)[1].strip()
    vs._set_state(uid, "set_value", name)
    safe_send_message(uid, "✏️ مقدارِ جدیدِ <code>%s</code> را بفرست (ریپلای لازم نیست)." % name,
                      reply_markup=ForceReply(selective=True),
                      reply_parameters=ReplyParameters(message_id=cq.message.id))
    cq.answer("منتظرِ مقدارِ جدید")
    return True


@app.on_callback_query(filters.regex(r"^vdel\|"))
def vdel_named_callback(app, cq):
    if not _is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    uid = int(cq.message.chat.id)
    name = cq.data.split("|", 1)[1].strip()
    vs._ask_delete(uid, name, app)
    cq.answer("تأیید لازم است")
    return True


@app.on_callback_query(filters.regex(r"^vdelok\|"))
def vdelok_callback(app, cq):
    if not _is_admin(cq.from_user.id):
        cq.answer("⛔️ فقط مدیرِ ربات", show_alert=True)
        return True
    uid = int(cq.message.chat.id)
    name = cq.data.split("|", 1)[1].strip()
    ok, msg = vs.delete_variable(name)
    safe_send_message(uid, ("🗑 <b>%s</b> حذف شد.\n%s" % (name, msg)) if ok
                      else ("❌ حذف نشد — %s: %s" % (name, msg)))
    with contextlib.suppress(Exception):
        vs._STATE.pop(uid, None)
    cq.answer("حذف شد ✅" if ok else "نشد", show_alert=not ok)
    return True
