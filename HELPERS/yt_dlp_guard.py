"""
Central yt-dlp guard for the whole bot.

Two problems are fixed here, in ONE place, so every code path (format
listing, video download, audio download, thumbnails, subtitles, ...) is
covered - including code that is added later.

1) Broken cookie files
   yt-dlp treats a malformed cookiefile as a FATAL error:

       ERROR: 'users/123/cookie.txt' does not look like a Netscape format
       cookies file

   so one empty cookie file killed every download (even for sites that work
   fine without cookies). Every call site only checked os.path.exists().

2) Eporner
   Eporner shows an "Age verification" interstitial to datacenter IPs (the
   /video-<hash>/... page), so yt-dlp cannot find the video hash:

       ERROR: [Eporner] xxx: Unable to extract hash

   The /embed/<hash>/ page of the same video is NOT age-gated and extracts
   fine. The URL is therefore normalized to the embed form before yt-dlp
   sees it.
"""

import yt_dlp

from HELPERS.cookie_helper import usable_cookie_path, describe_cookie_problem

_installed = False


def _install():
    global _installed
    if _installed:
        return
    _installed = True

    original_init = yt_dlp.YoutubeDL.__init__
    original_extract_info = yt_dlp.YoutubeDL.extract_info

    def patched_init(self, params=None, *args, **kwargs):
        try:
            if params and params.get('cookiefile'):
                cookie = params.get('cookiefile')
                usable = usable_cookie_path(cookie)
                if not usable:
                    # copy the dict: the caller may still mutate its own copy
                    params = dict(params)
                    params['cookiefile'] = None
                    try:
                        from HELPERS.logger import logger
                        logger.warning(
                            f"[yt-dlp guard] ignoring unusable cookie file "
                            f"({describe_cookie_problem(cookie)}): {cookie}"
                        )
                    except Exception:
                        pass
        except Exception:
            pass
        return original_init(self, params, *args, **kwargs)

    def patched_extract_info(self, url, *args, **kwargs):
        original_url = url
        try:
            from URL_PARSERS.normalizer import normalize_eporner_url
            normalized = normalize_eporner_url(url)
            if normalized and normalized != url:
                url = normalized
        except Exception:
            pass
        info = original_extract_info(self, url, *args, **kwargs)
        # the embed page of eporner has no og:title -> yt-dlp reports
        # "Untitled"; the readable title is in the slug of the original URL
        try:
            if isinstance(info, dict) and (not info.get('title') or info.get('title') == 'Untitled'):
                import re as _re
                _m = _re.search(r"/video-([A-Za-z0-9_-]+)/([^/?#]+)", original_url or "")
                if _m:
                    slug = _m.group(2).replace('-', ' ').replace('_', ' ').strip()
                    if slug:
                        info['title'] = slug
        except Exception:
            pass
        return info

    yt_dlp.YoutubeDL.__init__ = patched_init
    yt_dlp.YoutubeDL.extract_info = patched_extract_info


_install()
