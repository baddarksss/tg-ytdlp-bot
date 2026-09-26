# -*- coding: utf-8 -*-
"""مدیریتِ کانال‌های مقصدِ «کپیِ فیلم» — چند‌کاناله + افزودنِ دستی از داخلِ ربات.

• کانال‌ها دو منبع دارند:
    1) متغیرِ محیطی COPY_CHANNEL_ID  (می‌تواند چند مقدار با کاما باشد) — «قفل‌شده»
    2) فایلِ channels.json در پوشهٔ داده — هر کانالی که خودت داخلِ ربات اضافه کنی
• «ربات کجا ادمین است؟» با get_chat_member(chat, me) چک می‌شود و نتیجه ۱۰ دقیقه کش می‌شود.
• این ماژول عمداً هیچ ایمپورتِ pyrogram در سطحِ ماژول ندارد تا در تست هم قابلِ بارگذاری باشد.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

from CONFIG.config import Config
from HELPERS.logger import logger

_LOCK = threading.Lock()
_FILE_NAME = "channels.json"

# کشِ «ربات ادمین است؟»  →  key -> (ok, title, err, at)
_ADMIN_TTL = 600
_ADMIN_CACHE: dict = {}

# نتایجی که «کانال نیست» حساب می‌شوند (مقدارِ نمونه‌ای داخلِ .env.example و ...)
_JUNK = {"", "0", "none", "off", "false", "no", "-1001234567890"}

_TME_RE = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me)/(.+)$", re.I)


# ─────────────────────────── مسیرِ فایل و کمک‌کارها ───────────────────────────

def store_path() -> str:
    base = str(getattr(Config, "DATA_DIR", "") or ".").strip() or "."
    return os.path.join(base, _FILE_NAME)


def _valid_int(value: int) -> bool:
    # آیدیِ کانال همیشه منفی و بزرگ است (-100…)
    return value < -100


def normalize(value) -> tuple:
    """ورودیِ کاربر ⇒ مقدارِ استاندارد.  برمی‌گرداند: (مقدار، خطا)

    • ``-1001263727544``  ⇒  int
    • ``@mychannel`` / ``t.me/mychannel`` / ``https://t.me/mychannel``  ⇒  "@mychannel"
    """
    raw = str(value or "").strip()
    if not raw:
        return None, "خالی است"
    if raw.lower() in _JUNK:
        return None, ("این آیدیِ نمونه‌ایِ داخلِ مستنداتِ سورس است، نه کانالِ واقعی — "
                      "آیدیِ واقعی (-100…) یا @username را بفرست")
    m = _TME_RE.match(raw)
    if m:
        tail = m.group(1).strip().strip("/")
        if tail.startswith("+") or tail.lower().startswith("joinchat"):
            return None, ("لینکِ دعوت قابلِ‌استفاده نیست — آیدی عددی (-100…) یا "
                          "@username بفرست")
        raw = "@" + tail if not tail.startswith("@") else tail
    if raw.startswith("@"):
        name = raw[1:].strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{4,64}", name):
            return None, "نامِ کاربریِ کانال معتبر نیست"
        return "@" + name, ""
    txt = raw.replace(" ", "")
    try:
        num = int(txt)
    except ValueError:
        return None, "باید آیدیِ عددی (-100…) یا @username باشد"
    if not _valid_int(num):
        return None, ("آیدیِ کانال باید با -100 شروع شود (گروهی که آیدی به‌شکل "
                      "-100… دارد هم قبول است)")
    return num, ""


def key_of(value) -> str:
    """کلیدِ یکتا برای دکمه‌ها و ذخیره‌سازی (رشته)."""
    n, err = normalize(value)
    if err:
        return ""
    return str(n)


def env_keys() -> list:
    """کانال‌های آمده از متغیرِ محیطی (می‌تواند چندتا با کاما باشد)."""
    raw = str(getattr(Config, "COPY_CHANNEL_ID", "") or "")
    out = []
    for part in re.split(r"[,\s]+", raw):
        if not part.strip():
            continue
        if part.strip().lower() in _JUNK:
            continue
        k = key_of(part)
        if k and k not in out:
            out.append(k)
    return out


def ask_enabled() -> bool:
    """True = بعد از آپلود، دکمه‌های کانال زیرِ فیلم بیاید (خواستهٔ کاربر).

    با COPY_CHANNEL_ASK=0 به حالتِ خودکار (کپیِ بی‌سؤال در اولین کانال) برمی‌گردد.
    """
    return bool(getattr(Config, "COPY_CHANNEL_ASK", True))


# ─────────────────────────── خواندن/نوشتنِ فایل ───────────────────────────

def _read_file() -> list:
    path = store_path()
    try:
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"channel_store: cannot read {path}: {e}")
        return []
    items = data.get("channels") if isinstance(data, dict) else data
    out = []
    for it in (items or []):
        if isinstance(it, dict):
            k = key_of(it.get("id"))
            if k:
                out.append({"id": (int(k) if not k.startswith("@") else k),
                            "title": str(it.get("title") or "").strip()})
    return out


def _write_file(items: list) -> bool:
    path = store_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"channels": items}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except Exception as e:
        logger.error(f"channel_store: cannot write {path}: {e}")
        return False


# ─────────────────────────── فهرستِ کانال‌ها ───────────────────────────

def _title_for(key: str) -> str:
    if key.startswith("@"):
        return key
    for cached_key, (ok, title, _err, _at) in _ADMIN_CACHE.items():
        if cached_key == key and title:
            return title
    return key


def channels(refresh_titles: bool = False) -> list:
    """فهرستِ کانال‌های مقصد.

    هر عضو: {"key":…, "id":…, "title":…, "locked":bool}
      locked=True ⇒ از متغیرِ محیطی آمده و از داخلِ ربات پاک نمی‌شود.
    """
    env = env_keys()
    out, seen = [], set()
    for k in env:
        out.append({"key": k, "id": (int(k) if not k.startswith("@") else k),
                    "title": _title_for(k), "locked": True})
        seen.add(k)
    for it in _read_file():
        k = key_of(it["id"])
        if not k or k in seen:
            continue
        out.append({"key": k, "id": it["id"], "title": it.get("title") or _title_for(k),
                    "locked": False})
        seen.add(k)
    return out


def get(key: str) -> dict:
    for ch in channels():
        if ch["key"] == key:
            return ch
    return {}


def add_channel(key: str, title: str = "") -> tuple:
    """افزودنِ کانال (فقط آن‌هایی که از داخلِ ربات اضافه می‌شوند در فایل می‌روند)."""
    k = key_of(key)
    if not k:
        return False, "مقدارِ کانال معتبر نیست"
    existing = get(k)
    if existing:
        title = existing.get("title") or title
        if existing.get("locked"):
            return True, "این کانال از قبل در فهرست است (از Variables می‌آید)"
        return True, "این کانال از قبل در فهرست است"
    with _LOCK:
        items = [it for it in _read_file() if key_of(it["id"]) != k]
        items.append({"id": (int(k) if not k.startswith("@") else k),
                      "title": (title or "").strip()})
        ok = _write_file(items)
    if not ok:
        return False, "ذخیره‌سازیِ فهرست ناموفق بود"
    return True, "افزوده شد"


def remove_channel(key: str) -> tuple:
    k = key_of(key)
    if not k:
        return False, "مقدارِ کانال معتبر نیست"
    if k in env_keys():
        return False, "این کانال از Variables می‌آید؛ از همان‌جا حذفش کن"
    with _LOCK:
        items = [it for it in _read_file() if key_of(it["id"]) != k]
        ok = _write_file(items)
    return (ok, "حذف شد" if ok else "حذف ناموفق بود")


# ─────────────────────────── بررسیِ ادمین‌بودنِ ربات ───────────────────────────

def bot_can_post(app, key: str, force: bool = False) -> tuple:
    """آیا ربات در این کانال می‌تواند پست بگذارد؟  ⇒ (ok, title, err)"""
    k = key_of(key) or str(key)
    now = time.time()
    hit = _ADMIN_CACHE.get(k)
    if hit and not force and (now - hit[3]) < _ADMIN_TTL:
        return hit[0], hit[1], hit[2]
    ok, title, err = False, "", ""
    target = int(k) if not k.startswith("@") else k
    try:
        chat = app.get_chat(target)
        title = str(getattr(chat, "title", "") or "").strip()
        username = str(getattr(chat, "username", "") or "").strip()
        if not title:
            title = ("@" + username) if username else str(target)
        me = app.get_me()
        member = app.get_chat_member(target, getattr(me, "id", 0) or "me")
        status = str(getattr(member, "status", "")).lower()
        can_post = getattr(member, "can_post_messages", None)
        is_admin = ("owner" in status) or ("creator" in status) or ("administrator" in status)
        if not is_admin:
            err = "ربات در این کانال ادمین نیست"
        elif can_post is False:
            err = "ادمین است ولی اجازهٔ «ارسال پیام» ندارد"
        else:
            ok = True
    except Exception as e:
        err = str(e)[:140] or "کانال پیدا نشد"
    _ADMIN_CACHE[k] = (ok, title, err, now)
    return ok, title, err


def refresh_titles(app, keys=None) -> list:
    """عنوان/دسترسیِ همهٔ کانال‌ها را تازه می‌کند ⇒ فهرستِ به‌روز."""
    for ch in channels():
        if keys and ch["key"] not in keys:
            continue
        bot_can_post(app, ch["key"], force=True)
    return channels()


# ─────────────────────────── دکمه‌ها ───────────────────────────

def _kb(rows):
    from pyrogram.types import InlineKeyboardMarkup
    return InlineKeyboardMarkup(rows)


def film_keyboard(copied=None) -> object:
    """دکمه‌های زیرِ فیلم: یک دکمه برای هر کانال + «افزودن کانال» + بستن."""
    from pyrogram.types import InlineKeyboardButton
    copied = set(copied or ())
    rows = []
    for ch in channels():
        mark = "✅ " if ch["key"] in copied else "📢 "
        rows.append([InlineKeyboardButton(mark + (ch["title"] or ch["key"])[:48],
                                          callback_data="chcopy|" + ch["key"])])
    rows.append([InlineKeyboardButton("➕ افزودن کانال", callback_data="chadd"),
                 InlineKeyboardButton("✖️ بستن", callback_data="chhide")])
    return _kb(rows)


def panel(app, copied=None) -> tuple:
    """متن و دکمه‌های پنلِ /channels  ⇒ (text, keyboard)"""
    from pyrogram.types import InlineKeyboardButton
    chs = channels()
    lines = ["📢 <b>کانال‌های مقصد</b>",
             "بعد از هر آپلود، دکمه‌های زیرِ فیلم می‌آید؛ هر کدام را بزنی فیلم به همان کانال می‌رود.",
             ""]
    rows = []
    if not chs:
        lines.append("هنوز هیچ کانالی نداری — با دکمهٔ پایین اضافه کن.")
    for ch in chs:
        ok, title, err = bot_can_post(app, ch["key"])
        name = title or ch["title"] or ch["key"]
        state = "✅ ربات ادمین است" if ok else ("⚠️ " + (err or "ربات ادمین نیست"))
        lines.append("• <b>%s</b> — <code>%s</code>\n   %s" % (name, ch["key"], state))
        row = [InlineKeyboardButton("🗑 حذف" if not ch["locked"] else "🔒 از Variables",
                                    callback_data=("chdel|" + ch["key"]) if not ch["locked"]
                                    else "chlock")]
        rows.append(row)
    lines.append("")
    lines.append("ℹ️ کانال‌هایی که با 🔒 مشخص‌اند از متغیرِ محیطی <code>COPY_CHANNEL_ID</code> "
                 "می‌آیند و فقط از Railway → Variables قابلِ حذف‌اند.")
    rows.append([InlineKeyboardButton("➕ افزودن کانال", callback_data="chadd"),
                 InlineKeyboardButton("🔄 بررسیِ ادمین‌بودن", callback_data="chrefresh")])
    return "\n".join(lines), _kb(rows)
