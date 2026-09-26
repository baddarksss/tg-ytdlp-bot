# -*- coding: utf-8 -*-
"""مسیریابیِ متن‌های اضافیِ این نسخه (کانال‌ها و متغیرها).

چرا لازم است:
  در این سورس، اولین هندلرِ پیام‌های خصوصی ``URL_PARSERS/url_extractor.py``
  (فیلترِ ``filters.text & filters.private``) است و pyrogram بعد از اولین هندلری
  که فیلترش بگذرد، زنجیره را می‌بندد. پس هر هندلرِ متنیِ تازه‌ای که با
  ``@app.on_message`` ثبت شود، هرگز اجرا نمی‌شود. راهِ درست همان راهی است که
  خودِ پروژه برای ``/settings`` و ``/clean`` رفته: مسیریابی از داخلِ همان
  ``url_distractor``.

  برای همین، در url_extractor فقط یک خط اضافه شده:
      from HELPERS.extra_router import route_extra_text
      if route_extra_text(app, message):
          return

  و این ماژول، متن را به ماژول‌های مربوطه می‌دهد. خروجی True = پیام مصرف شد.
"""
from __future__ import annotations

from HELPERS.logger import logger


def route_extra_text(app, message) -> bool:
    """True یعنی این پیام یکی از دستورها/دکمه‌های ما بود و کارش انجام شد."""
    text = (getattr(message, "text", None) or "").strip()
    if not text:
        return False

    # ۱) کانال‌ها: /channels, /addchannel، دکمهٔ «📢 کانال‌ها» و حالتِ «آیدی را بفرست»
    try:
        from COMMANDS.channels_cmd import handle_channels_text
        if handle_channels_text(app, message):
            return True
    except Exception as e:
        logger.error(f"extra_router: channels routing failed: {e}")

    # ۲) متغیرها: /vars، دکمهٔ «🧩 متغیرها» و جریانِ ویرایش/حذف/افزودن
    try:
        from HELPERS.vars_store import handle_vars_text
        if handle_vars_text(app, message):
            return True
    except Exception as e:
        logger.error(f"extra_router: vars routing failed: {e}")

    return False
