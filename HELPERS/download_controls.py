# -*- coding: utf-8 -*-
"""HELPERS/download_controls.py — دکمه‌های کنترلِ دانلود (❌ لغو · 🔄 ادامه).

چه می‌کند:
  • یک رجیستریِ سبک برای هر کاربر نگه می‌دارد (پیامِ دانلود، زمانِ آخرین پیشرفت،
    تابعِ «دوباره اجرا کن» و حالت).
  • دکمه‌های شیشه‌ای روی **پیامِ دانلود** می‌گذارد: لغو + ادامه.
  • با هر پیشرفت (`touch`) زمانِ آخرین فعالیت به‌روز می‌شود؛ اگر مدتی هیچ پیشرفتی
    نیاید، دانلود «گیرکرده» حساب می‌شود و دکمهٔ 🔄 آن را از همان‌جا ادامه می‌دهد.
  • وقتی دانلودی وسطِ راه قطع/خطا شود، یک پیامِ «🔄 ادامه بده» می‌فرستد و فایلِ
    نیمه‌کارهٔ `.part` را نگه می‌دارد تا ادامه از همان‌جا انجام شود.

متغیرهای محیطی:
  DL_STALL_SECONDS   (پیش‌فرض 180) ثانیه بی‌خبری ⇒ «گیرکرده»
  DL_RESUME_TTL      (پیش‌فرض 1800) چند ثانیه دکمهٔ «ادامه» بعد از خطا معتبر بماند
  DL_RESUME_COOLDOWN (پیش‌فرض 30) فاصلهٔ حداقلی بین دو درخواستِ ادامه
  DL_AUTO_RESUME     (پیش‌فرض false) ادامه‌دادنِ خودکار وقتی دانلود گیر می‌کند
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Dict, Optional

from pyrogram import filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from HELPERS.app_instance import get_app
from HELPERS.logger import logger

CALLBACK_PATTERN = r"^dlctl\|"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    try:
        v = str(os.environ.get(name, "1" if default else "0")).strip().lower()
        return v in ("1", "true", "yes", "on")
    except Exception:
        return default


STALL_SECONDS = _env_int("DL_STALL_SECONDS", 180)
OFFER_TTL = _env_int("DL_RESUME_TTL", 1800)
RESUME_COOLDOWN = _env_int("DL_RESUME_COOLDOWN", 30)
AUTO_RESUME = _env_bool("DL_AUTO_RESUME", False)

BTN_CANCEL = "❌ لغو دانلود"
BTN_RESUME = "🔄 ادامهٔ دانلود"

# متن‌ها دوزبانه‌اند تا به سیستمِ پیام‌های چند‌زبانه گره نخوریم.
TXT_CANCELLED = "🛑 دانلود لغو شد."
TXT_CANCEL_NONE = "دانلودِ فعالی برای لغو نیست."
TXT_RESUME_OFFER = ("⏸ <b>دانلود تمام نشد</b> (قطع شد یا وسطش ایست کرد).\n"
                    "فایلِ نیمه‌کاره نگه داشته شد — با دکمهٔ زیر از همان‌جا ادامه می‌دهم.")
TXT_RESUME_STARTED = "🔄 از همان‌جا ادامه می‌دهم…"
TXT_RESUME_NONE = "چیزی برای ادامه نیست."
TXT_RESUME_BUSY = "دارم ادامه می‌دهم؛ چند لحظه صبر کن."
TXT_RESUME_COOLDOWN = "همین چند لحظه پیش درخواستِ ادامه دادم — کمی صبر کن."
TXT_RESUME_ALIVE = ("دانلود همین الان در جریان است. اگر واقعاً گیر کرده، "
                    "دکمهٔ «🔄 ادامه» را یک بار دیگر بزن تا همین حالا از سر بگیرم.")
TXT_RESUME_FORCED = "باشه، دانلود را از همان‌جا از سر می‌گیرم…"
TXT_RESUME_UPLOAD = "در حالِ آپلود به تلگرام است؛ ادامه لازم نیست."
TXT_NOT_YOURS = "این دکمه مالِ شما نیست."

_LOCK = threading.RLock()
_CTRL: Dict[int, Dict[str, Any]] = {}
# کاربر → زمانِ انقضا؛ تا آن موقع فایل‌های نیمه‌کاره (.part) پاک نمی‌شوند
_KEEP_PARTIAL: Dict[int, float] = {}
_LAST_RESUME: Dict[int, float] = {}
_LAST_PRESS: Dict[int, float] = {}
_WATCHDOG_STARTED = False


# ───────────────────────────── ثبت و وضعیت ─────────────────────────────
def register_controls(user_id: int, kind: str = "video", url: str = "",
                      message_id: Optional[int] = None) -> None:
    """شروعِ یک دانلود: رجیستری را برای این کاربر تنظیم می‌کند."""
    with _LOCK:
        entry = _CTRL.get(user_id) or {}
        entry.update({
            "kind": kind,
            "url": url or entry.get("url", ""),
            "msg_id": message_id or entry.get("msg_id"),
            "chat_id": user_id,
            "active": True,
            "phase": "download",
            "offer": False,
            "restarting": False,
            "started": time.time(),
            "last_progress": time.time(),
        })
        _CTRL[user_id] = entry
    _start_watchdog()


def install_restarter(user_id: int, fn: Callable[[], Any]) -> None:
    """تابعی که وقتی کاربر «🔄 ادامه» را زد، دوباره اجرا می‌شود."""
    with _LOCK:
        entry = _CTRL.get(user_id) or {}
        entry["restarter"] = fn
        _CTRL[user_id] = entry


def touch(user_id: int, phase: Optional[str] = None) -> None:
    """هر بار پیشرفتی در دانلود/آپلود رخ داد صدا زده می‌شود."""
    with _LOCK:
        entry = _CTRL.get(user_id)
        if not entry:
            return
        entry["last_progress"] = time.time()
        if phase:
            entry["phase"] = phase


def seconds_since_progress(user_id: int) -> float:
    with _LOCK:
        entry = _CTRL.get(user_id)
        if not entry:
            return -1.0
        return max(0.0, time.time() - float(entry.get("last_progress") or 0))


def is_stalled(user_id: int, seconds: Optional[int] = None) -> bool:
    """آیا از آخرین پیشرفت بیش از حدِ معمول گذشته؟"""
    with _LOCK:
        entry = _CTRL.get(user_id)
        if not entry or not entry.get("active"):
            return False
        limit = int(seconds if seconds is not None else STALL_SECONDS)
        return (time.time() - float(entry.get("last_progress") or 0)) > limit


def snapshot(user_id: int) -> Optional[Dict[str, Any]]:
    with _LOCK:
        entry = _CTRL.get(user_id)
        return dict(entry) if entry else None


def finish(user_id: int) -> None:
    """پایانِ دانلود: اگر پیشنهادِ «ادامه» داده شده، رجیستری بماند؛ وگرنه پاک شود.

    در پایانِ موفق، دکمه‌های لغو/ادامه از پیامِ دانلود برداشته می‌شوند تا کاربر
    روی پیامِ نهاییِ «ارسال شد» دکمهٔ بی‌مصرف نبیند.
    """
    with _LOCK:
        entry = _CTRL.get(user_id)
        if not entry:
            return
        entry["active"] = False
        entry["phase"] = "done"
        keep_offer = bool(entry.get("offer"))
        msg_id = entry.get("msg_id")
        if not keep_offer:
            _CTRL.pop(user_id, None)
            _KEEP_PARTIAL.pop(user_id, None)
    if keep_offer or not msg_id:
        return
    try:
        from HELPERS.safe_messeger import safe_edit_reply_markup
        safe_edit_reply_markup(chat_id=user_id, message_id=msg_id, reply_markup=None)
    except Exception as exc:
        logger.debug(f"[DLCTL] could not strip keyboard from {msg_id}: {exc}")


def clear_for(user_id: int, msg_id: Optional[int] = None) -> bool:
    """پاک‌کردنِ ثبت، فقط اگر همین دانلود (همان پیام) هنوز جاری باشد.

    جلوی این باگ را می‌گیرد: دانلودِ قدیمیِ لغوشده دیر خطا می‌دهد و ثبتِ
    دانلودِ جدیدِ همان کاربر (ری‌استارت‌شده) را پاک می‌کند.
    """
    with _LOCK:
        entry = _CTRL.get(user_id)
        if entry and msg_id and entry.get("msg_id") and entry.get("msg_id") != msg_id:
            logger.info(f"[DLCTL] skip stale clear for {user_id} "
                        f"(registry msg={entry.get('msg_id')} != {msg_id})")
            return False
    clear(user_id)
    return True


def clear(user_id: int) -> None:
    with _LOCK:
        _CTRL.pop(user_id, None)
        _LAST_RESUME.pop(user_id, None)
        _LAST_PRESS.pop(user_id, None)
        _KEEP_PARTIAL.pop(user_id, None)


def keep_partials(user_id: int) -> bool:
    """آیا فایل‌های نیمه‌کارهٔ این کاربر باید نگه داشته شوند؟ (با انقضا)"""
    with _LOCK:
        exp = _KEEP_PARTIAL.get(user_id)
        return bool(exp and time.time() < exp)


def release_keep_partials(user_id: int) -> None:
    with _LOCK:
        _KEEP_PARTIAL.pop(user_id, None)


def _mark_keep(user_id: int) -> None:
    with _LOCK:
        _KEEP_PARTIAL[user_id] = time.time() + OFFER_TTL


# ───────────────────────────── کیبورد ─────────────────────────────
def keyboard(user_id: int, include_cancel: bool = True, include_resume: bool = True):
    rows = []
    if include_resume:
        rows.append([InlineKeyboardButton(BTN_RESUME, callback_data=f"dlctl|resume|{user_id}")])
    if include_cancel:
        rows.append([InlineKeyboardButton(BTN_CANCEL, callback_data=f"dlctl|cancel|{user_id}")])
    return InlineKeyboardMarkup(rows) if rows else None


def markup_kwargs(user_id: int) -> Dict[str, Any]:
    """kwargs آماده برای safe_send_message/safe_edit_message_text (اگر دانلودی در جریان باشد)."""
    with _LOCK:
        entry = _CTRL.get(user_id)
    if not entry:
        return {}
    # در پایان کار دکمه‌ها را برمی‌داریم تا روی پیامِ نهایی نمانند
    if not entry.get("active") and not entry.get("offer"):
        return {}
    return {"reply_markup": keyboard(user_id)}


# ───────────────────────────── پیشنهادِ «ادامه» ─────────────────────────────
def offer_resume(user_id: int, message=None, restarter: Optional[Callable] = None,
                 url: str = "", kind: str = "video", reason: str = "",
                 msg_id: Optional[int] = None) -> bool:
    """بعد از قطع‌شدنِ دانلود، پیامِ «🔄 ادامه» می‌فرستد و فایلِ نیمه‌کاره را نگه می‌دارد."""
    from HELPERS.safe_messeger import safe_send_message
    try:
        with _LOCK:
            cur = _CTRL.get(user_id)
            if (cur and cur.get("active") and msg_id and cur.get("msg_id")
                    and cur["msg_id"] != msg_id):
                logger.info(f"[DLCTL] skip stale resume offer for {user_id} "
                            f"(registry msg={cur['msg_id']} != {msg_id})")
                return False
        with _LOCK:
            entry = _CTRL.get(user_id) or {}
            entry.update({
                "active": False,
                "offer": True,
                "offer_ts": time.time(),
                "kind": kind,
                "url": url or entry.get("url", ""),
                "restarter": restarter or entry.get("restarter"),
                "reason": reason,
            })
            _CTRL[user_id] = entry
        _mark_keep(user_id)
        text = TXT_RESUME_OFFER
        if reason:
            text += f"\n\n<i>{reason[:200]}</i>"
        safe_send_message(user_id, text, parse_mode=enums.ParseMode.HTML, message=message,
                          reply_markup=keyboard(user_id, include_cancel=True, include_resume=True))
        logger.info(f"[DLCTL] resume offer sent to {user_id} (kind={kind})")
        return True
    except Exception as exc:
        logger.warning(f"[DLCTL] offer_resume failed: {exc}")
        return False


# ───────────────────────────── ادامه‌دادن ─────────────────────────────
def _do_resume(user_id: int) -> None:
    """لغو نسخهٔ گیرکرده + اجرای دوبارهٔ دانلود (از همان‌جا)."""
    from HELPERS.download_status import cancel_user_downloads
    try:
        with _LOCK:
            entry = _CTRL.get(user_id)
            if not entry:
                return
            restarter = entry.get("restarter")
            entry["restarting"] = True
            entry["active"] = False
        _mark_keep(user_id)
        if restarter is None:
            logger.warning(f"[DLCTL] resume requested but no restarter for {user_id}")
            return
        # ۱) به نسخهٔ قبلی بگو بایستد تا فایلِ نیم‌کاره آزاد شود
        try:
            cancel_user_downloads(user_id)
        except Exception:
            pass
        time.sleep(2.0)
        # ۲) دوباره اجرا کن (yt-dlp با --continue روی .part ادامه می‌دهد،
        #    لینکِ مستقیم با هدرِ Range از همان بایت ادامه می‌دهد)
        logger.info(f"[DLCTL] resuming download for {user_id} (kind={entry.get('kind')})")
        restarter()
    except Exception as exc:
        logger.error(f"[DLCTL] resume failed for {user_id}: {exc}")
    finally:
        with _LOCK:
            if user_id in _CTRL:
                _CTRL[user_id]["restarting"] = False


def request_resume(user_id: int, manual: bool = True) -> bool:
    """اگر مجاز باشد، ادامه را در یک ریسهٔ جدا شروع می‌کند."""
    now = time.time()
    with _LOCK:
        last = _LAST_RESUME.get(user_id, 0)
        if manual and (now - last) < RESUME_COOLDOWN:
            return False
        _LAST_RESUME[user_id] = now
    threading.Thread(target=_do_resume, args=(user_id,), name=f"dl-resume-{user_id}",
                     daemon=True).start()
    return True


# ───────────────────────────── نگهبان ─────────────────────────────
def _watchdog_loop() -> None:
    while True:
        time.sleep(15)
        try:
            now = time.time()
            stale = []
            auto = []
            with _LOCK:
                for uid, entry in list(_CTRL.items()):
                    if entry.get("offer") and not entry.get("active"):
                        if (now - float(entry.get("offer_ts") or now)) > OFFER_TTL:
                            stale.append(uid)
                        continue
                    if not entry.get("active"):
                        continue
                    idle = now - float(entry.get("last_progress") or now)
                    if idle > STALL_SECONDS and entry.get("restarter") and not entry.get("restarting"):
                        auto.append(uid)
            for uid in stale:
                clear(uid)
                logger.info(f"[DLCTL] resume offer expired for {uid}")
            if AUTO_RESUME:
                for uid in auto:
                    if request_resume(uid, manual=False):
                        logger.warning(f"[DLCTL] auto-resume for stalled download of {uid}")
        except Exception as exc:
            logger.warning(f"[DLCTL] watchdog error: {exc}")


def _start_watchdog() -> None:
    global _WATCHDOG_STARTED
    if _WATCHDOG_STARTED or not _env_bool("DL_WATCHDOG", True):
        return
    with _LOCK:
        if _WATCHDOG_STARTED:
            return
        _WATCHDOG_STARTED = True
    threading.Thread(target=_watchdog_loop, name="dlctl-watchdog", daemon=True).start()


# ───────────────────────────── هندلرهای دکمه ─────────────────────────────
def _is_admin(user_id: int) -> bool:
    try:
        from CONFIG.config import Config
        return int(user_id) in {int(x) for x in (getattr(Config, "ADMIN", None) or [])}
    except Exception:
        return False


def _dlctl_callback(client, callback_query):
    """هندلرِ دکمه‌های «❌ لغو دانلود» و «🔄 ادامهٔ دانلود»."""
    try:
        parts = (callback_query.data or "").split("|")
        if len(parts) < 3:
            return
        action, uid_raw = parts[1], parts[2]
        try:
            target_uid = int(uid_raw)
        except Exception:
            callback_query.answer("دکمهٔ نامعتبر")
            return
        who = getattr(getattr(callback_query, "from_user", None), "id", None)
        if who is None:
            return
        if int(who) != target_uid and not _is_admin(who):
            callback_query.answer(TXT_NOT_YOURS)
            return

        if action == "cancel":
            from HELPERS.download_status import cancel_user_downloads
            snap = snapshot(target_uid) or {}
            if snap.get("phase") == "upload":
                callback_query.answer(TXT_RESUME_UPLOAD)
                return
            n = cancel_user_downloads(target_uid)
            with _LOCK:
                entry = _CTRL.get(target_uid)
                if entry:
                    entry["active"] = False
                    entry["offer"] = False
            clear(target_uid)
            callback_query.answer(TXT_CANCELLED if n else TXT_CANCEL_NONE)
            try:
                from HELPERS.safe_messeger import safe_edit_message_text
                mid = getattr(getattr(callback_query, "message", None), "id", None)
                if mid:
                    safe_edit_message_text(target_uid, mid, TXT_CANCELLED, reply_markup=None)
            except Exception:
                pass
            logger.info(f"[DLCTL] cancel by {who} for {target_uid} (events={n})")
            return

        if action == "resume":
            snap = snapshot(target_uid)
            if not snap:
                callback_query.answer(TXT_RESUME_NONE)
                return
            if snap.get("restarting"):
                callback_query.answer(TXT_RESUME_BUSY)
                return
            if snap.get("phase") == "upload" and snap.get("active"):
                callback_query.answer(TXT_RESUME_UPLOAD)
                return
            forced = False
            if snap.get("active") and not is_stalled(target_uid) and not snap.get("offer"):
                # دانلود ظاهراً در جریان است. یک بار زدن = راهنما؛ دوباره زدن
                # در فاصلهٔ کوتاه = اصرارِ کاربر ⇒ همین حالا از سر بگیر.
                now = time.time()
                with _LOCK:
                    prev = _LAST_PRESS.get(target_uid, 0)
                    _LAST_PRESS[target_uid] = now
                if (now - prev) > 45:
                    callback_query.answer(TXT_RESUME_ALIVE)
                    return
                forced = True
            if not request_resume(target_uid, manual=True):
                callback_query.answer(TXT_RESUME_COOLDOWN)
                return
            callback_query.answer(TXT_RESUME_FORCED if forced else TXT_RESUME_STARTED)
            logger.info(f"[DLCTL] {'forced ' if forced else ''}resume by {who} for {target_uid}")
            return

        callback_query.answer("")
    except Exception as exc:
        logger.error(f"[DLCTL] callback error: {exc}")
        try:
            callback_query.answer("خطا")
        except Exception:
            pass


def register_handlers() -> bool:
    """ثبتِ هندلرِ callback روی app (باید بعد از set_app صدا زده شود)."""
    app = get_app()
    if app is None:
        # اگر زودتر از ساخته‌شدنِ app ایمپورت شد، از رجیستریِ هندلرها استفاده کن
        try:
            from HELPERS.handler_registry import on_callback_query as _reg
            _reg(filters.regex(CALLBACK_PATTERN))(_dlctl_callback)
            logger.info("[DLCTL] callback registered via handler registry (app not ready)")
        except Exception as exc:
            logger.warning(f"[DLCTL] deferred registration failed: {exc}")
        return False
    try:
        app.on_callback_query(filters.regex(CALLBACK_PATTERN))(_dlctl_callback)
        logger.info("[DLCTL] callback handlers registered (dlctl|cancel, dlctl|resume)")
        return True
    except Exception as exc:
        logger.warning(f"[DLCTL] handler registration failed: {exc}")
        return False


register_handlers()
