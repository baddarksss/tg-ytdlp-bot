# -*- coding: utf-8 -*-
"""فایل → لینکِ مستقیم (با انقضای خودکار و حذفِ دستی).

کارِ این ماژول:
  • فایلِ کاربر (فیلم/سند/صوت/عکس) را روی سرویس ذخیره می‌کند و یک **لینک مستقیم**
    می‌دهد: ``https://<دامنه>/d/<token>/<name>``
  • لینک بعد از زمانِ انتخابی (۳۰ دقیقه / ۱ ساعت / ۶ ساعت / ۲۴ ساعت) **منقضی** می‌شود
    و فایل خودکار پاک می‌شود؛ حذفِ دستی هم دارد.
  • یک وب‌سرورِ کوچکِ aiohttp داخلِ همین سرویس بالا می‌آید (روی ``PORT`` یا 8080)
    و فایل را با پشتیبانیِ Range (برای دانلود‌منیجر و پخش‌کننده) می‌فرستد.

نکته: لینک عمومی است — هر کس لینک را داشته باشد می‌تواند دانلود کند. برای همین
توکن تصادفی است و زمانِ انقضا اجباری.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import shutil
import threading
import time
import urllib.parse
import urllib.request

from CONFIG.config import Config
from HELPERS import filelink_routes as fr
from HELPERS.logger import logger

# ⚠️ f48: Cloudflare جلوی User-Agent پیش‌فرضِ python-urllib را می‌گیرد
#    (error code: 1010 ⇒ HTTP 403 Forbidden) — حتی با توکنِ درست.
#    با یک User-Agent معمولی، همان توکن بدونِ مشکل کار می‌کند.
RAILWAY_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


# ─────────────────────────── تنظیمات ───────────────────────────
TTL_OPTIONS = [("30m", 1800, "۳۰ دقیقه"), ("1h", 3600, "۱ ساعت"),
               ("6h", 21600, "۶ ساعت"), ("24h", 86400, "۲۴ ساعت"),
               ("72h", 259200, "۳ روز"), ("168h", 604800, "۷ روز")]
TTL = {k: sec for k, sec, _ in TTL_OPTIONS}
# سقفِ فایلِ قابلِ دریافت از تلگرام:
#   این ربات با pyrogram روی پروتکلِ MTProto کار می‌کند (نه HTTP Bot API)، پس
#   محدودیتِ ۲۰ مگابایتیِ Bot API اینجا وجود ندارد و فایل‌های بزرگ را مستقیم
#   می‌شود از تلگرام گرفت. سقفِ خودِ تلگرام برای ربات = ۲ گیگابایت.
MAX_TG_DOWNLOAD = 2 * 1024 * 1024 * 1024 - 8192
# مهلتِ دانلودِ فایل‌های بزرگ (ثانیه)
DOWNLOAD_TIMEOUT = 3600

_LOCK = threading.RLock()
_SERVER_STARTED = False
_SWEEPER_STARTED = False
_URL_CACHE = {"base": "", "at": 0.0}


def links_dir() -> str:
    """پوشهٔ لینک‌ها (مشترک با وب‌سرورِ سلامت — همان پوشهٔ داده)."""
    return fr.links_dir()


def _store_path() -> str:
    return fr.store_path()


def _load() -> dict:
    return fr.load()


def _save(recs: dict):
    try:
        tmp = _store_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(recs, f, ensure_ascii=False)
        os.replace(tmp, _store_path())
    except Exception as e:
        logger.error(f"filelink: save failed: {e}")


# ─────────────────────────── کمکی‌ها ───────────────────────────

_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _fa(text) -> str:
    """عددها را فارسی نشان می‌دهد (خوش‌خوان‌تر در چت)."""
    return str(text).translate(_FA_DIGITS)


def human(sec) -> str:
    sec = max(0, int(sec or 0))
    if sec < 120:
        return _fa("%d ثانیه" % sec)
    if sec < 3600:
        return _fa("%d دقیقه" % round(sec / 60))
    if sec < 86400:
        h, m = divmod(int(sec // 60), 60)
        return _fa("%d ساعت%s" % (h, (" و %d دقیقه" % m) if m and h < 6 else ""))
    return _fa("%d روز" % round(sec / 86400))


def human_size(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return _fa("%.1f %s" % (n, unit)) if unit != "B" else _fa("%d B" % n)
        n /= 1024.0


def _normalize_base(url) -> str:
    """پاک‌سازیِ آدرسِ پایهٔ لینک‌ها: فاصله/خطِ جدید/اسکیمِ جاافتاده.

    نمونهٔ واقعیِ دیپلویِ زنده: ``LINK_BASE_URL=dgdgdg.embezzle3131.dpdns.org``
    (بدونِ ``https://``) ⇒ لینک‌های فایل **بی‌اسکیم** ساخته می‌شدند؛ در تلگرام
    کلیک‌پذیرِ یکدست نبودند و وقتی کاربر همان متن را به ربات برمی‌گرداند،
    استخراجِ لینک به حدسِ «لینکِ بدونِ اسکیم» می‌افتاد و گاهی url=None به
    yt-dlp می‌رسید (سقوطِ «'NoneType' object has no attribute 'lower'»).
    """
    u = "".join(str(url or "").split())             # همهٔ فاصله/خطِ جدید حذف شود
    u = u.strip().strip('"').strip("'").strip()
    if not u:
        return ""
    if u.startswith("//"):  # protocol-relative
        u = "https:" + u
    if "://" in u:
        scheme, rest = u.split("://", 1)
        if scheme.lower() not in ("http", "https"):  # ftp:// و … ⇒ همان https
            u = "https://" + rest.lstrip("/")
    else:
        u = "https://" + u.lstrip("/")
    return u.rstrip("/")


def base_url(refresh: bool = False) -> str:
    """دامنهٔ عمومیِ سرویس (برای ساختنِ لینک)."""
    if not refresh and _URL_CACHE["base"] and time.time() - _URL_CACHE["at"] < 300:
        return _URL_CACHE["base"]
    url = (str(getattr(Config, "LINK_BASE_URL", "") or "").strip()
           or os.environ.get("LINK_BASE_URL", "").strip())
    if not url:
        dom = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
        if dom:
            url = dom
    if not url:
        url = _domain_from_api()
    url = _normalize_base(url)
    if not url and base_url:
        pass
    _URL_CACHE["base"] = url
    _URL_CACHE["at"] = time.time()
    return url


def _domain_from_api() -> str:
    """اگر دامنهٔ سرویس از طریق API ریلوی پیدا شود (خودِ ربات می‌سازد)."""
    tok = (str(getattr(Config, "RAILWAY_API_TOKEN", "") or "")
           or os.environ.get("RAILWAY_API_TOKEN", "")).strip()
    sid = (str(getattr(Config, "RAILWAY_SERVICE_ID", "") or "")
           or os.environ.get("RAILWAY_SERVICE_ID", "")).strip()
    if not (tok and sid):
        return ""
    q = ('query($s:String!){service(id:$s){serviceInstances{edges{node{domains{'
         'serviceDomains{domain}}}}}}}')
    try:
        req = urllib.request.Request(
            "https://backboard.railway.com/graphql/v2",
            data=json.dumps({"query": q, "variables": {"s": sid}}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok,
                     "User-Agent": RAILWAY_UA, "Accept": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8", "replace") or "{}")
        for edge in (((data.get("data") or {}).get("service") or {})
                     .get("serviceInstances", {}).get("edges") or []):
            for d in ((edge.get("node") or {}).get("domains", {}) or {}).get("serviceDomains") or []:
                dom = (d or {}).get("domain")
                if dom:
                    return "https://" + str(dom).strip()
    except Exception as e:
        logger.warning(f"filelink: domain lookup failed: {e}")
    return ""


def link_url(rec: dict) -> str:
    base = base_url()
    if not base:
        return ""
    fname = urllib.parse.quote(rec.get("name") or "file")
    return "%s/d/%s/%s" % (base, rec.get("token"), fname)


def resolve(token: str):
    return fr.resolve(token)


def active() -> list:
    with _LOCK:
        recs = _load()
    out = [r for r in recs.values()
           if os.path.exists(r.get("path") or "") and (not r.get("exp") or time.time() < r["exp"])]
    return sorted(out, key=lambda r: r.get("created") or 0, reverse=True)


def default_ttl() -> str:
    """TTLِ پیش‌فرضِ لینک (env: LINK_DEFAULT_TTL — پیش‌فرض ۶ ساعت).

    لینکِ ۳۰ دقیقه‌ای برای «فایل بده و بعداً خودت دانلود کن» زود می‌پرید؛
    پیش‌فرض ۶ ساعت است (خواستِ کارفرما) و گزینه‌های ۲۴ ساعت/۳ روز/۷ روز هم هست.
    نکته: فایل تا FILELINK_KEEP_HOURS بعد از انقضا روی دیسک می‌ماند تا خودِ ربات
    بتواند همان لینک را دوباره دانلود کند.
    """
    try:
        val = (os.environ.get("LINK_DEFAULT_TTL") or "6h").strip()
    except Exception:
        val = "6h"
    return val if val in TTL else "6h"


class DiskFullError(RuntimeError):
    """جایِ کافی روی دیسکِ لینک‌ها نیست (والیومِ کوچکِ ریلوی)."""


def ensure_space(need_bytes: int) -> None:
    """قبلِ از ساختنِ لینک، جایِ لازم را آزاد می‌کند؛ نشد ⇒ DiskFullError.

    ترتیب: اول جاروی عادی (منقضی‌های گذشته از مهلت)؛ اگر جا نشد، فایل‌های
    منقضیِ نگه‌داشته‌شده از قدیمی‌ترین؛ اگر باز هم نشد ⇒ خطای روشن به کاربر.
    """
    need_mb = (need_bytes or 0) / (1024.0 * 1024.0)
    want = fr.min_free_mb() + need_mb
    if fr.free_mb() >= want:
        return
    with contextlib.suppress(Exception):
        fr.sweep_files()
    if fr.free_mb() >= want:
        return
    # جارو فقط «گذشته از مهلتِ نگه‌داری» و (در تنگیِ جا) منقضی‌ها را می‌برد؛
    # اینجا یک بار دیگر با آستانهٔ بزرگ‌تر تلاش می‌کنیم.
    try:
        os.environ["FILELINK_MIN_FREE_MB"] = str(int(want) + 1)
        with contextlib.suppress(Exception):
            fr.sweep_files()
    finally:
        with contextlib.suppress(Exception):
            os.environ.pop("FILELINK_MIN_FREE_MB", None)
    free = fr.free_mb()
    if free < want:
        raise DiskFullError(
            "جایِ دیسکِ لینک‌ها کافی نیست (آزاد: %s، لازم: %s). "
            "چند لینکِ کهنه را با «🔗 همهٔ لینک‌ها → 🧹 پاک‌کردنِ همه» پاک کن "
            "یا والیومِ سرور را بزرگ‌تر کن."
            % (fr_human(free), fr_human(want)))


def fr_human(mb: float) -> str:
    if mb >= 1024:
        return "%.1fGB" % (mb / 1024.0)
    return "%.0fMB" % mb


def add_file(src: str, name: str, chat_id: int, ttl: str, message_id: int = 0) -> dict:
    """فایل را به پوشهٔ لینک‌ها می‌برد و رکوردِ لینک می‌سازد."""
    ttl = ttl or default_ttl()
    with contextlib.suppress(Exception):
        ensure_space(os.path.getsize(src))
    token = secrets.token_urlsafe(9)
    safe_name = os.path.basename(name or "file") or "file"
    dest = os.path.join(links_dir(), "%s_%s" % (token, safe_name))
    shutil.move(src, dest)
    rec = {"token": token, "name": safe_name, "path": dest,
           "size": os.path.getsize(dest), "chat_id": int(chat_id), "message_id": int(message_id or 0),
           "created": time.time(),
           "exp": (time.time() + TTL.get(ttl, TTL.get(default_ttl(), 86400))),
           "ttl": ttl}
    with _LOCK:
        recs = _load()
        recs[token] = rec
        _save(recs)
    logger.info(f"filelink: created {token} ({safe_name}, {rec['size']}B, ttl={ttl})")
    return rec


def delete(token: str, notify: bool = True) -> bool:
    """فایل + رکورد را پاک می‌کند."""
    with _LOCK:
        recs = _load()
        rec = recs.pop(token, None)
        if rec:
            _save(recs)
    if not rec:
        return False
    with contextlib.suppress(Exception):
        os.remove(rec.get("path") or "")
    logger.info(f"filelink: deleted {token}")
    if notify:
        safe_notify(rec, "🗑 فایل و لینکش پاک شد.")
    return True


def extend(token: str, ttl: str) -> dict:
    with _LOCK:
        recs = _load()
        rec = recs.get(token)
        if not rec:
            return None
        rec["exp"] = time.time() + TTL.get(ttl, TTL["30m"])
        rec["ttl"] = ttl
        _save(recs)
    return rec


def sweep() -> list:
    """پاک‌کردنِ فایل‌های منقضی + اطلاع به صاحبِ لینک."""
    expired = fr.sweep_files()
    for rec in expired:
        logger.info(f"filelink: expired {rec.get('token')} ({rec.get('name')})")
        safe_notify(rec, "⏱ زمانِ لینکِ «%s» تمام شد و فایلش پاک شد." % (rec.get("name") or ""))
    return expired


def start_sweeper(interval: int = 30):
    """تردِ پاک‌سازیِ دوره‌ای در پروسهٔ ربات (حذفِ فایلِ منقضی + پیام به کاربر)."""
    global _SWEEPER_STARTED
    if _SWEEPER_STARTED:
        return
    _SWEEPER_STARTED = True

    def _loop():
        while True:
            with contextlib.suppress(Exception):
                sweep()
            time.sleep(interval)

    threading.Thread(target=_loop, name="filelink-sweeper", daemon=True).start()
    logger.info("filelink: sweeper started")


def clear_all() -> int:
    recs = active()
    n = 0
    for r in recs:
        if delete(r["token"], notify=False):
            n += 1
    return n


def storage_used() -> tuple:
    n, total = 0, 0
    with contextlib.suppress(Exception):
        for f in os.listdir(links_dir()):
            p = os.path.join(links_dir(), f)
            if os.path.isfile(p) and f != "links.json":
                n += 1
                total += os.path.getsize(p)
    return n, total


def safe_notify(rec: dict, text: str):
    """پیامِ اطلاع‌رسانی به صاحبِ لینک (از تردِ وب‌سرور)."""
    chat_id = rec.get("chat_id")
    if not chat_id:
        return
    try:
        from HELPERS.app_instance import get_app
        from HELPERS.safe_messeger import safe_send_message
        app = get_app()
        if app is None:
            return
        safe_send_message(int(chat_id), text)
    except Exception as e:
        logger.warning(f"filelink: notify failed: {e}")


# ─────────────────────────── وب‌سرور ───────────────────────────

def _build_app():
    from aiohttp import web

    async def download(request):
        token = request.match_info.get("token", "")
        rec = resolve(token)
        if not rec:
            return web.Response(status=404, text="لینک منقضی شده یا وجود ندارد.")
        path = rec["path"]
        try:
            resp = web.FileResponse(path)
        except Exception:
            return web.Response(status=404, text="فایل پیدا نشد.")
        resp.headers["Content-Disposition"] = (
            'attachment; filename="%s"' % urllib.parse.quote(os.path.basename(path)))
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Accept-Ranges"] = "bytes"
        return resp

    async def head_download(request):
        token = request.match_info.get("token", "")
        rec = resolve(token)
        if not rec:
            return web.Response(status=404, text="لینک منقضی شده یا وجود ندارد.")
        return web.Response(status=200, headers={
            "Content-Length": str(rec.get("size") or 0),
            "Accept-Ranges": "bytes",
            "Content-Disposition": 'attachment; filename="%s"'
                                   % urllib.parse.quote(rec.get("name") or "file")})

    async def root(request):
        n, total = storage_used()
        return web.Response(text="file-link server ok — %d فایل فعال (%s)"
                                 % (n, human_size(total)),
                            content_type="text/plain", charset="utf-8")

    async def sweeper():
        while True:
            with contextlib.suppress(Exception):
                await asyncio.get_event_loop().run_in_executor(None, sweep)
            await asyncio.sleep(30)

    app = web.Application()
    app.router.add_get("/d/{token}", download)
    app.router.add_get("/d/{token}/{name}", download)
    app.router.add_head("/d/{token}", head_download)
    app.router.add_head("/d/{token}/{name}", head_download)
    app.router.add_get("/", root)

    # v82: the startup hook must NOT return the sweeper task - aiohttp
    # awaits whatever a startup signal returns, and the sweeper loops
    # forever => app.startup() never finished and the server never came up.
    # Fire-and-forget instead (the thread based sweeper does the same job).
    async def _start_sweeper(_app):
        asyncio.ensure_future(sweeper())

    app.on_startup.append(_start_sweeper)
    return app


def start_server() -> bool:
    """سرورِ دانلود در پروسهٔ ربات — **فقط اگر پورت آزاد باشد**.

    روی Railway، وب‌سرورِ سلامت (``HELPERS/railway_health.py`` که از entrypoint بالا
    می‌آید) روی پورتِ عمومی گوش می‌دهد و همان روت‌های ``/d/...`` را سرو می‌کند
    (به HELPERS/filelink_routes نگاه کن). پس این تابع فقط نگهبانِ حالتِ دوم است:
    اگر پورت آزاد بود، خودش سرور را بالا می‌آورد؛ اگر گرفته بود، بی‌صدا برمی‌گردد.
    """
    global _SERVER_STARTED
    if _SERVER_STARTED:
        return True
    port = int(os.environ.get("PORT") or getattr(Config, "LINK_PORT", 0) or 8080)

    def _run():
        from aiohttp import web
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # v82: web.run_app() does NOT work in a thread - it installs a
            # signal handler and calls set_wakeup_fd(), which is only allowed
            # in the main thread ("set_wakeup_fd only works in main thread of
            # the main interpreter"). The server therefore never started and
            # every file link was dead. AppRunner + TCPSite has no such
            # requirement and works fine in a background thread.
            _app = _build_app()
            runner = web.AppRunner(_app, access_log=None)
            loop.run_until_complete(runner.setup())
            site = web.TCPSite(runner, host="0.0.0.0", port=port)
            loop.run_until_complete(site.start())
            logger.info(f"filelink: server listening on 0.0.0.0:{port}")
            loop.run_forever()
        except OSError as e:
            logger.info(f"filelink: پورت {port} در اختیارِ سرورِ سلامت است ({e}) — "
                        f"همان روت‌ها را سرو می‌کند.")
        except Exception as e:
            logger.error(f"filelink: server failed on port {port}: {e}")

    try:
        threading.Thread(target=_run, name="filelink-server", daemon=True).start()
        _SERVER_STARTED = True
        logger.info(f"filelink: server starting on 0.0.0.0:{port}")
    except Exception as e:
        logger.error(f"filelink: cannot start server: {e}")
    start_sweeper()
    return True
