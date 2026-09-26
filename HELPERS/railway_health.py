# -*- coding: utf-8 -*-
"""
HELPERS/railway_health.py — وب‌سرور سلامت (اختیاری، برای Railway / مانیتورینگ)

هر وقت Railway متغیر PORT بدهد (یعنی دامنه/پورت عمومی ساخته شده باشد) یا خودت
HEALTH_PORT را ست کنی، این سرور کوچک بالا می‌آید و این آدرس‌ها را جواب می‌دهد:

    GET /            اطلاعات کوتاه
    GET /health      سلامت (برای Healthcheck خودِ Railway)
    GET /healthz     مثل /health
    GET /ready       آمادگی (اگر پروسهٔ ربات مرده باشد 503 می‌دهد)

هیچ وابستگی سنگینی ندارد و روی همان پروسهٔ جداگانه اجرا می‌شود؛ اگر خراب شود
ربات هیچ آسیبی نمی‌بیند.
"""
import json
import os
import shutil
import time

try:
    from aiohttp import web
except Exception as exc:  # pragma: no cover - aiohttp همیشه در requirements هست
    raise SystemExit("aiohttp لازم است: %s" % exc)

START_TS = time.time()
DATA_DIR = os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or ""


def _read_pid():
    candidates = [
        os.path.join(DATA_DIR, "bot.pid") if DATA_DIR else "",
        "/tmp/bot.pid",
    ]
    for path in candidates:
        if not path:
            continue
        try:
            with open(path, "r") as handle:
                return int(handle.read().strip())
        except Exception:
            continue
    return 0


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return os.path.exists("/proc/%d" % pid)


def _is_mount(path):
    """آیا روی این مسیر واقعاً یک Volume مانت شده است؟"""
    if not path:
        return False
    try:
        with open("/proc/mounts", "r") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == path:
                    return True
    except Exception:
        pass
    return False


def _disk():
    path = DATA_DIR if (DATA_DIR and os.path.isdir(DATA_DIR)) else "/app"
    try:
        usage = shutil.disk_usage(path)
        return {
            "path": path,
            "totalGB": round(usage.total / (1024 ** 3), 2),
            "freeGB": round(usage.free / (1024 ** 3), 2),
        }
    except Exception:
        return {}


def _payload():
    pid = _read_pid()
    alive = _pid_alive(pid)
    return {
        "status": "ok" if alive else "degraded",
        "service": "tg-ytdlp-bot",
        "botAlive": alive,
        "botPid": pid or None,
        "uptimeSec": int(time.time() - START_TS),
        "dataDir": DATA_DIR or "(ephemeral)",
        "volume": _is_mount(os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or DATA_DIR),
        "disk": _disk(),
        "tz": os.environ.get("TZ", "UTC"),
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


async def _json(request):
    return web.json_response(_payload())


async def _ready(request):
    payload = _payload()
    strict = os.environ.get("HEALTH_STRICT", "").lower() in ("1", "true", "yes", "on")
    code = 200
    if strict and not payload["botAlive"]:
        code = 503
    return web.json_response(payload, status=code)


async def _root(request):
    payload = _payload()
    lines = [
        "tg-ytdlp-bot",
        "status   : %s" % payload["status"],
        "botAlive : %s" % payload["botAlive"],
        "uptime   : %ss" % payload["uptimeSec"],
        "dataDir  : %s" % payload["dataDir"],
        "health   : /health  (json)   ready: /ready",
    ]
    return web.Response(text="\n".join(lines) + "\n", content_type="text/plain")


def build_app():
    app = web.Application()
    app.router.add_get("/", _root)
    app.router.add_get("/health", _json)
    app.router.add_get("/healthz", _json)
    app.router.add_get("/ready", _ready)
    # فایل → لینکِ مستقیم: روت‌های /d/<token>/<name> (ماژولِ سبک، بدونِ وابستگی به ربات)
    try:
        from HELPERS.filelink_routes import register_routes
        register_routes(app)
    except Exception as exc:            # هرگز سرورِ سلامت را نباید از کار بیندازد
        print("[filelink] routes not registered: %s" % exc)
    return app


def main():
    port = 0
    for key in ("HEALTH_PORT", "PORT"):
        raw = (os.environ.get(key) or "").strip()
        if raw.isdigit():
            port = int(raw)
            break
    if not port:
        print("ℹ️  HEALTH_PORT/PORT ست نشده — سرور سلامت خاموش است (ربات بدون پورت هم کار می‌کند).")
        return
    print("🩺 سلامت روی 0.0.0.0:%d — /health و /ready" % port)
    web.run_app(build_app(), host="0.0.0.0", port=port, access_log=None, print=None)


if __name__ == "__main__":
    main()
