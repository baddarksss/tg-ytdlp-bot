"""
Cookie-file sanity checks shared by every yt-dlp call site.

Why this exists
---------------
yt-dlp does NOT ignore a broken cookiefile - it raises a hard error:

    ERROR: 'users/123/cookie.txt' does not look like a Netscape format
    cookies file

so the whole download dies even on sites that would have worked fine
without cookies. Every call site in this bot only checked
``os.path.exists(cookie)``, so an EMPTY cookie file (0 bytes) - which is
exactly what an aborted cookie upload or a copy of the empty global
``data/cookie.txt`` leaves behind - killed every download with a cryptic
message.

With these helpers a cookiefile is only handed to yt-dlp when it really is
a Netscape cookie file; otherwise it is skipped (download continues without
cookies) and the user gets a clear message instead of a raw yt-dlp error.
"""

import os

# The two headers real cookie exporters write
NETSCAPE_HEADER = "# Netscape HTTP Cookie File"
NETSCAPE_ALT_HEADER = "# HTTP Cookie File"


def is_valid_cookie_file(path) -> bool:
    """True only when *path* is a usable Netscape cookie file.

    Checks (in this order): the file exists, it is a regular file, it is
    not empty, and its content looks like a Netscape cookie file (either
    the header comment or a row with the 7 tab separated fields).
    """
    try:
        if not path:
            return False
        if not os.path.isfile(path):
            return False
        if os.path.getsize(path) < 10:
            return False
        with open(path, "rb") as f:
            head = f.read(8192)
        if not head:
            return False
        text = head.decode("utf-8", "ignore")
        if NETSCAPE_HEADER in text or NETSCAPE_ALT_HEADER in text:
            return True
        # Some exporters drop the header but keep the row format:
        # domain \t flag \t path \t secure \t expiry \t name \t value
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            return len(line.split("\t")) >= 7
        return False
    except Exception:
        return False


def usable_cookie_path(path):
    """Return *path* when it is a valid cookie file, otherwise None.

    Use this everywhere before assigning ``ydl_opts['cookiefile']``.
    """
    return path if is_valid_cookie_file(path) else None


def describe_cookie_problem(path) -> str:
    """A short, human readable reason why *path* cannot be used."""
    try:
        if not path:
            return "no cookie file"
        if not os.path.exists(path):
            return "cookie file not found"
        if os.path.getsize(path) == 0:
            return "cookie file is empty"
        return "cookie file is not in Netscape format"
    except Exception:
        return "cookie file is not usable"
