# -*- coding: utf-8 -*-
"""HELPERS/direct_link.py — دانلودِ «لینکِ مستقیم» (فایلِ خام روی سرور).

سناریو: کاربر لینکی مثل ``https://example.com/movie.mp4`` می‌فرستد (نه صفحهٔ
یوتیوب و نه سایتِ پشتیبانی‌شدهٔ yt-dlp). این ماژول:

  ۱) تشخیص می‌دهد که لینک، مستقیم است (پسوندِ مدیا یا نوعِ محتوا در HEAD).
  ۲) خودش با ``requests`` استریم می‌کند — با **ادامه‌دادن (Range)** اگر قبلاً
     نیمه‌کاره مانده باشد، با نمایشِ درصد/سرعت/زمانِ باقی‌مانده،
  ۳) دکمه‌های **❌ لغو** و **🔄 ادامه** را روی پیامِ دانلود می‌گذارد،
  ۴) فایل را با همان آپلودرِ خودِ ربات به تلگرام می‌فرستد و فایلِ موقت را پاک می‌کند.

متغیرهای محیطی:
  DIRECT_LINK_DISABLE=1        خاموش‌کردنِ کلِ این مسیر
  DIRECT_LINK_EXTS=".pdf,.epub" پسوندهای اضافیِ مجاز (علاوه بر مدیا)
  DIRECT_LINK_PROBE=0          تشخیصِ بدونِ پسوند با HEAD را خاموش می‌کند
  DIRECT_LINK_UA="..."         User-Agent سفارشی (پیش‌فرض: کروم)
"""
from __future__ import annotations

import hashlib
import html
import os
import re
import threading
import time
import urllib.parse
from typing import Any, Dict, Tuple

from HELPERS.logger import logger

# ── پسوندها ────────────────────────────────────────────────────────────────
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".m4v", ".ts",
              ".m2ts", ".mpg", ".mpeg", ".wmv", ".3gp", ".mts", ".ogv", ".vob"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma", ".mka", ".aiff"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS
# این‌ها را عمداً به yt-dlp می‌سپاریم (پلی‌لیستِ HLS/DASH و ادغام لازم دارند)
MANIFEST_EXTS = {".m3u8", ".mpd", ".ism"}
# خانوادهٔ MPEG-TS: تلگرام این کانتینر را به‌عنوان ویدیو پخش نمی‌کند ⇒ بعد از دانلود،
# فقط کانتینر بی‌کم‌وکاست به MP4 تبدیل می‌شود (بدونِ کدگذاریِ دوباره، سریع).
TS_EXTS = {".ts", ".m2ts", ".mts", ".tp", ".trp"}
KNOWN_PAGE_EXTS = {".html", ".htm", ".php", ".asp", ".aspx", ".jsp", ".cgi", ".shtml"}
DEFAULT_EXTRA_EXTS = {".pdf", ".epub", ".mobi", ".srt", ".vtt", ".ass", ".torrent", ".apk"}
OCTET_TYPES = ("application/octet-stream", "binary/octet-stream", "application/mp4",
               "application/x-matroska")
MANIFEST_TYPES = ("application/vnd.apple.mpegurl", "application/x-mpegurl",
                  "audio/mpegurl", "application/dash+xml")
# نوع‌هایی که «صفحهٔ وب/API» هستند ⇒ هرگز لینکِ مستقیم نیستند
WEB_TYPES = ("text/html", "application/xhtml+xml", "text/xml", "application/xml",
             "application/json", "application/ld+json", "application/problem+json",
             "application/rss+xml", "application/atom+xml", "text/plain")
# سندهای باینریِ رایج که مستقیم دانلود می‌شوند
DOC_TYPES = ("application/pdf", "application/epub+zip", "application/x-mobipocket-ebook",
             "application/vnd.amazon.ebook")
MIN_PROBE_BYTES = 256 * 1024          # برای لینکِ بی‌پسوند، فایلِ کوچکِ octet را نگیر
SKIP_HOSTS = {"youtube.com", "youtu.be", "m.youtube.com", "instagram.com", "tiktok.com",
              "twitter.com", "x.com", "facebook.com", "fb.watch", "vimeo.com", "dailymotion.com"}
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _env_bool(name: str, default: bool = False) -> bool:
    v = str(os.environ.get(name, "1" if default else "0")).strip().lower()
    return v in ("1", "true", "yes", "on")


def _extra_exts() -> set:
    raw = os.environ.get("DIRECT_LINK_EXTS", "")
    if raw.strip():
        out = set()
        for item in raw.split(","):
            item = item.strip().lower()
            if not item:
                continue
            out.add(item if item.startswith(".") else "." + item)
        return out
    return set(DEFAULT_EXTRA_EXTS)


# ── ابزارها ────────────────────────────────────────────────────────────────
def _ext(path: str) -> str:
    base = (path or "").rsplit("/", 1)[-1]
    if "." not in base:
        return ""
    return "." + base.rsplit(".", 1)[-1].lower()


def hsize(num: float) -> str:
    try:
        num = float(num)
    except Exception:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def htime(seconds: float) -> str:
    try:
        seconds = max(0, int(seconds))
    except Exception:
        return "--:--"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _clean_name(name: str, fallback: str = "file") -> str:
    name = urllib.parse.unquote(name or "")
    name = os.path.basename(name.strip().replace("\\", "/")) or fallback
    name = re.sub(r"[^\w\s.\-()\[\]ٔ-یآ-یء-ي]", "_", name, flags=re.UNICODE)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name or fallback)[:120]


def _name_from_headers(headers: Dict[str, str], url: str) -> str:
    cd = ""
    for key in ("Content-Disposition", "content-disposition"):
        if headers.get(key):
            cd = headers[key]
            break
    if cd:
        m = re.search(r"filename\*=UTF-8''([^;]+)", cd, flags=re.I)
        if not m:
            m = re.search(r'filename="?([^";]+)"?', cd, flags=re.I)
        if m:
            return _clean_name(m.group(1))
    path = urllib.parse.urlparse(url).path
    base = path.rsplit("/", 1)[-1] or ""
    return _clean_name(base, fallback="download")


def _content_type_ext(ct: str) -> str:
    ct = (ct or "").lower().split(";")[0].strip()
    return {
        "video/mp4": ".mp4", "video/webm": ".webm", "video/x-matroska": ".mkv",
        "video/quicktime": ".mov", "video/mpeg": ".mpg", "video/x-msvideo": ".avi",
        "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/aac": ".aac",
        "audio/flac": ".flac", "audio/wav": ".wav", "audio/ogg": ".ogg",
        "application/pdf": ".pdf", "application/zip": ".zip",
        "video/mp2t": ".ts", "video/mpegts": ".ts", "video/vnd.dlna.mpeg-tts": ".ts",
    }.get(ct, "")


# ── تشخیص ──────────────────────────────────────────────────────────────────
_PROBE_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_PROBE_LOCK = threading.Lock()


def probe(url: str, timeout: int = 8) -> Dict[str, Any]:
    """با HEAD (و در صورتِ نیاز GETِ بسته) نوع/حجمِ محتوا را می‌گیرد. نتیجه کش می‌شود."""
    with _PROBE_LOCK:
        cached = _PROBE_CACHE.get(url)
        if cached and (time.time() - cached[0]) < 600:
            return cached[1]
    info: Dict[str, Any] = {}
    try:
        import requests
        headers = {"User-Agent": os.environ.get("DIRECT_LINK_UA", DEFAULT_UA),
                   "Accept": "*/*"}
        try:
            r = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
            if r.status_code >= 400:
                raise RuntimeError(f"HEAD {r.status_code}")
            info = {"status": r.status_code,
                    "content_type": r.headers.get("Content-Type", ""),
                    "length": int(r.headers.get("Content-Length") or 0),
                    "accept_ranges": "bytes" in (r.headers.get("Accept-Ranges", "") or "").lower(),
                    "final_url": r.url, "headers": dict(r.headers)}
        except Exception:
            r = requests.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=True)
            try:
                info = {"status": r.status_code,
                        "content_type": r.headers.get("Content-Type", ""),
                        "length": int(r.headers.get("Content-Length") or 0),
                        "accept_ranges": "bytes" in (r.headers.get("Accept-Ranges", "") or "").lower(),
                        "final_url": r.url, "headers": dict(r.headers)}
            finally:
                r.close()
    except Exception as exc:
        logger.debug(f"[DIRECT] probe failed for {url}: {exc}")
        info = {}
    if info:  # فقط نتیجهٔ معتبر کش می‌شود؛ خطای موقت شبکه کش نمی‌شود
        with _PROBE_LOCK:
            if len(_PROBE_CACHE) > 200:
                _PROBE_CACHE.clear()
            _PROBE_CACHE[url] = (time.time(), info)
    return info


def is_media_file_url(url: str) -> bool:
    """آیا مسیرِ لینک، پسوندِ فایلِ خامِ مدیا/سند دارد؟ (بدونِ شبکه — برای گاردِ CDN)

    فقط پسوندِ مسیر را نگاه می‌کند: ``.../segment1.ts`` ⇒ True · ``.../master.m3u8`` ⇒ False
    """
    try:
        ext = _ext(urllib.parse.urlparse(url).path or "")
    except Exception:
        return False
    return ext in MEDIA_EXTS or ext in TS_EXTS or ext in _extra_exts()


def _filelink_parts(url: str):
    """(token, name) اگر مسیرِ لینک شبیهِ ‎/d/<token>[/name]‎ باشد، وگرنه None.

    v82: لینکِ عمومی ممکن است زیرِ یک پیشوندِ مسیر باشد (مثلاً ‎/f/d/<token>``
    وقتی وب‌سرور روی یک زیرپوشه‌ی دامنه سرو می‌شود) ⇒ دنبالِ بخشِ «d»
    در هر جای مسیر می‌گردیم، نه فقط اولِ مسیر.
    """
    try:
        parts = [p for p in (urllib.parse.urlparse(url).path or "").split("/") if p]
    except Exception:
        return None
    for i, p in enumerate(parts):
        if p == "d" and i + 1 < len(parts):
            return parts[i + 1], (parts[i + 2] if len(parts) > i + 2 else "")
    return None


def local_mirror_urls(url: str) -> list:
    """آدرس‌های محلی (127.0.0.1) برای همان لینک؛ سرورِ فایل/سلامت روی همین کانتینر است.

    فایده: دانلودِ لینکِ خودِ ربات به دامنهٔ عمومی/کلودفلر و «hairpin» وابسته نمی‌شود.
    """
    got = _filelink_parts(url)
    if not got:
        return []
    token, name = got
    path = "/d/" + token + (("/" + name) if name else "")
    out, seen = [], set()
    for key in ("HEALTH_PORT", "PORT", "LINK_PORT", "HEALTHCHECK_PORT"):
        raw = (os.environ.get(key) or "").strip()
        if raw.isdigit() and int(raw) not in seen:
            seen.add(int(raw))
            out.append(f"http://127.0.0.1:{int(raw)}{path}")
    return out


def self_link_record(url: str):
    """اگر لینک، لینکِ فایلِ خودِ همین ربات باشد رکوردش را برمی‌گرداند (وگرنه None).

    لینک‌های ``/d/<token>`` و ``/d/<token>/<name>`` در ``links.json`` همین ربات ثبت
    می‌شوند؛ پس هم تشخیصِ «لینکِ مستقیم» قطعی است و هم می‌توان فایل را محلی کپی
    کرد (بدونِ رفت‌وبرگشتِ اینترنتی و بدونِ وابستگی به دامنهٔ عمومی).
    """
    try:
        parsed = urllib.parse.urlparse(url)
        parts = [p for p in (parsed.path or "").split("/") if p]
        # v82: the link may live under a path prefix (/f/d/<token>/<name>)
        idx = -1
        for i, p in enumerate(parts):
            if p == "d" and i + 1 < len(parts):
                idx = i
                break
        if idx < 0:
            return None
        token = parts[idx + 1]
        from HELPERS import filelink_routes as _fr
        # allow_expired=True ⇒ لینکِ منقضی هم برای «خودِ ربات» قابلِ استفاده است
        # (لینکِ عمومی همان‌طور ۴۰۴ می‌ماند، ولی فایل تا مهلتِ نگه‌داری روی دیسک است)
        rec = _fr.resolve(token, allow_expired=True)
        if rec and os.path.exists(rec.get("path") or ""):
            return rec
    except Exception as exc:
        logger.debug(f"[DIRECT] self-link check failed: {exc}")
    return None


def self_link_local_path(url: str) -> str:
    """مسیرِ محلیِ فایل اگر لینک مالِ خودِ ربات باشد؛ وگرنه رشتهٔ خالی."""
    rec = self_link_record(url)
    return (rec or {}).get("path") or ""


def is_self_filelink(url: str) -> bool:
    return self_link_record(url) is not None


def is_direct_link(url: str, deep: bool = True) -> Tuple[bool, str]:
    """آیا این لینک، فایلِ خام است؟ (خروجی: (بله/خیر، دلیل))"""
    if _env_bool("DIRECT_LINK_DISABLE", False):
        return False, "disabled"
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False, "bad-url"
    if (parsed.scheme or "").lower() not in ("http", "https"):
        # rtsp/ftp/magnet/… دستِ yt-dlp یا ردِ صریح
        return False, f"scheme:{(parsed.scheme or 'none').lower()}"
    host = (parsed.hostname or "").lower()
    # لینکِ فایلِ خودِ ربات (ساختهٔ همین ربات) همیشه لینکِ مستقیم است
    if is_self_filelink(url):
        return True, "self-link"
    # اگر مسیرش ‎/d/<token>‎ است، از سرورِ محلیِ خودمان بپرس (بدونِ وابستگی به دامنه)
    if _filelink_parts(url):
        for _mirror in local_mirror_urls(url):
            _mi = probe(_mirror, timeout=3)
            if (_mi or {}).get("status") == 200:
                return True, "self-link(local)"
    ext = _ext(parsed.path or "")
    if ext in MANIFEST_EXTS:
        return False, f"manifest:{ext}"
    if ext in MEDIA_EXTS:
        return True, f"ext:{ext}"
    if ext in _extra_exts():
        return True, f"ext:{ext}"
    if ext in KNOWN_PAGE_EXTS:
        return False, f"page:{ext}"
    if not deep or not _env_bool("DIRECT_LINK_PROBE", True):
        return False, "no-ext"
    if any(host == h or host.endswith("." + h) for h in SKIP_HOSTS):
        return False, "known-site"
    info = probe(url)
    ct = (info.get("content_type") or "").lower().split(";")[0].strip()
    length = int(info.get("length") or 0)
    disp_name = _name_from_headers(info.get("headers") or {}, url) if info.get("headers") else ""
    has_disp = any(k.lower() == "content-disposition" for k in (info.get("headers") or {}))
    disp_ext = _ext(disp_name)
    if ct in MANIFEST_TYPES:
        # HLS/DASH بدونِ پسوندِ .m3u8: باز هم مانیفست است ⇒ کارِ yt-dlp
        return False, f"manifest-ct:{ct}"
    if ct.startswith("video/") or ct.startswith("audio/"):
        return True, f"ct:{ct}"
    if has_disp and disp_ext in (MEDIA_EXTS | TS_EXTS | _extra_exts()):
        # سرور خودش گفته «فایلِ ضمیمه با این نام» ⇒ قطعاً لینکِ مستقیم است
        return True, f"attachment:{disp_ext}"
    if has_disp and ct not in WEB_TYPES:
        # Content-Disposition: attachment با هر نوعِ غیرِ وب (zip/pdf/bin/…) ⇒ فایل
        return True, f"attachment:{ct or 'binary'}"
    if ct in DOC_TYPES:
        return True, f"ct:{ct}"
    if ct in OCTET_TYPES and (length >= MIN_PROBE_BYTES or has_disp):
        return True, f"ct:{ct}"
    return False, f"ct:{ct or 'unknown'}"


def _ffmpeg_path() -> str:
    """مسیرِ ffmpeg (اول PATH، بعد هِلپرِ خودِ ربات)."""
    import shutil
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        from DOWN_AND_UP.ffmpeg import get_ffmpeg_path
        return get_ffmpeg_path() or ""
    except Exception:
        return ""


def remux_to_mp4(path: str) -> str:
    """MPEG-TS → MP4 فقط با تغییرِ کانتینر (``-c copy``)؛ خروجیِ خالی = نشد.

    اگر AAC باشد، ``aac_adtstoasc`` هم لازم است؛ در صورتِ خطا بدونِ آن و در نهایت
    با کانتینرِ MKV تلاش می‌شود. فایلِ اصلی دست‌نخورده می‌ماند.
    """
    import subprocess
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        logger.warning("[DIRECT] ffmpeg not found — .ts uploaded as-is")
        return ""
    base = os.path.splitext(path)[0]
    attempts = [
        [base + ".mp4", ["-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart"]],
        [base + ".mp4", ["-c", "copy", "-movflags", "+faststart"]],
        [base + ".mkv", ["-c", "copy"]],
    ]
    tried = set()
    for out, extra in attempts:
        if out in tried:
            continue
        tried.add(out)
        try:
            if os.path.exists(out):
                os.remove(out)
        except Exception:
            pass
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", path] + extra + [out]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3600)
            if proc.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
                logger.info(f"[DIRECT] remuxed {os.path.basename(path)} → {os.path.basename(out)} "
                            f"({hsize(os.path.getsize(out))})")
                return out
            logger.debug(f"[DIRECT] remux attempt failed ({os.path.basename(out)}): "
                         f"{proc.stderr.decode('utf-8', 'ignore')[:200]}")
            try:
                if os.path.exists(out):
                    os.remove(out)  # خروجیِ نیمه‌کاره نماند
            except Exception:
                pass
        except Exception as exc:
            logger.warning(f"[DIRECT] remux error: {exc}")
    return ""


# ── دانلود ─────────────────────────────────────────────────────────────────
class _Cancelled(Exception):
    pass


class _HttpError(Exception):
    """سرور با کدِ ۴xx/۵xx جواب داد (لینکِ مرده/منقضی/بدونِ دسترسی)."""

    def __init__(self, status: int, body: str = ""):
        super().__init__(f"HTTP {status}")
        self.status = int(status)
        self.body = (body or "")[:400]


class _TooBig(Exception):
    pass


def _progress_text(name: str, done: int, total: int, started: float, note: str = "") -> str:
    elapsed = max(0.001, time.time() - started)
    speed = done / elapsed
    percent = (done / total * 100) if total else 0
    blocks = int(percent // 10)
    bar = "🟩" * blocks + "⬜️" * (10 - blocks)
    lines = [f"📥 <b>{html.escape(name)}</b>",
             f"{bar}   {percent:.1f}%",
             f"⬇️ {hsize(done)}" + (f" / {hsize(total)}" if total else ""),
             f"🚀 {hsize(speed)}/s" + (f"   ⏱ {htime((total - done) / speed)}" if total and speed > 0 else "")]
    if note:
        lines.append(note)
    return "\n".join(lines)


def download_direct(app, message, url: str, user_id: int, kind_hint: str = "") -> bool:
    """کارگرِ دانلودِ لینکِ مستقیم (در یک ریسهٔ جدا اجرا می‌شود)."""
    from HELPERS.safe_messeger import safe_send_message, safe_edit_message_text
    from HELPERS.download_status import (set_active_download, can_start_download,
                                         get_download_limit_msg, register_download_cancel_event,
                                         unregister_download_cancel_event, _adaptive_interval)
    from HELPERS.download_controls import (register_controls, install_restarter, keyboard,
                                           touch, finish, clear, offer_resume)
    from CONFIG.limits import LimitsConfig

    if not can_start_download(user_id):
        try:
            safe_send_message(user_id, get_download_limit_msg(user_id), message=message)
        except Exception:
            pass
        return False

    max_bytes = int(getattr(LimitsConfig, "MAX_FILE_SIZE_GB", 2)) * 1024 ** 3
    tg_limit = 2 * 1024 ** 3              # سقفِ ارسالِ فایل توسط ربات در تلگرام
    user_dir = os.path.join("users", str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    logger.info("[DIRECT] start user=%s hint=%r url=%s", user_id, kind_hint, url[:160])

    cancel_ev = register_download_cancel_event(user_id)
    msg_id = None
    target_path = ""
    target_name = ""
    done = False
    set_active_download(user_id, True)
    register_controls(user_id, kind="direct", url=url)
    install_restarter(user_id, lambda: download_direct(app, message, url, user_id, kind_hint))

    try:
        import requests
        headers = {"User-Agent": os.environ.get("DIRECT_LINK_UA", DEFAULT_UA),
                   "Accept": "*/*", "Referer": url}
        # ۱) لینکِ فایلِ خودِ ربات؟ ⇒ فایل همان‌جاست، محلی کپی می‌کنیم
        #    (نامِ فایل روی دیسک «<token>_<name>» است؛ نامِ اصلی از رکورد می‌آید)
        _self_rec = self_link_record(url) if _env_bool("DIRECT_LINK_LOCAL_SELF", True) else None
        local_src = (_self_rec or {}).get("path") or ""
        if local_src and os.path.exists(local_src):
            info = {"content_type": "", "length": os.path.getsize(local_src),
                    "accept_ranges": True, "local": True, "headers": {}}
            name = _clean_name((_self_rec or {}).get("name") or os.path.basename(local_src),
                               fallback="file")
            logger.info(f"[DIRECT] self-link → local copy ({hsize(info['length'])}, '{name}') for {user_id}")
        else:
            local_src = ""
            info = probe(url)
            name = _name_from_headers(info.get("headers") or {}, url)
            if not _ext(name):
                ext = _content_type_ext(info.get("content_type") or "")
                if ext:
                    name += ext
        total_remote = int(info.get("length") or 0)
        logger.info("[DIRECT] probe: status=%s len=%s ctype=%r ranges=%s local=%s name=%r",
                    info.get("status"), total_remote or "-", (info.get("content_type") or "")[:40],
                    info.get("accept_ranges"), bool(info.get("local")), name[:80])
        # ── فضایِ دیسک: با عددِ واقعیِ فایل (نه حدسِ ۲ گیگابایتی) ──
        try:
            from HELPERS.filesystem_hlp import check_disk_space, disk_usage_report, humanbytes
            _du = disk_usage_report(user_dir)
            _need = int(total_remote * 1.15) + (1024 * 1024) if total_remote else (200 * 1024 * 1024)
            if not check_disk_space(user_dir, _need, estimate_known=bool(total_remote), label="direct"):
                clear(user_id)
                set_active_download(user_id, False)
                safe_send_message(
                    user_id,
                    "⛔️ جایِ دیسکِ سرور برای این فایل کافی نیست.\n"
                    "📊 اندازهٔ فایل: <code>%s</code> · آزاد: <code>%s</code> · ظرفیت: <code>%s</code>\n"
                    "👈 کمی صبر کن تا فایل‌های قبلی پاک شوند، یا فایلِ کوچک‌تری بفرست."
                    % (hsize(total_remote) if total_remote else "نامعلوم",
                       humanbytes(_du.get("free", 0)), humanbytes(_du.get("total", 0))),
                    message=message, parse_mode="html")
                logger.warning("[DIRECT] aborted: not enough disk for %s (free=%s)",
                               hsize(total_remote) if total_remote else "?",
                               humanbytes(_du.get("free", 0)))
                return False
        except ImportError:
            pass
        if total_remote and total_remote > max_bytes:
            safe_send_message(user_id,
                              f"❌ حجمِ فایل ({hsize(total_remote)}) از سقفِ مجاز "
                              f"({getattr(LimitsConfig, 'MAX_FILE_SIZE_GB', 2)}GB) بیشتر است.",
                              message=message)
            return False
        if total_remote and total_remote > tg_limit:
            safe_send_message(user_id,
                              f"⚠️ حجمِ فایل {hsize(total_remote)} است؛ تلگرام اجازهٔ ارسالِ بیش از "
                              f"{hsize(tg_limit)} را به ربات نمی‌دهد. تا حدِ امکان تلاش می‌کنم.",
                              message=message)

        # نامِ روی دیسک: پایدار (هشِ لینک) تا «ادامه» روی همان فایل بنشیند.
        # پسوندِ .part لازم است: تمیزکاریِ دوره‌ایِ ربات فایل‌های مدیای کامل را پاک می‌کند
        # ولی .part را نگه می‌دارد؛ در پایانِ دانلود به نامِ نهایی تغییرِ نام می‌دهد.
        digest = hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()[:10]
        target_name = name
        final_path = os.path.join(user_dir, f"dl_{digest}_{name}")
        target_path = final_path + ".part"
        pos = os.path.getsize(target_path) if os.path.exists(target_path) else 0

        proc = safe_send_message(user_id, _progress_text(name, pos, total_remote, time.time(),
                                                        note="شروع دانلودِ لینکِ مستقیم…"),
                                 message=message, parse_mode="html",
                                 reply_markup=keyboard(user_id))
        msg_id = getattr(proc, "id", None)
        register_controls(user_id, kind="direct", url=url, message_id=msg_id)

        started = time.time()
        last_edit = 0.0
        if local_src:
            # کپیِ محلیِ همان فایل (سریع، بدونِ شبکه؛ با پشتیبانیِ لغو و ادامه)
            with open(local_src, "rb") as src, open(target_path, "ab" if pos else "wb") as dst:
                if pos:
                    src.seek(pos)
                written = pos
                while True:
                    if cancel_ev.is_set():
                        raise _Cancelled("لغو توسط کاربر")
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
                    written += len(chunk)
                    touch(user_id, "download")
                    now = time.time()
                    if msg_id and (now - last_edit) >= _adaptive_interval(now - started, user_id):
                        last_edit = now
                        safe_edit_message_text(user_id, msg_id,
                                               _progress_text(name, written, total_remote, started,
                                                              note=("📁 کپیِ محلی (فایلِ خودِ ربات)"
                                                                    + (" · لینک منقضی بود ولی فایل هنوز بود"
                                                                       if (_self_rec or {}).get("expired") else ""))),
                                               parse_mode="html", reply_markup=keyboard(user_id))
                    if written > max_bytes:
                        raise _TooBig(f"حجم از سقفِ {getattr(LimitsConfig, 'MAX_FILE_SIZE_GB', 2)}GB گذشت")
            done = True
        if not done:
            # منابعِ ممکن: سرورِ محلیِ خودِ ربات (اگر لینک ‎/d/...‎ باشد) و بعد آدرسِ اصلی
            _last_http_err = None
            _cands = local_mirror_urls(url) if _env_bool("DIRECT_LINK_LOCAL_MIRROR", True) else []
            if url not in _cands:
                _cands.append(url)
            for _idx, _cand in enumerate(_cands):
                _hdrs = dict(headers)
                _from_local = _cand != url
                try:
                    if cancel_ev.is_set():
                        raise _Cancelled("لغو توسط کاربر")
                    if pos and info.get("accept_ranges") is not False:
                        _hdrs["Range"] = f"bytes={pos}-"
                    r = requests.get(_cand, headers=_hdrs, stream=True, timeout=(20, 90),
                                     allow_redirects=True)
                    if r.status_code == 416:                      # فایل از قبل کامل است
                        r.close()
                        done = True
                        break
                    if r.status_code == 200 and "Range" in _hdrs:
                        pos = 0                                   # سرور Range را پشتیبانی نکرد
                    if r.status_code not in (200, 206):
                        try:
                            _body = (r.text or "")[:400]
                        except Exception:
                            _body = ""
                        r.close()
                        raise _HttpError(r.status_code, _body)
                    if r.status_code == 206 and pos:
                        # اگر سرور از بایتِ درخواستی شروع نکرد، دوباره از صفر بنویس
                        cont = (r.headers.get("Content-Range") or "").strip()
                        try:
                            _start = int(cont.split(" ")[1].split("-")[0]) if " " in cont else pos
                        except Exception:
                            _start = pos
                        if _start != pos:
                            logger.warning(f"[DIRECT] server resumed at {_start} instead of {pos}; restarting file")
                            pos = 0
                    total = int(r.headers.get("Content-Length") or 0) + pos
                    mode = "ab" if pos else "wb"
                    written = pos
                    with open(target_path, mode) as fh:
                        for chunk in r.iter_content(chunk_size=512 * 1024):
                            if cancel_ev.is_set():
                                r.close()
                                raise _Cancelled("لغو توسط کاربر")
                            if not chunk:
                                continue
                            fh.write(chunk)
                            written += len(chunk)
                            touch(user_id, "download")
                            now = time.time()
                            if msg_id and (now - last_edit) >= _adaptive_interval(now - started, user_id):
                                last_edit = now
                                safe_edit_message_text(
                                    user_id, msg_id,
                                    _progress_text(name, written, total, started,
                                                   note=("🔗 از سرورِ محلیِ خودِ ربات" if _from_local else "")),
                                    parse_mode="html", reply_markup=keyboard(user_id))
                            if written > max_bytes:
                                raise _TooBig(f"حجم از سقفِ {getattr(LimitsConfig, 'MAX_FILE_SIZE_GB', 2)}GB گذشت")
                    r.close()
                    done = True
                    break
                except (_Cancelled, _TooBig):
                    raise
                except Exception as _cand_err:
                    if _idx < len(_cands) - 1:
                        logger.warning(f"[DIRECT] source failed ({_cand}): {_cand_err} → trying next source")
                        if isinstance(_cand_err, _HttpError):
                            _last_http_err = _cand_err     # برای پیامِ پایانی
                        continue
                    raise
        if not done and _last_http_err is not None:
            raise _last_http_err
    except _HttpError as exc:
        # لینکِ مرده/منقضی/بدونِ دسترسی: «ادامه» معنی ندارد ⇒ پیامِ روشن و تمام
        clear(user_id)
        _hint = "لینک را چک کن (ممکن است منقضی یا نیازمندِ کوکی/هدر باشد)."
        if _filelink_parts(url) and exc.status in (404, 410):
            _hint = ("این لینکِ فایل منقضی شده یا روی سرورِ دیگری ساخته شده "
                     "(رکوردش روی این سرور نیست).\n"
                     "👈 همان فایل را دوباره برایم بفرست تا لینکِ تازه بسازم.")
        _txt = (f"❌ دانلود نشد — سرور گفت <code>HTTP {exc.status}</code>\n{_hint}")
        logger.info(f"[DIRECT] HTTP {exc.status} for {user_id}: {url[:80]} | body={exc.body[:120]!r}")
        try:
            if msg_id:
                safe_edit_message_text(user_id, msg_id, _txt, parse_mode="html", reply_markup=None)
            else:
                safe_send_message(user_id, _txt, parse_mode="html", message=message)
        except Exception:
            pass
        try:
            if target_path and os.path.exists(target_path) and os.path.getsize(target_path) == 0:
                os.remove(target_path)
        except Exception:
            pass
        return False
    except _Cancelled:
        logger.info(f"[DIRECT] cancelled by user {user_id}: {url[:80]}")
        clear(user_id)
        try:
            if msg_id:
                safe_edit_message_text(user_id, msg_id, "🛑 دانلود لغو شد.", reply_markup=None)
        except Exception:
            pass
        return False
    except _TooBig as exc:
        clear(user_id)
        safe_send_message(user_id, f"❌ {exc}", message=message)
        try:
            if target_path and os.path.exists(target_path):
                os.remove(target_path)
        except Exception:
            pass
        return False
    except Exception as exc:
        logger.error(f"[DIRECT] download failed for {user_id}: {exc}")
        _http_msg = str(exc)
        if _http_msg.startswith("HTTP 4"):
            # لینک اشتباه/منقضی/بدونِ دسترسی: چیزی برای «ادامه» نیست
            clear(user_id)
            # لینکِ ‎/d/<token>‎ که ۴۰۴ می‌دهد ⇒ یا منقضی شده یا روی سرورِ دیگری
            # (یک دیپلویِ دیگرِ همان ربات) ساخته شده و رکوردش اینجا نیست.
            _hint = "لینک را چک کن (ممکن است منقضی یا نیازمندِ کوکی/هدر باشد)."
            if _filelink_parts(url) and _http_msg.startswith("HTTP 404"):
                _hint = ("این لینکِ فایل منقضی شده یا روی سرورِ دیگری ساخته شده "
                         "(رکوردش روی این سرور نیست).\n"
                         "👈 همان فایل را دوباره برایم بفرست تا لینکِ تازه بسازم.")
            try:
                if msg_id:
                    safe_edit_message_text(user_id, msg_id,
                                           f"❌ دانلود نشد — سرور گفت <code>{html.escape(_http_msg)}</code>\n"
                                           f"{_hint}",
                                           parse_mode="html", reply_markup=None)
                else:
                    safe_send_message(user_id,
                                      f"❌ دانلود نشد — سرور گفت <code>{html.escape(_http_msg)}</code>\n{_hint}",
                                      parse_mode="html", message=message)
            except Exception:
                pass
            try:
                if target_path and os.path.exists(target_path) and os.path.getsize(target_path) == 0:
                    os.remove(target_path)
            except Exception:
                pass
            return False
        try:
            _reason = f"خطا: {exc}"
            if _filelink_parts(url):
                # لینکِ ‎/d/...‎ یعنی لینکِ فایلِ یک ربات: معمولاً منقضی‌شده یا فایل پاک‌شده
                _reason = (f"لینکِ خودِ سرور جواب نداد ({exc}) — ممکن است لینک منقضی شده "
                           f"یا فایلش از سرور پاک شده باشد")
            offer_resume(user_id, message,
                         lambda: download_direct(app, message, url, user_id, kind_hint),
                         url=url, kind="direct", reason=_reason, msg_id=msg_id)
            if msg_id:
                safe_edit_message_text(user_id, msg_id,
                                       f"⚠️ دانلود ناتمام ماند (<i>{html.escape(str(exc))[:120]}</i>) — "
                                       f"با دکمهٔ زیر از همان‌جا ادامه بده.",
                                       parse_mode="html", reply_markup=keyboard(user_id))
        except Exception:
            pass
        return False
    finally:
        if not done or not target_path:
            set_active_download(user_id, False)
            try:
                unregister_download_cancel_event(user_id, cancel_ev)
            except Exception:
                pass

    # ── آپلود به تلگرام ────────────────────────────────────────────────────
    try:
        try:
            if os.path.exists(target_path):
                os.replace(target_path, final_path)
            target_path = final_path
        except Exception as _mv_err:
            logger.debug(f"[DIRECT] rename to final name failed: {_mv_err}")

        # ── MPEG-TS: تبدیلِ کانتینر به MP4 تا تلگرام آن را به‌عنوان ویدیو پخش کند ──
        upload_path, upload_name = target_path, target_name
        if _ext(target_name) in TS_EXTS:
            try:
                if msg_id:
                    safe_edit_message_text(user_id, msg_id,
                                           _progress_text(target_name, 0, 0, time.time(),
                                                          note="🔧 تبدیلِ کانتینرِ MPEG-TS به MP4 "
                                                               "(بدونِ افتِ کیفیت)…"),
                                           parse_mode="html", reply_markup=keyboard(user_id))
            except Exception:
                pass
            try:
                from HELPERS.download_controls import touch as _dlctl_touch
                _dlctl_touch(user_id, "remux")
            except Exception:
                pass
            remuxed = remux_to_mp4(target_path)
            if remuxed:
                upload_path = remuxed
                upload_name = os.path.splitext(target_name)[0] + _ext(remuxed)
                logger.info(f"[DIRECT] .ts remuxed for user {user_id}: {os.path.basename(upload_path)}")

        size = os.path.getsize(upload_path)
        try:
            from HELPERS.download_controls import touch as _dlctl_touch
            _dlctl_touch(user_id, "upload")
        except Exception:
            pass
        if msg_id:
            safe_edit_message_text(user_id, msg_id,
                                   _progress_text(upload_name, size, size, started,
                                                  note="⬆️ آپلود به تلگرام…"),
                                   parse_mode="html", reply_markup=keyboard(user_id))
        from HELPERS.download_status import progress_bar
        from HELPERS.upload_guard import timed_upload
        ext = _ext(upload_name)
        caption = f"{upload_name}\n📦 {hsize(size)}"
        # v82: progress_args must NEVER be None - pyrogram does `*progress_args`
        # and None explodes with "Value after * must be an iterable, not
        # NoneType" (that killed every upload without a progress message).
        progress_args = ((user_id, msg_id, f"⬆️ {html.escape(upload_name)}")
                         if msg_id else ())

        def _send_video():
            return app.send_video(user_id, upload_path, caption=caption, file_name=upload_name,
                                  supports_streaming=True, progress=progress_bar,
                                  progress_args=progress_args)

        def _send_audio():
            return app.send_audio(user_id, upload_path, caption=caption, file_name=upload_name,
                                  progress=progress_bar, progress_args=progress_args)

        def _send_doc():
            # v82: this pyrogram build made `force_document` mutually exclusive
            # with `disable_content_type_detection` (which defaults to True)
            # -> the old call raised "Parameters `force_document` and
            # `disable_content_type_detection` are mutually exclusive" and
            # the file was never sent. `disable_content_type_detection` is the
            # supported way to force a document.
            return app.send_document(user_id, upload_path, caption=caption, file_name=upload_name,
                                     disable_content_type_detection=True, progress=progress_bar,
                                     progress_args=progress_args)

        sent = None
        if ext in VIDEO_EXTS or ext in TS_EXTS:
            try:
                sent = timed_upload(_send_video)
            except Exception as exc:
                logger.warning(f"[DIRECT] send_video failed ({exc}); retrying as document")
        elif ext in AUDIO_EXTS:
            try:
                sent = timed_upload(_send_audio)
            except Exception as exc:
                logger.warning(f"[DIRECT] send_audio failed ({exc}); retrying as document")
        if sent is None:
            sent = timed_upload(_send_doc)

        touch(user_id, "done")
        try:
            if msg_id:
                safe_edit_message_text(user_id, msg_id,
                                       f"✅ <b>{html.escape(upload_name)}</b>\n📦 {hsize(size)} — ارسال شد.",
                                       parse_mode="html", reply_markup=None)
        except Exception:
            pass
        for leftover in {target_path, upload_path} - {None, ""}:
            try:
                if os.path.exists(leftover):
                    os.remove(leftover)
            except Exception:
                pass
        finish(user_id)
        logger.info(f"[DIRECT] uploaded {upload_name} ({hsize(size)}) for {user_id}")
        return True
    except Exception as exc:
        logger.error(f"[DIRECT] upload failed for {user_id}: {exc}")
        offer_resume(user_id, message, lambda: download_direct(app, message, url, user_id, kind_hint),
                     url=url, kind="direct", reason=f"آپلود ناتمام: {exc}")
        return False
    finally:
        set_active_download(user_id, False)
        try:
            unregister_download_cancel_event(user_id, cancel_ev)
        except Exception:
            pass


def maybe_handle_direct_link(app, message, url: str, user_id: int) -> bool:
    """اگر لینک مستقیم باشد، دانلود را شروع می‌کند و True برمی‌گرداند."""
    try:
        ok, reason = is_direct_link(url)
        if not ok:
            # سطحِ info (نه debug) تا در لاگِ Railway دیده شود؛ وگرنه بی‌صدا رد می‌شود
            logger.info(f"[DIRECT] not a direct link ({reason}): {url[:120]}")
            return False
        logger.info(f"[DIRECT] handling direct link ({reason}) for {user_id}: {url[:100]}")
        threading.Thread(target=download_direct, args=(app, message, url, user_id, reason),
                         name=f"direct-dl-{user_id}", daemon=True).start()
        return True
    except Exception as exc:
        logger.error(f"[DIRECT] dispatch failed: {exc}")
        return False
