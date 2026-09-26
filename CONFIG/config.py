# -*- coding: utf-8 -*-
"""
CONFIG/config.py — کانفیگِ متغیرمحور (Railway / Docker)
Environment-driven configuration for Railway (and any Docker host).

قاعده:
  • هر مقداری که در Variables ست کرده باشید، بر مقدار داخل کد اولویت دارد.
  • اگر متغیری ست نشده باشد، همان مقدار پیش‌فرض `CONFIG/_config.py` برمی‌گردد.
  • برای «خاموش‌کردن» یک مقدار اختیاری (مثل آدرس کوکی) مقدار `off` بگذارید.
  • سه مقدار اجباری هستند: BOT_TOKEN ، API_ID ، API_HASH — اگر ست نباشند،
    برنامه با پیام واضح در لاگ متوقف می‌شود (نه با خطای مبهم وسط کار).

پس نیازی نیست هیچ چیزی داخل این فایل ویرایش شود. برای مقادیر ثابتِ غیرسری
می‌توانید `CONFIG/_config.py` را ویرایش کنید؛ برای سری‌ها فقط Variables.
"""
import os
import sys

from CONFIG import envguard as ev
from CONFIG._config import Config as _Base

# ─────────────────────────────────────────────────────────────────────────────
# مسیر داده‌های پایدار (Volume روی Railway)
# ─────────────────────────────────────────────────────────────────────────────
def _detect_data_dir():
    """پوشهٔ قابل‌نوشتنِ پایدار: DATA_DIR → مسیر Volume ریلیوی → /data."""
    candidates = [
        os.environ.get("DATA_DIR"),
        os.environ.get("RAILWAY_VOLUME_MOUNT_PATH"),
        "/data",
    ]
    for candidate in candidates:
        if candidate and os.path.isdir(candidate) and os.access(candidate, os.W_OK):
            return candidate
    return ""


DATA_DIR = _detect_data_dir()


def _data_path(filename, fallback):
    """فایل‌های نوشتنی را تا حد امکان داخل Volume می‌گذاریم تا با ری‌استارت پاک نشوند."""
    return os.path.join(DATA_DIR, filename) if DATA_DIR else fallback


# ─────────────────────────────────────────────────────────────────────────────
# کوکی‌ها: پیش‌فرض‌های `http://localhost/...` مخصوص داکر قدیمی (کانتینر Caddy) هستند
# و روی Railway معنی ندارند؛ پس تا وقتی خودت آدرس نداده‌ای، خالی/خاموش می‌مانند.
# ─────────────────────────────────────────────────────────────────────────────
def _cookie_url(name):
    value = ev.s(name, getattr(_Base, name, ""))
    if isinstance(value, str) and value.startswith(("http://localhost/", "http://127.0.0.1/")):
        return ""
    return value or ""


class Config(_Base):
    """همهٔ مقادیر _config.py به ارث می‌رسند و در صورت وجود متغیر، بازنویسی می‌شوند."""

    # ── اجباری‌ها: اگر ست نباشند، برنامه با پیام واضح بالا نمی‌آید ────────────
    BOT_TOKEN = ev.require("BOT_TOKEN", "توکن ربات از @BotFather")
    API_ID = ev.require_int("API_ID", "api_id از my.telegram.org")
    API_HASH = ev.require("API_HASH", "api_hash از my.telegram.org")

    # ── هویت ربات ────────────────────────────────────────────────────────────
    BOT_NAME = ev.s("BOT_NAME", _Base.BOT_NAME)
    BOT_NAME_FOR_USERS = ev.s("BOT_NAME_FOR_USERS", _Base.BOT_NAME_FOR_USERS)
    MINIAPP_URL = ev.s("MINIAPP_URL", _Base.MINIAPP_URL)

    # ── دسترسی‌ها ─────────────────────────────────────────────────────────────
    ADMIN = ev.ints("ADMIN", _Base.ADMIN)
    ADMIN_USERNAME = ev.s("ADMIN_USERNAME", _Base.ADMIN_USERNAME)
    ADMIN_GROUP = ev.ints("ADMIN_GROUP", _Base.ADMIN_GROUP)
    ALLOWED_GROUP = ev.ints("ALLOWED_GROUP", _Base.ALLOWED_GROUP)
    ALLOWED_USERS = ev.ints("ALLOWED_USERS", _Base.ALLOWED_USERS)

    # ── کانال‌های لاگ / عضویت اجباری ─────────────────────────────────────────
    LOGS_ID = ev.i("LOGS_ID", _Base.LOGS_ID)
    LOGS_VIDEO_ID = ev.i("LOGS_VIDEO_ID", _Base.LOGS_VIDEO_ID)
    LOGS_NSFW_ID = ev.i("LOGS_NSFW_ID", _Base.LOGS_NSFW_ID)
    LOGS_IMG_ID = ev.i("LOGS_IMG_ID", _Base.LOGS_IMG_ID)
    LOGS_PAID_ID = ev.i("LOGS_PAID_ID", _Base.LOGS_PAID_ID)
    LOG_EXCEPTION = ev.i("LOG_EXCEPTION", _Base.LOG_EXCEPTION)
    SUBSCRIBE_CHANNEL = ev.i("SUBSCRIBE_CHANNEL", _Base.SUBSCRIBE_CHANNEL)
    # کانالِ مقصدِ «کپیِ تمیزِ فیلم» — مثلا -1001234567890 یا @mychannel
    # 0 / خالی = خاموش. ربات باید در آن کانال ادمین (اجازهٔ پست) باشد.
    COPY_CHANNEL_ID = ev.s("COPY_CHANNEL_ID", "")
    # True (پیش‌فرض) = بعد از آپلود، دکمه‌های کانال زیرِ فیلم می‌آید تا خودت انتخاب کنی
    # False = بدون پرسیدن، مستقیم در اولین کانال کپی می‌شود
    COPY_CHANNEL_ASK = ev.b("COPY_CHANNEL_ASK", True)
    SUBSCRIBE_CHANNEL_URL = ev.opt("SUBSCRIBE_CHANNEL_URL", _Base.SUBSCRIBE_CHANNEL_URL)
    CHANNEL_GUARD_SESSION_STRING = ev.opt(
        "CHANNEL_GUARD_SESSION_STRING", _Base.CHANNEL_GUARD_SESSION_STRING
    )
    STAR_RECEIVER = ev.i("STAR_RECEIVER", _Base.STAR_RECEIVER)

    # ── کوکی‌ها ──────────────────────────────────────────────────────────────
    COOKIE_URL = _cookie_url("COOKIE_URL")
    YOUTUBE_COOKIE_URL = _cookie_url("YOUTUBE_COOKIE_URL")
    YOUTUBE_COOKIE_ORDER = ev.s("YOUTUBE_COOKIE_ORDER", _Base.YOUTUBE_COOKIE_ORDER)
    YOUTUBE_COOKIE_TEST_URL = ev.s("YOUTUBE_COOKIE_TEST_URL", _Base.YOUTUBE_COOKIE_TEST_URL)
    INSTAGRAM_COOKIE_URL = _cookie_url("INSTAGRAM_COOKIE_URL")
    TIKTOK_COOKIE_URL = _cookie_url("TIKTOK_COOKIE_URL")
    FACEBOOK_COOKIE_URL = _cookie_url("FACEBOOK_COOKIE_URL")
    TWITTER_COOKIE_URL = _cookie_url("TWITTER_COOKIE_URL")
    VK_COOKIE_URL = _cookie_url("VK_COOKIE_URL")
    COOKIE_FILE_PATH = _data_path("cookie.txt", _Base.COOKIE_FILE_PATH)
    FIREBASE_CACHE_FILE = _data_path("dump.json", _Base.FIREBASE_CACHE_FILE)

    # ── پروکسی (اگر ست نشود، عیناً مثل قبل غیرفعال/پیش‌فرض می‌ماند) ──────────
    PROXY_TYPE = ev.s("PROXY_TYPE", _Base.PROXY_TYPE)
    PROXY_IP = ev.s("PROXY_IP", _Base.PROXY_IP)
    PROXY_PORT = ev.i("PROXY_PORT", _Base.PROXY_PORT)
    PROXY_USER = ev.s("PROXY_USER", _Base.PROXY_USER)
    PROXY_PASSWORD = ev.s("PROXY_PASSWORD", _Base.PROXY_PASSWORD)
    PROXY_2_TYPE = ev.s("PROXY_2_TYPE", _Base.PROXY_2_TYPE)
    PROXY_2_IP = ev.s("PROXY_2_IP", _Base.PROXY_2_IP)
    PROXY_2_PORT = ev.i("PROXY_2_PORT", _Base.PROXY_2_PORT)
    PROXY_2_USER = ev.s("PROXY_2_USER", _Base.PROXY_2_USER)
    PROXY_2_PASSWORD = ev.s("PROXY_2_PASSWORD", _Base.PROXY_2_PASSWORD)
    PROXY_SELECT = ev.s("PROXY_SELECT", _Base.PROXY_SELECT)

    # ── PO Token یوتیوب (bgutil) ────────────────────────────────────────────
    # روی Railway پیش‌فرض «خاموش» است؛ برای روشن‌کردن: YOUTUBE_POT_ENABLED=true
    YOUTUBE_POT_ENABLED = ev.b("YOUTUBE_POT_ENABLED", False)
    YOUTUBE_POT_BASE_URL = ev.s("YOUTUBE_POT_BASE_URL", "http://127.0.0.1:4416")
    YOUTUBE_POT_DISABLE_INNERTUBE = ev.b("YOUTUBE_POT_DISABLE_INNERTUBE", False)

    # ── دیتابیس ──────────────────────────────────────────────────────────────
    USE_FIREBASE = ev.b("USE_FIREBASE", _Base.USE_FIREBASE)
    FIREBASE_CONF = ev.js("FIREBASE_CONF", _Base.FIREBASE_CONF)
    FIREBASE_USER = ev.opt("FIREBASE_USER", _Base.FIREBASE_USER)
    FIREBASE_PASSWORD = ev.opt("FIREBASE_PASSWORD", _Base.FIREBASE_PASSWORD)
    FIREBASE_SERVICE_ACCOUNT = ev.s("FIREBASE_SERVICE_ACCOUNT", getattr(_Base, "FIREBASE_SERVICE_ACCOUNT", ""))
    BOT_DB_PATH = ev.s("BOT_DB_PATH", _Base.BOT_DB_PATH)
    VIDEO_CACHE_DB_PATH = ev.s("VIDEO_CACHE_DB_PATH", _Base.VIDEO_CACHE_DB_PATH)
    PLAYLIST_CACHE_DB_PATH = ev.s("PLAYLIST_CACHE_DB_PATH", _Base.PLAYLIST_CACHE_DB_PATH)
    IMAGE_CACHE_DB_PATH = ev.s("IMAGE_CACHE_DB_PATH", _Base.IMAGE_CACHE_DB_PATH)

    # ── داشبورد وب (روی Railway لازم نیست) ───────────────────────────────────
    DASHBOARD_PORT = ev.i("DASHBOARD_PORT", _Base.DASHBOARD_PORT)
    DASHBOARD_USERNAME = ev.s("DASHBOARD_USERNAME", _Base.DASHBOARD_USERNAME)
    DASHBOARD_PASSWORD = ev.s("DASHBOARD_PASSWORD", _Base.DASHBOARD_PASSWORD)

    # ── لاگ ──────────────────────────────────────────────────────────────────
    VERBOSE_LOGGING = ev.b("VERBOSE_LOGGING", _Base.VERBOSE_LOGGING)
    AUTO_CACHE_RELOAD_ENABLED = ev.b("AUTO_CACHE_RELOAD_ENABLED", _Base.AUTO_CACHE_RELOAD_ENABLED)
    RELOAD_CACHE_EVERY = ev.i("RELOAD_CACHE_EVERY", _Base.RELOAD_CACHE_EVERY)


# ─────────────────────────────────────────────────────────────────────────────
# بررسی مقادیر اجباری (بلافاصله بعد از ساخته‌شدن کلاس)
# ─────────────────────────────────────────────────────────────────────────────
if ev.fail_messages():
    _lines = ["", "=" * 66,
              "❌ پیکربندی ناقص است — این متغیرها در Railway ست نشده‌اند:",
              "❌ Missing required variables:"]
    for _name, _desc in ev.fail_messages():
        _lines.append("   • %s   (%s)" % (_name, _desc or "-"))
    _lines += [
        "=" * 66,
        "👉 Railway → سرویس → Variables → این‌ها را اضافه کنید:",
        "   BOT_TOKEN ، API_ID ، API_HASH",
        "",
        "👉 Add them in Railway → your service → Variables, then redeploy.",
        "=" * 66, "",
    ]
    print("\n".join(_lines))
    raise SystemExit(2)


# کوکی‌های یوتیوب ۱ تا ۱۰ (حتماً باید به‌صورت ویژگی کلاس ثبت شوند)
for _idx in range(1, 11):
    _key = "YOUTUBE_COOKIE_URL_%d" % _idx
    setattr(Config, _key, _cookie_url(_key))


# ─────────────────────────────────────────────────────────────────────────────
# هشدارها + بنر راه‌اندازی (مقدارهای حساس ماسک می‌شوند)
# ─────────────────────────────────────────────────────────────────────────────
def _startup_warnings():
    warns = []
    import re as _re
    if not _re.match(r"^\d{6,}:[A-Za-z0-9_-]{30,}$", str(Config.BOT_TOKEN or "")):
        warns.append("فرمت BOT_TOKEN درست به‌نظر نمی‌رسد (شکل درست: 123456:AA...).")
    if not [i for i in (Config.ADMIN or []) if i and i > 0]:
        warns.append("ADMIN ست نشده — دستورات ادمین کار نمی‌کند (آیدی عددی خودت را بگذار).")
    if not config_has_logs():
        warns.append("LOGS_ID / LOG_EXCEPTION ست نشده‌اند — ارسال لاگ به کانال خطا می‌دهد.")
    if Config.USE_FIREBASE and (not Config.FIREBASE_CONF or "apiKey" not in (Config.FIREBASE_CONF or {})):
        warns.append("USE_FIREBASE=true است ولی FIREBASE_CONF کامل نیست.")
    if not DATA_DIR:
        warns.append("Volume وصل نیست — با هر دیپلوی، کاربران/کوکی‌ها پاک می‌شوند (مسیر /data را Volume کن).")
    return warns


def config_has_logs():
    """آیا کانال لاگ واقعی ست شده (نه مقدار نمونهٔ داخل کد)؟"""
    for key in ("LOGS_ID", "LOG_EXCEPTION"):
        value = getattr(Config, key, 0) or 0
        try:
            value = int(value)
        except (TypeError, ValueError):
            return True
        if value and value != -100111111111111:
            return True
    return False


def _print_banner():
    if os.environ.get("TG_YTDLP_NO_BANNER", "") not in ("", "0", "false", "off"):
        return
    ev.report(
        "tg-ytdlp-bot · پیکربندی (از متغیرهای محیطی)",
        [
            ("BOT_NAME_FOR_USERS", Config.BOT_NAME_FOR_USERS),
            ("ADMIN", ", ".join(str(x) for x in (Config.ADMIN or [])[:3])),
            ("API_ID", Config.API_ID),
            ("BOT_TOKEN", Config.BOT_TOKEN, True),
            ("API_HASH", Config.API_HASH, True),
            ("LOGS_ID", Config.LOGS_ID if config_has_logs() else ""),
            ("SUBSCRIBE_CHANNEL", Config.SUBSCRIBE_CHANNEL),
            ("USE_FIREBASE", Config.USE_FIREBASE),
            ("YOUTUBE_POT_ENABLED", Config.YOUTUBE_POT_ENABLED),
            ("PROXY", Config.PROXY_IP if ev.has("PROXY_IP") else ""),
            ("DATA_DIR", DATA_DIR or "(بدون Volume)"),
            ("TZ", os.environ.get("TZ", "(سیستم)")),
        ],
        warnings=_startup_warnings(),
    )


_print_banner()
