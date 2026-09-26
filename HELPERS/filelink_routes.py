# -*- coding: utf-8 -*-
"""روت‌های دانلودِ لینکِ مستقیم — بدونِ وابستگی به CONFIG.

چرا جدا: این روت‌ها هم داخلِ پروسهٔ ربات ثبت می‌شوند و هم داخلِ وب‌سرورِ سلامت
(``HELPERS/railway_health.py`` که از ``scripts/docker-entrypoint.sh`` بالا می‌آید و
روی پورتِ عمومیِ Railway گوش می‌دهد). آن پروسه فقط ``os``/``json``/``aiohttp``
می‌شناسد، پس این ماژول هم باید سبک و بدونِ CONFIG باشد تا سرورِ سلامت هیچ‌وقت
به‌خاطرِ ما از کار نیفتد.

اشتراکِ داده با پروسهٔ ربات از راهِ همان فایلِ ``links.json`` در پوشهٔ داده است.
"""
from __future__ import annotations

import json
import os
import urllib.parse


def data_dir() -> str:
    return (os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
            or "/data")


def links_dir() -> str:
    d = os.path.join(data_dir(), "filelinks")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def store_path() -> str:
    return os.path.join(links_dir(), "links.json")


def load() -> dict:
    try:
        with open(store_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def resolve(token: str):
    """رکوردِ لینک اگر وجود داشته باشد، فایلش هست و منقضی نشده باشد."""
    import time
    rec = load().get(token)
    if not rec:
        return None
    if rec.get("exp") and time.time() > rec["exp"]:
        return None
    path = rec.get("path") or ""
    return rec if os.path.exists(path) else None


def sweep_files() -> list:
    """فایل‌های منقضی را پاک می‌کند و رکوردهای پاک‌شده را برمی‌گرداند."""
    import time
    now = time.time()
    recs = load()
    expired = []
    for token, rec in list(recs.items()):
        if rec.get("exp") and now > rec["exp"]:
            expired.append(rec)
            recs.pop(token, None)
    if expired:
        try:
            with open(store_path(), "w", encoding="utf-8") as f:
                json.dump(recs, f, ensure_ascii=False)
        except Exception:
            pass
        for rec in expired:
            try:
                os.remove(rec.get("path") or "")
            except Exception:
                pass
    return expired


def register_routes(app) -> bool:
    """روت‌های ``/d/<token>`` (و ``/d/<token>/<name>``) را به یک اپِ aiohttp اضافه می‌کند."""
    try:
        from aiohttp import web
    except Exception:
        return False

    async def _download(request):
        rec = resolve(request.match_info.get("token", ""))
        if not rec:
            return web.Response(status=404, text="لینک منقضی شده یا وجود ندارد.")
        path = rec.get("path") or ""
        if not os.path.exists(path):
            return web.Response(status=404, text="فایل پیدا نشد.")
        resp = web.FileResponse(path)          # پشتیبانی از Range برای دانلود‌منیجر
        resp.headers["Content-Disposition"] = ('attachment; filename="%s"'
                                               % urllib.parse.quote(os.path.basename(path)))
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Accept-Ranges"] = "bytes"
        return resp

    for path in ("/d/{token}", "/d/{token}/{name}"):
        # add_get خودش HEAD را هم ثبت می‌کند (allow_head=True) — ثبتِ دوبارهٔ HEAD را
        # aiohttp با خطا رد می‌کند و آن‌وقت بعضی روت‌ها ثبت نمی‌شوند.
        app.router.add_get(path, _download, allow_head=True)
    return True
