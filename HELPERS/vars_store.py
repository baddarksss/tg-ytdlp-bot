# -*- coding: utf-8 -*-
"""پنلِ متغیرها (Variables) — دیدن، بکاپ‌گرفتن، ویرایش و حذف از داخلِ ربات.

چرا این ماژول:
  • کاربر (خودت) نخواهی هر بار بروی Railway → Variables؛ از داخلِ چت هم فهرستِ
    کاملِ متغیرها را می‌بینی، هم فایلِ بکاپ می‌گیری، هم مقدار را عوض/حذف می‌کنی.
  • خواندن: اگر ``RAILWAY_API_TOKEN`` ست باشد، فهرست از API ریلوی خوانده می‌شود
    (همان چیزی که در داشبورد می‌بینی). اگر نباشد، از محیطِ خودِ برنامه (env) که
    Railway همان متغیرها را داخلش گذاشته خوانده می‌شود.
  • نوشتن (ویرایش/حذف/افزودن): فقط با توکنِ ریلوی ممکن است.

امنیت: همهٔ دکمه‌ها فقط برای ADMIN کار می‌کنند. مقدارهای حسّاس در «متنِ چت»
ماسک می‌شوند و مقدارِ کامل فقط داخلِ فایلِ .env فرستاده می‌شود.
"""
from __future__ import annotations

import contextlib
import json
import os
import urllib.request

from CONFIG.config import Config
from HELPERS.logger import logger

RAILWAY_GQL = "https://backboard.railway.com/graphql/v2"
BACKUP_FILE = "railway-variables.env"

# حالتِ گفتگو برای هر کاربر: {user_id: {"action": "set_value"|"choose_set"|"choose_del"|"add", "name": str}}
_STATE: dict = {}

# نام‌هایی که حذفشان ربات را از کار می‌اندازد ⇒ هشدارِ ویژه
CRITICAL = {"BOT_TOKEN", "API_ID", "API_HASH", "ADMIN", "TELEGRAM_API_ID", "TELEGRAM_API_HASH"}

_SECRET_HINTS = ("TOKEN", "KEY", "HASH", "SECRET", "PASS", "COOKIE", "SESSION", "AUTH", "SID")

# متغیرهای داخلیِ کانتینر که به کارِ کاربر نمی‌آیند (فقط در حالتِ env فیلتر می‌شوند)
_ENV_NOISE = {
    "PATH", "HOME", "HOSTNAME", "PWD", "OLDPWD", "SHLVL", "TERM", "LANG", "LC_ALL", "CI",
    "PORT", "_", "VIRTUAL_ENV", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH",
    "DEBIAN_FRONTEND", "LS_COLORS", "GIT_PYTHON_REFRESH", "UV_INDEX_URL", "UV_CACHE_DIR",
}


def is_admin(user_id) -> bool:
    try:
        return int(user_id) in set(getattr(Config, "ADMIN", []) or [])
    except Exception:
        return False


# ─────────────────────────── خواندنِ متغیرها ───────────────────────────

def railway_ids() -> tuple:
    """آیدی‌های پروژه/محیط/سرویس (Railway خودش داخلِ محیط می‌گذارد)."""
    def g(name):
        return str(getattr(Config, name, "") or os.environ.get(name, "")).strip()

    return g("RAILWAY_PROJECT_ID"), g("RAILWAY_ENVIRONMENT_ID"), g("RAILWAY_SERVICE_ID")


def token() -> str:
    return (str(getattr(Config, "RAILWAY_API_TOKEN", "") or "")
            or os.environ.get("RAILWAY_API_TOKEN", "")
            or os.environ.get("RAILWAY_TOKEN", "")).strip()


def _gql(query: str, variables: dict = None) -> tuple:
    """یک کوئری/میوتیشنِ GraphQL. خروجی: (data یا None, پیامِ خطا)."""
    tok = token()
    if not tok:
        return None, "توکنِ ریلوی ست نشده (RAILWAY_API_TOKEN)"
    body = {"query": query}
    if variables:
        body["variables"] = variables
    try:
        req = urllib.request.Request(
            RAILWAY_GQL, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok},
            method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8", "replace") or "{}")
        if data.get("errors"):
            return None, str(data["errors"][0].get("message") or data["errors"][0])[:180]
        return data.get("data") or {}, ""
    except Exception as e:
        logger.error(f"vars_store: railway api error: {e}")
        return None, str(e)[:180]


def railway_variables() -> tuple:
    """متغیرها از API ریلوی: (dict یا None, پیام)."""
    pid, eid, sid = railway_ids()
    if not (pid and eid and sid):
        return None, "آیدی پروژه/محیط/سرویسِ Railway در دسترس نیست"
    q = ("query($p:String!,$e:String!,$s:String!){"
         "variables(projectId:$p, environmentId:$e, serviceId:$s)}")
    data, err = _gql(q, {"p": pid, "e": eid, "s": sid})
    if data is None:
        return None, err
    v = data.get("variables")
    if not isinstance(v, dict):
        return None, "پاسخِ غیرمنتظره از ریلوی"
    return {k: ("" if v[k] is None else str(v[k])) for k in sorted(v)}, ""


def env_variables() -> dict:
    out = {}
    for k, v in os.environ.items():
        if k in _ENV_NOISE or k.startswith(("npm_", "YARN_", "NODE_", "PIP_")):
            continue
        out[k] = "" if v is None else str(v)
    return dict(sorted(out.items()))


def variables() -> tuple:
    """(dictِ متغیرها, منبع, هشدار) — اول API، بعد env."""
    if token():
        v, err = railway_variables()
        if v:
            return v, "Railway API", ""
        # توکن دارد ولی نخواند ⇒ با env کار می‌کنیم و پیام می‌دهیم
        return env_variables(), "محیطِ برنامه (env)", err
    return env_variables(), "محیطِ برنامه (env)", ""


# ─────────────────────────── نمایش ───────────────────────────

def mask(name: str, value: str) -> str:
    v = "" if value is None else str(value)
    if v == "":
        return "(خالی)"
    up = name.upper()
    if any(h in up for h in _SECRET_HINTS) or len(v) > 60 or "\n" in v:
        head = v.split("\n")[0][:4]
        return "%s…%s (%d کاراکتر)" % (head, v[-3:] if len(v) > 8 else "", len(v))
    return v


def _fmt_value(name: str, value: str, show_full: bool = False) -> str:
    v = "" if value is None else str(value)
    if "\n" in v and not show_full:
        return mask(name, v)
    if show_full:
        if "\n" in v:
            return "<pre>%s</pre>" % v[:1500]
        return "<code>%s</code>" % (v if v else "(خالی)")
    return mask(name, v)


def list_text(vars_: dict, source: str, show_full: bool = False, note: str = "") -> str:
    lines = ["🧩 <b>متغیرهای ربات</b>  <i>(%d تا · منبع: %s)</i>" % (len(vars_), source)]
    if note:
        lines.append("⚠️ %s" % note)
    if show_full:
        lines.append("🔓 حالتِ کامل — این پیام مقدارهای محرمانه را دارد، جایی نفرست.")
    lines.append("")
    for k in sorted(vars_):
        lines.append("• <b>%s</b> = %s" % (k, _fmt_value(k, vars_[k], show_full)))
    if not vars_:
        lines.append("چیزی پیدا نشد.")
    return "\n".join(lines)


def panel(show_full: bool = False) -> tuple:
    """(متن, کیبورد) پنلِ متغیرها."""
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    vars_, source, warn = variables()
    text = list_text(vars_, source, show_full, warn)
    rows = [
        [InlineKeyboardButton("📤 بکاپِ کلِ متغیرها (فایل)", callback_data="vbackup"),
         InlineKeyboardButton("🔓 نمایشِ کامل" if not show_full else "🔒 حالتِ پوشیده",
                              callback_data="vfull")],
        [InlineKeyboardButton("✏️ ویرایش", callback_data="vset"),
         InlineKeyboardButton("🗑 حذف", callback_data="vdel"),
         InlineKeyboardButton("➕ افزودن", callback_data="vadd")],
        [InlineKeyboardButton("🔄 به‌روزرسانی", callback_data="vmenu"),
         InlineKeyboardButton("📢 کانال‌ها", callback_data="vchannels")],
        [InlineKeyboardButton("✖️ بستن", callback_data="vclose")],
    ]
    return text, InlineKeyboardMarkup(rows)


def picker(action: str, vars_: dict) -> tuple:
    """(متن, کیبورد) انتخابِ نامِ متغیر برای ویرایش/حذف."""
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    verb = "ویرایش" if action == "set" else "حذف"
    lines = ["✏️ <b>%sِ کدام متغیر؟</b>" % verb,
             "",
             "روی نامش بزن، یا خودت اسم/عدد را همین‌جا بنویس و بفرست.",
             "(نام‌ها: %d تا)" % len(vars_)]
    names = sorted(vars_)
    rows, row = [], []
    for n in names:
        row.append(InlineKeyboardButton(("⚠️ " if (action == "del" and n in CRITICAL) else "") + n,
                                        callback_data="v%s|%s" % (action, n)))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("✖️ انصراف", callback_data="vclose")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def backup_file_text(vars_: dict, source: str) -> str:
    """محتوای فایلِ .env — همهٔ متغیرها با مقدارِ کامل."""
    out = ["# بکاپِ متغیرهای ربات", "# منبع: %s" % source,
           "# تاریخ: %s" % __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"), ""]
    for k in sorted(vars_):
        v = "" if vars_[k] is None else str(vars_[k])
        if "\n" in v or '"' in v:
            v = '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
        out.append("%s=%s" % (k, v))
    return "\n".join(out) + "\n"


# ─────────────────────────── نوشتن (نیاز به توکن) ───────────────────────────

def set_variable(name: str, value: str) -> tuple:
    pid, eid, sid = railway_ids()
    if not (pid and eid and sid):
        return False, "آیدی پروژه/محیط/سرویسِ Railway پیدا نشد"
    q = ("mutation($i:VariableUpsertInput!){variableUpsert(input:$i)}")
    data, err = _gql(q, {"i": {"projectId": pid, "environmentId": eid, "serviceId": sid,
                               "name": name, "value": value}})
    if data is None:
        return False, err
    if data.get("variableUpsert"):
        logger.info(f"vars_store: set {name} ({len(value)} chars)")
        return True, "ذخیره شد ✅ (ریلوی سرویس را یک بار ری‌استارت می‌کند — طبیعی است)"
    return False, "ریلوی قبول نکرد"


def delete_variable(name: str) -> tuple:
    pid, eid, sid = railway_ids()
    if not (pid and eid and sid):
        return False, "آیدی پروژه/محیط/سرویسِ Railway پیدا نشد"
    q = ("mutation($i:VariableDeleteInput!){variableDelete(input:$i)}")
    data, err = _gql(q, {"i": {"projectId": pid, "environmentId": eid, "serviceId": sid,
                               "name": name}})
    if data is None:
        return False, err
    if data.get("variableDelete"):
        logger.info(f"vars_store: deleted {name}")
        return True, "حذف شد ✅ (ریلوی سرویس را یک بار ری‌استارت می‌کند)"
    return False, "ریلوی قبول نکرد"


# ─────────────────────────── گفتگو (مسیریابیِ متن) ───────────────────────────

LABELS = {"📢 کانال‌ها", "کانال‌ها", "📢 کانالها", "کانالها",
          "🧩 متغیرها", "متغیرها", "🧩 متغیرها / وریبل‌ها"}


def _clear(uid):
    _STATE.pop(int(uid), None)


def _set_state(uid, action, name=""):
    _STATE[int(uid)] = {"action": action, "name": name}


def _state_of(uid):
    return _STATE.get(int(uid)) or {}


def _ask(uid, text, reply_to=0):
    from HELPERS.safe_messeger import safe_send_message
    from pyrogram.types import ForceReply, ReplyParameters
    safe_send_message(uid, text, reply_markup=ForceReply(selective=True),
                      reply_parameters=ReplyParameters(message_id=reply_to) if reply_to else None)


def _show_panel(uid, answer_msg_id=0, show_full=False):
    from HELPERS.safe_messeger import safe_edit_message_text, safe_send_message
    text, kb = panel(show_full)
    if answer_msg_id:
        if safe_edit_message_text(uid, answer_msg_id, text, reply_markup=kb) is not None:
            return
    safe_send_message(uid, text, reply_markup=kb)


def send_text(app, uid, text, file_name: str = "", caption: str = ""):
    """متنِ بلند را یا پیام می‌فرستد یا فایل (برای لیستِ کامل/بکاپ)."""
    from HELPERS.safe_messeger import safe_send_message
    if len(text) <= 3800:
        safe_send_message(uid, text)
        return
    import io
    with contextlib.suppress(Exception):
        # هندلرهای سینک در ترد اجرا می‌شوند ⇒ متدِ async کلاینت باید این‌طور صدا زده شود
        from HELPERS.safe_messeger import run_pyrogram_client_coroutine
        run_pyrogram_client_coroutine(app, app.send_document(
            uid, io.BytesIO(text.encode("utf-8")),
            file_name=file_name or "list.txt", caption=caption or "متن زیاد بود ⇒ فایل"))


def handle_vars_text(app, message) -> bool:
    """نقطهٔ ورودِ متن‌ها — از HELPERS/extra_router صدا زده می‌شود. True = مصرف شد."""
    try:
        uid = int(message.chat.id)
    except Exception:
        return False
    text = (getattr(message, "text", None) or "").strip()
    if not text:
        return False
    head = text.lower().split()[0].split("@")[0]
    label = text.replace("\u200c", "").strip()

    # ۱) دستور/برچسبِ بازکردنِ پنل
    if head in ("/vars", "/var", "/variables", "/env") or label in LABELS - {"📢 کانال‌ها", "کانال‌ها"}:
        if not is_admin(uid):
            from HELPERS.safe_messeger import safe_send_message
            safe_send_message(uid, "⛔️ این دستور فقط برای مدیرِ ربات است.")
            return True
        _clear(uid)
        _show_panel(uid)
        return True

    st = _state_of(uid)
    if not st:
        return False
    if not is_admin(uid):
        _clear(uid)
        return False

    action = st.get("action")

    # ۱.۵) لغو و محافظت: وسطِ جریان، لینک را به‌عنوان مقدار ذخیره نکن
    if text in ("/cancel", "/لغو", "لغو", "✖️ انصراف", "✖️ لغو"):
        _clear(uid)
        from HELPERS.safe_messeger import safe_send_message
        safe_send_message(uid, "✖️ لغو شد.")
        return True
    if text.startswith(("http://", "https://", "www.")):
        _clear(uid)
        from HELPERS.safe_messeger import safe_send_message
        safe_send_message(uid, "ℹ️ وسطِ کارِ متغیرها بودی؛ این متن را لینک فرض کردم و "
                               "لغو شد تا فیلمت دانلود شود. اگر واقعاً مقدارِ متغیر بود، "
                               "دوباره از «✏️ ویرایش» شروع کن.")
        return False

    # ۲) وسطِ «چه متغیری؟» ⇒ این متن خودِ نام است
    if action in ("choose_set", "choose_del"):
        name = text.split("=")[0].strip()
        if not name:
            return False
        if action == "choose_del":
            return _ask_delete(uid, name, app)
        _set_state(uid, "set_value", name)
        _ask(uid, "✏️ مقدارِ جدیدِ <code>%s</code> را بفرست (همین‌جا ریپلای کن)." % name)
        return True

    # ۳) وسطِ گرفتنِ مقدار ⇒ این متن مقدار است (اگر «نام=مقدار» بود، هر دو)
    if action == "set_value":
        name = st.get("name") or ""
        value = text
        if "=" in text and text.split("=")[0].strip() == name:
            value = text.split("=", 1)[1]
        _clear(uid)
        if not name:
            return False
        ok, msg = set_variable(name, value)
        from HELPERS.safe_messeger import safe_send_message
        safe_send_message(uid, ("✅ <b>%s</b> ذخیره شد.\n%s" % (name, msg)) if ok
                          else ("❌ %s: %s" % (name, msg)))
        return True

    # ۴) وسطِ افزودن ⇒ انتظار «نام=مقدار»
    if action == "add":
        if "=" not in text:
            from HELPERS.safe_messeger import safe_send_message
            safe_send_message(uid, "به این شکل بفرست: <code>NAME=value</code>")
            return True
        name, value = text.split("=", 1)
        name = name.strip()
        _clear(uid)
        if not name:
            return False
        ok, msg = set_variable(name, value)
        from HELPERS.safe_messeger import safe_send_message
        safe_send_message(uid, ("✅ متغیرِ <b>%s</b> اضافه/به‌روز شد.\n%s" % (name, msg)) if ok
                          else ("❌ %s: %s" % (name, msg)))
        return True

    return False


def _ask_delete(uid, name: str, app) -> bool:
    from HELPERS.safe_messeger import safe_send_message
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    vars_, _src, _w = variables()
    warn = ""
    if name in CRITICAL or name not in vars_:
        warn = ("\n\n⚠️ <b>هشدار:</b> <code>%s</code> از متغیرهای حیاتیِ ربات است؛ "
                "با حذفش ربات ممکن است بالا نیاید." % name) if name in CRITICAL else \
            ("\n\nℹ️ <code>%s</code> در فهرست نبود." % name)
    safe_send_message(uid, "🗑 مطمئنی <code>%s</code> حذف شود؟%s" % (name, warn),
                      reply_markup=InlineKeyboardMarkup([[
                          InlineKeyboardButton("✅ حذف کن", callback_data="vdelok|%s" % name),
                          InlineKeyboardButton("❌ انصراف", callback_data="vclose")]]))
    return True
