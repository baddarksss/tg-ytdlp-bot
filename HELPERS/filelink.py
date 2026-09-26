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
from HELPERS.logger import logger

# ─────────────────────────── تنظیمات ───────────────────────────
TTL_OPTIONS = [("30m", 1800, "۳۰ دقیقه"), ("1h", 3600, "۱ ساعت"),
               ("6h", 21600, "۶ ساعت"), ("24h", 86400, "۲۴ ساعت")]
TTL = {k: sec for k, sec, _ in TTL_OPTIONS}
# محدودیتِ دانلودِ فایل از تلگرام با Bot API معمولی: ۲۰ مگابایت
MAX_TG_DOWNLOAD = 20 * 1024 * 1024 - 8192

_LOCK = threading.RLock()
_SERVER_STARTED = False
_URL_CACHE = {"base": "", "at": 0.0}


def links_dir() -> str:
    d = os.path.join(str(getattr(Config, "DATA_DIR", "/data") or "/data"), "filelinks")
    with contextlib.suppress(Exception):
        os.makedirs(d, exist_ok=True)
    return d


def _store_path() -> str:
    return os.path.join(links_dir(), "links.json")


def _load() -> dict:
    try:
        with open(_store_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


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


def base_url(refresh: bool = False) -> str:
    """دامنهٔ عمومیِ سرویس (برای ساختنِ لینک)."""
    if not refresh and _URL_CACHE["base"] and time.time() - _URL_CACHE["at"] < 300:
        return _URL_CACHE["base"]
    url = (str(getattr(Config, "LINK_BASE_URL", "") or "").strip()
           or os.environ.get("LINK_BASE_URL", "").strip())
    if not url:
        dom = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
        if dom:
            url = dom if dom.startswith("http") else "https://" + dom
    if not url:
        url = _domain_from_api()
    url = url.rstrip("/")
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
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok},
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
    with _LOCK:
        recs = _load()
    rec = recs.get(token)
    if not rec:
        return None
    if rec.get("exp") and time.time() > rec["exp"]:
        return None
    if not os.path.exists(rec.get("path") or ""):
        return None
    return rec


def active() -> list:
    with _LOCK:
        recs = _load()
    out = [r for r in recs.values()
           if os.path.exists(r.get("path") or "") and (not r.get("exp") or time.time() < r["exp"])]
    return sorted(out, key=lambda r: r.get("created") or 0, reverse=True)


def add_file(src: str, name: str, chat_id: int, ttl: str, message_id: int = 0) -> dict:
    """فایل را به پوشهٔ لینک‌ها می‌برد و رکوردِ لینک می‌سازد."""
    token = secrets.token_urlsafe(9)
    safe_name = os.path.basename(name or "file") or "file"
    dest = os.path.join(links_dir(), "%s_%s" % (token, safe_name))
    shutil.move(src, dest)
    rec = {"token": token, "name": safe_name, "path": dest,
           "size": os.path.getsize(dest), "chat_id": int(chat_id), "message_id": int(message_id or 0),
           "created": time.time(), "exp": (time.time() + TTL.get(ttl, 0)) if TTL.get(ttl, 0) else 0,
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
    """پاک‌کردنِ فایل‌های منقضی. لیستِ رکوردهای پاک‌شده را برمی‌گرداند."""
    now = time.time()
    expired = []
    with _LOCK:
        recs = _load()
        for token, rec in list(recs.items()):
            if rec.get("exp") and now > rec["exp"]:
                expired.append(rec)
                recs.pop(token, None)
        if expired:
            _save(recs)
    for rec in expired:
        with contextlib.suppress(Exception):
            os.remove(rec.get("path") or "")
        logger.info(f"filelink: expired {rec.get('token')} ({rec.get('name')})")
        safe_notify(rec, "⏱ زمانِ لینکِ «%s» تمام شد و فایلش پاک شد." % (rec.get("name") or ""))
    return expired


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
    app.on_startup.append(lambda a: asyncio.ensure_future(sweeper()))
    return app


def start_server() -> bool:
    """سرور را یک‌بار در یک ترد جدا بالا می‌آورد. True = بالا آمد/بالاست."""
    global _SERVER_STARTED
    if _SERVER_STARTED:
        return True
    port = int(os.environ.get("PORT") or getattr(Config, "LINK_PORT", 0) or 8080)

    def _run():
        from aiohttp import web
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            web.run_app(_build_app(), host="0.0.0.0", port=port, loop=loop,
                        print=None, access_log=None)
        except Exception as e:
            logger.error(f"filelink: server failed on port {port}: {e}")

    try:
        threading.Thread(target=_run, name="filelink-server", daemon=True).start()
        _SERVER_STARTED = True
        logger.info(f"filelink: server starting on 0.0.0.0:{port}")
        return True
    except Exception as e:
        logger.error(f"filelink: cannot start server: {e}")
        return False
